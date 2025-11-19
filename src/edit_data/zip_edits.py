"""
Does the things from `edits.py` but in memory with a Zipfile
"""

from typing import Optional

import zipfile

import json
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

from edit_data.types import *

ZIP_CHANGES_NAME = "changes.zip"
CHANGES_NAME = ".changes"
CONCRETE_NAME = "concrete-history"
EDITS_NAME = "edits-history"


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


def load_zipfile_contents(zip_path: Path) -> dict[Path, str]:
    """
    Load a zipfile from the given path
    """
    if not zip_path.exists():
        raise FileNotFoundError(f"Zip file path {zip_path} does not exist")
    with zipfile.ZipFile(zip_path, "r") as zipped_file:
        file_contents: dict[Path, str] = {}
        for info in zipped_file.infolist():
            if info.is_dir():
                continue
            with zipped_file.open(info.filename) as file:
                content = file.read().decode("utf-8")
                file_contents[Path(info.filename)] = content
    return file_contents


def is_num(s: str) -> bool:
    try:
        int(s)
        return True
    except ValueError:
        return False


def get_important_paths(file_dict: dict[Path, str]) -> set[Path]:
    important_paths: set[Path] = set()
    for path in file_dict.keys():
        if 3 < len(path.parts):
            continue
        if path.parts[-2] != EDITS_NAME and path.parts[-2] != CONCRETE_NAME:
            continue
        if not is_num(path.parts[-1]):
            continue
        important_paths.add(path.parent.parent)
    return important_paths


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
    last_checkpoint: Optional[ConcreteCheckpoint] = None
    concrete_checkpoints: dict[datetime, ConcreteCheckpoint] = {}
    for raw_checkpoint in raw_checkpoints:
        match raw_checkpoint:
            case NewConcreteCheckpoint():
                concrete_checkpoints[raw_checkpoint.mtime] = raw_checkpoint
                last_checkpoint = raw_checkpoint
            case RawSameConcreteCheckpoint(prevMtime, mtime):
                assert prevMtime in concrete_checkpoints
                same_checkpoint = SameConcreteCheckpoint(
                    concrete_checkpoints[prevMtime], mtime
                )
                concrete_checkpoints[mtime] = same_checkpoint
                last_checkpoint = same_checkpoint
    assert last_checkpoint is not None

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

    # Create edits with concrete checkpoints
    edits: list[Edit] = []
    for raw_edit in raw_edits:
        base_change = concrete_checkpoints[raw_edit.baseTime]
        edit = Edit(raw_edit.file, raw_edit.time, base_change, raw_edit.changes)
        edits.append(edit)

    edits.sort(key=lambda x: x.time)
    return FileChangeHistory(file, edits, last_checkpoint)


def load_workspace_history(changes_zip_loc: Path) -> dict[Path, FileChangeHistory]:
    """
    Load e.g. changes.zip into a workspace history mapping
    """
    zip_contents = load_zipfile_contents(changes_zip_loc)
    important_paths = get_important_paths(zip_contents)
    print(f"Found {important_paths} important paths.")
    file_tree = build_file_tree(list(zip_contents.keys()))
    print(file_tree)
    workspace_history: dict[Path, FileChangeHistory] = {}
    for file_path in important_paths:
        file_history = load_file_history(file_path, zip_contents, file_tree)
        workspace_history[file_path] = file_history
    return workspace_history


def get_last_new_concrete_checkpoint(
    checkpoint: ConcreteCheckpoint,
) -> NewConcreteCheckpoint:
    match checkpoint:
        case NewConcreteCheckpoint():
            return checkpoint
        case SameConcreteCheckpoint(prev, _):
            return get_last_new_concrete_checkpoint(prev)


def apply_change(base: str, change: ContentChange) -> str:
    return (
        base[: change.rangeOffset]
        + change.text
        + base[(change.rangeOffset + change.rangeLength) :]
    )


def apply_edit(base: str, edit: Edit) -> str:
    curbase = base
    for change in edit.changes:
        curbase = apply_change(curbase, change)
    return curbase


def get_file_contents(base: str, edits: list[Edit]) -> str:
    cur_base = base
    for edit in edits:
        cur_base = apply_edit(cur_base, edit)
    return cur_base


def get_version_at_time(
    file: Path, workspace_history: dict[Path, FileChangeHistory], time: datetime
) -> str:
    assert file in workspace_history, f"Have no history for {file}."
    file_history = workspace_history[file]

    # Find the edit that is closest to the time
    edit_sequence: list[Edit] = []
    for edit in file_history.edits_history:
        if edit.time > time:
            break
        edit_sequence.append(edit)

    if 0 == len(edit_sequence):
        return get_last_new_concrete_checkpoint(file_history.last_checkpoint).contents

    # Get the chain of edits and checkpoint up to the last edit
    last_edit = edit_sequence[-1]
    reversed_edits = edit_sequence[::-1]
    last_edit_chain: list[Edit] = []
    for edit in reversed_edits:
        if edit.base_change != last_edit.base_change:
            break
        last_edit_chain.append(edit)
    last_edit_chain.reverse()

    return get_file_contents(
        get_last_new_concrete_checkpoint(last_edit.base_change).contents,
        last_edit_chain,
    )


def get_version_at_edit(
    file: Path, workspace_history: dict[Path, FileChangeHistory], edit_idx: int
) -> dict[Path, str]:
    assert file in workspace_history, f"Have no history for {file}."
    file_history = workspace_history[file]
    assert edit_idx < len(
        file_history.edits_history
    ), f"Edit index {edit_idx} out of range."

    target_edit = file_history.edits_history[edit_idx]
    versions: dict[Path, str] = {}
    for f in workspace_history:
        versions[f] = get_version_at_time(f, workspace_history, target_edit.time)
    return versions


def total_num_edits(workspace_history: dict[Path, FileChangeHistory]) -> int:
    return sum(len(fh.edits_history) for fh in workspace_history.values())


if __name__ == "__main__":
    wsh = load_workspace_history(Path("tests/changes.zip"))
    print(wsh.keys())
    version = get_version_at_edit(Path("Expressions.lean"), wsh, 10)
    print(version[Path("Expressions.lean")])
