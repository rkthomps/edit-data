"""
Does the things from `edits.py` but in memory with a Zipfile
"""

from typing import Optional

import os
import tempfile
import zipfile

import json
from pydantic import TypeAdapter

from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

from edit_data.types import *
from edit_data.common import *


@dataclass
class FileNode:
    part: str
    children: dict[str, "FileTree"]

    def iterdir(self) -> list[str]:
        return list(self.children.keys())

    def find(self, path: Path) -> "FileTree | None":
        if len(path.parts) == 0:
            return self
        first_part = path.parts[0]
        if first_part in self.children:
            child = self.children[first_part]
            if isinstance(child, FileNode):
                return child.find(Path(*path.parts[1:]))
            else:
                return child
        else:
            return None

    def get_dir(self, path: Path) -> "FileNode":
        node = self.find(path)
        if node is None or not is_dir(node):
            raise FileNotFoundError(f"Directory {path} not found")
        return node  # type: ignore

    def put(self, path: Path, full_path: Path):
        if len(path.parts) == 0:
            return
        first_part = path.parts[0]
        if first_part not in self.children:
            if len(path.parts) == 1:
                self.children[first_part] = full_path
                return
            else:
                assert len(path.parts) > 1
                self.children[first_part] = FileNode(first_part, {})
        child = self.children[first_part]
        if isinstance(child, FileNode):
            child.put(Path(*path.parts[1:]), full_path)


FileTree = FileNode | Path


def is_dir(node: FileTree) -> bool:
    return isinstance(node, FileNode)


def build_file_tree(paths: list[Path]) -> FileNode:
    root = FileNode(".", {})
    for path in paths:
        root.put(path, path)
    return root


def is_important_path(path: Path) -> bool:
    if path == Path(METADATA_NAME):
        return True
    if len(path.parts) < 3:
        return False
    if path.parts[-2] != EDITS_NAME and path.parts[-2] != CONCRETE_NAME:
        return False
    if not is_num(path.parts[-1]):
        return False
    return True


def get_file_real_path(important_path: Path) -> Optional[Path]:
    """
    A path like "a/b/edits-history/12345" maps to "a/b"
    metadata.json returns None
    """
    if important_path == Path(METADATA_NAME):
        return None
    assert is_important_path(important_path)
    return Path(*important_path.parts[:-2])


def get_real_paths(important_paths: list[Path]) -> set[Path]:
    real_paths: set[Path] = set()
    for p in important_paths:
        real_path = get_file_real_path(p)
        if real_path is not None:
            real_paths.add(real_path)
    return real_paths


def load_zipfile_contents_from_file(f: zipfile.ZipFile) -> dict[Path, str]:
    file_contents: dict[Path, str] = {}
    for info in f.infolist():
        if info.is_dir():
            continue
        if not is_important_path(Path(info.filename)):
            continue
        with f.open(info.filename) as file:
            content = file.read().decode("utf-8")
            file_contents[Path(info.filename)] = content
    return file_contents


