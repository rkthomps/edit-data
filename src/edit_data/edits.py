from __future__ import annotations
import zipfile
import json
import os
from typing import Any, Optional
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from dataclasses_json import DataClassJsonMixin


ZIP_CHANGES_NAME = "changes.zip"
CHANGES_NAME = ".changes"
CONCRETE_NAME = "concrete-history"
EDITS_NAME = "edits-history"


def datetime_from_milis(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000)


@dataclass(frozen=True, eq=True)
class Position:
    line: int
    character: int

    @property
    def params(self) -> dict[str, int]:
        return {
            "line": self.line,
            "character": self.character,
        }

    @classmethod
    def from_response(cls, data: Any) -> "Position":
        return cls(
            line=data["line"],
            character=data["character"],
        )


@dataclass(frozen=True)
class Range(DataClassJsonMixin):
    start: Position
    end: Position

    def immediately_before(self, other: "Range") -> bool:
        if self.end.line == other.start.line:
            return self.end.character == other.start.character
        if self.end.line + 1 == other.start.line:
            return other.start.character == 0
        return False

    @property
    def params(self) -> dict[str, Any]:
        return {
            "start": self.start.params,
            "end": self.end.params,
        }

    @classmethod
    def from_response(cls, data: Any) -> "Range":
        return cls(
            start=Position.from_response(data["start"]),
            end=Position.from_response(data["end"]),
        )


@dataclass(frozen=True)
class NewConcreteCheckpoint:
    contents: str
    mtime: datetime

    @classmethod
    def from_ts_dict(cls, obj: Any) -> NewConcreteCheckpoint:
        return cls(obj["contents"], datetime_from_milis(int(obj["mtime"])))


@dataclass(frozen=True)
class SameConcreteCheckpoint:
    prev: ConcreteCheckpoint
    mtime: datetime


ConcreteCheckpoint = NewConcreteCheckpoint | SameConcreteCheckpoint


@dataclass(frozen=True)
class ContentChange:
    range: Range
    text: str
    rangeOffset: int
    rangeLength: int

    @classmethod
    def from_ts_dict(cls, obj: Any):
        return cls(
            Range.from_response(obj["range"]),
            obj["text"],
            obj["rangeOffset"],
            obj["rangeLength"],
        )


@dataclass(frozen=True)
class Edit:
    file: str
    time: datetime
    base_change: ConcreteCheckpoint
    changes: list[ContentChange]


@dataclass(frozen=True)
class RawEdit:
    file: str
    time: datetime
    baseTime: datetime
    changes: list[ContentChange]

    @classmethod
    def from_ts_dict(cls, obj: Any):
        return cls(
            obj["file"],
            datetime_from_milis(int(obj["time"])),
            datetime_from_milis(int(obj["baseTime"])),
            [ContentChange.from_ts_dict(c) for c in obj["changes"]],
        )


@dataclass(frozen=True)
class FileChangeHistory:
    path: Path
    edits_history: list[Edit]
    last_checkpoint: ConcreteCheckpoint


@dataclass(frozen=True)
class RawSameConcreteCheckpoint:
    prevMtime: datetime
    mtime: datetime

    @classmethod
    def from_ts_dict(cls, obj: Any):
        return cls(
            datetime_from_milis(int(obj["prevMtime"])),
            datetime_from_milis(int(obj["mtime"])),
        )


RawConcreteCheckpoint = NewConcreteCheckpoint | RawSameConcreteCheckpoint


def raw_concrete_checkpoint_from_json(json_data: Any) -> RawConcreteCheckpoint:
    attempted_type = json_data["type"]
    match attempted_type:
        case "same":
            return RawSameConcreteCheckpoint.from_ts_dict(json_data)
        case "new":
            return NewConcreteCheckpoint.from_ts_dict(json_data)
        case _:
            raise ValueError(f"Unknown checkpoint type {attempted_type}")


def load_file_history(file: Path, workspace: Path) -> FileChangeHistory:
    msg = f"File {file} does not exist. Did you pass a non-relative path?"
    assert (workspace / CHANGES_NAME / file).exists(), msg
    concrete_loc = workspace / CHANGES_NAME / file / CONCRETE_NAME
    # Load raw concrete checkpoints (with pointers to other files)
    raw_checkpoints: list[RawConcreteCheckpoint] = []
    for f in concrete_loc.iterdir():
        with f.open("r") as fin:
            data = json.load(fin)
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
    edits_loc = workspace / CHANGES_NAME / file / EDITS_NAME
    if not edits_loc.exists():
        return FileChangeHistory(file, [], last_checkpoint)

    raw_edits: list[RawEdit] = []
    for f in edits_loc.iterdir():
        with f.open("r") as fin:
            data = json.load(fin)
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


def is_essential_file(p: Path) -> bool:
    return p.suffix == ".lean" or p.name == "lean-toolchain"


def get_essential_files(workspace: Path) -> list[Path]:
    files: list[Path] = []
    for root, dirs, _ in os.walk(workspace / CHANGES_NAME):
        for d in dirs:
            if is_essential_file(Path(root) / d):
                rel_root = Path(root).relative_to(workspace / CHANGES_NAME)
                files.append(rel_root / d)
    return files


class NoChangesError(Exception):
    pass


def unpack_changes(workspace: Path):
    if (workspace / CHANGES_NAME).exists():
        return
    elif (workspace / ZIP_CHANGES_NAME).exists():
        if os.stat(workspace / ZIP_CHANGES_NAME).st_size == 0:
            raise NoChangesError("Empty changes.zip")
        with zipfile.ZipFile(workspace / ZIP_CHANGES_NAME, "r") as zip_ref:
            zip_ref.extractall(workspace / CHANGES_NAME)
    else:
        raise NoChangesError("No .changes nor changes.zip found")


def load_workspace_history(workspace: Path) -> dict[Path, FileChangeHistory]:
    unpack_changes(workspace)
    workspace_history: dict[Path, FileChangeHistory] = {}
    for file in get_essential_files(workspace):
        history = load_file_history(file, workspace)
        workspace_history[file] = history
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
    workspace_history = load_workspace_history(Path("tests").resolve())
    # file = Path("LeanInduction.lean")
    # version = get_version_at_edit(file, workspace_history, 994)
    # print(version[Path("LeanInduction.lean")])