def load_zipfile_contents_from_path(zip_path: Path) -> dict[Path, str]:
    """
    Load a zipfile from the given path
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"Zip file path {zip_path} does not exist")
    with zipfile.ZipFile(zip_path, "r") as zipped_file:
        return load_zipfile_contents_from_file(zipped_file)


def is_num(s: str) -> bool:
    try:
        int(s)
        return True
    except ValueError:
        return False


def is_sorted(xs: list[float]) -> bool:
    return all(xs[i] <= xs[i + 1] for i in range(len(xs) - 1))


def raw_edits_to_edits(
    raw_edits: list[RawEdit],
    concrete_checkpoints: list[ConcreteCheckpoint],
) -> list[Edit]:
    """
    Produces a list of edits.
    Raw edits are sorted by time.
    Concrete checkpoints are sorted by mtime.
    """
    assert is_sorted([e.time.timestamp() for e in raw_edits])
    assert is_sorted([c.mtime.timestamp() for c in concrete_checkpoints])

    checkpoint_ptr = 0

    edits: list[Edit] = []
    for raw_edit in raw_edits:
        while checkpoint_ptr < len(concrete_checkpoints):
            checkpoint = concrete_checkpoints[checkpoint_ptr]
            if raw_edit.time < checkpoint.mtime:
                break
            checkpoint_ptr += 1
        assert checkpoint_ptr > 0  # We always get a concrete checkpoint before the edit
        base_change = concrete_checkpoints[checkpoint_ptr - 1]
        assert base_change.mtime <= raw_edit.time
        edit = Edit(
            file=raw_edit.file,
            time=raw_edit.time,
            base_change=base_change,
            changes=raw_edit.changes,
        )
        edits.append(edit)

    assert is_sorted([e.time.timestamp() for e in edits])
    return edits


def load_file_history(
    file: Path, file_dict: dict[Path, str], file_tree: FileNode
) -> FileChangeHistory:
    file_node = file_tree.get_dir(file)
    concrete_node = file_node.get_dir(Path(CONCRETE_NAME))
    checkpoint_paths = concrete_node.children.values()

    raw_checkpoints: list[RawConcreteCheckpoint] = []
    for f in checkpoint_paths:
        assert isinstance(f, Path)
        data = json.loads(file_dict[f])
        intermediate_checkpoint = raw_concrete_checkpoint_from_json(data)
        raw_checkpoints.append(intermediate_checkpoint)
    raw_checkpoints.sort(key=lambda x: x.mtime)

    # Establish pointers in memory
    concrete_checkpoints: list[ConcreteCheckpoint] = []
    last_checkpoint: Optional[ConcreteCheckpoint] = None
    for raw_checkpoint in raw_checkpoints:
        match raw_checkpoint:
            case NewConcreteCheckpoint():
                concrete_checkpoints.append(raw_checkpoint)
                last_checkpoint = raw_checkpoint
            case RawSameConcreteCheckpoint(prevMtime, mtime):
                assert prevMtime in concrete_checkpoints
                same_checkpoint = SameConcreteCheckpoint(
                    prev=concrete_checkpoints[prevMtime], mtime=mtime
                )
                last_checkpoint = same_checkpoint
                concrete_checkpoints.append(same_checkpoint)

    assert last_checkpoint is not None

    # Sort checkpoints by time
    concrete_checkpoints.sort(key=lambda x: x.mtime)

    # Load Edits
    if EDITS_NAME not in file_node.children:
        return FileChangeHistory(file, [], last_checkpoint)
    edits_node = file_node.get_dir(Path(EDITS_NAME))

    raw_edits: list[RawEdit] = []
    for f in edits_node.children.values():
        assert isinstance(f, Path)
        data = json.loads(file_dict[f])
        raw_edit = RawEdit.from_ts_dict(data)
        raw_edits.append(raw_edit)

    # Sort edits by time
    raw_edits.sort(key=lambda x: x.time)

    edits = raw_edits_to_edits(raw_edits, concrete_checkpoints)
    return FileChangeHistory(file, edits, last_checkpoint)


def get_metadata(file_tree: FileNode, file_dict: dict[Path, str]) -> ChangeMetadata:
    if METADATA_NAME not in file_tree.children:
        raise FileNotFoundError("Metadata file not found in zip contents")

    metadata_contents = file_dict[Path(METADATA_NAME)]
    metadata = metadata_from_ts_dict(json.loads(metadata_contents))
    return metadata


def load_workspace_history_from_zip_contents(
    zip_contents: dict[Path, str],
) -> WorkspaceChangeHistory:
    file_tree_paths = get_real_paths(list(zip_contents.keys()))
    file_tree = build_file_tree(list(zip_contents.keys()))
    file_history_dict: dict[Path, FileChangeHistory] = {}
    for file_path in file_tree_paths:
        file_history = load_file_history(file_path, zip_contents, file_tree)
        file_history_dict[file_path] = file_history
    metadata = get_metadata(file_tree, zip_contents)
    sorted_files = sorted(file_history_dict.values(), key=lambda fh: fh.path)
    workspace_history = WorkspaceChangeHistory(metadata=metadata, files=sorted_files)
    return workspace_history


def load_workspace_history(changes_zip_loc: Path) -> WorkspaceChangeHistory:
    """
    Load e.g. changes.zip into a workspace history mapping
    """
    zip_contents = load_zipfile_contents_from_path(changes_zip_loc)
    return load_workspace_history_from_zip_contents(zip_contents)
