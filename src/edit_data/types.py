from typing import Any, Optional, Literal

import os
import re
import json
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass

from pydantic import BaseModel, model_validator

from edit_data.common import *


def datetime_from_milis(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000)


def datetime_to_milis(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class Position(BaseModel):
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


class Range(BaseModel):
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


class NewConcreteCheckpoint(BaseModel):
    contents: str
    mtime: datetime

    def to_ts_dict(self) -> dict[str, Any]:
        return {
            "type": "new",
            "contents": self.contents,
            "mtime": datetime_to_milis(self.mtime),
        }

    @classmethod
    def from_ts_dict(cls, obj: Any) -> "NewConcreteCheckpoint":
        return cls(
            contents=obj["contents"], mtime=datetime_from_milis(int(obj["mtime"]))
        )


class SameConcreteCheckpoint(BaseModel):
    prev: "ConcreteCheckpoint"
    mtime: datetime

    def to_ts_dict(self) -> dict[str, Any]:
        return {
            "type": "same",
            "prevMtime": datetime_to_milis(self.prev.mtime),
            "mtime": datetime_to_milis(self.mtime),
        }


ConcreteCheckpoint = NewConcreteCheckpoint | SameConcreteCheckpoint


class ContentChange(BaseModel):
    range: Range
    text: str
    rangeOffset: int
    rangeLength: int

    def to_ts_dict(self) -> dict[str, Any]:
        return {
            "range": self.range.params,
            "text": self.text,
            "rangeOffset": self.rangeOffset,
            "rangeLength": self.rangeLength,
        }

    @classmethod
    def from_ts_dict(cls, obj: Any):
        return cls(
            range=Range.from_response(obj["range"]),
            text=obj["text"],
            rangeOffset=obj["rangeOffset"],
            rangeLength=obj["rangeLength"],
        )


class Edit(BaseModel):
    file: str
    time: datetime
    base_change: ConcreteCheckpoint
    changes: list[ContentChange]

    def to_ts_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "time": datetime_to_milis(self.time),
            "baseTime": datetime_to_milis(self.base_change.mtime),
            "changes": [c.to_ts_dict() for c in self.changes],
        }


@dataclass(frozen=True)
class RawEdit:
    file: str
    time: datetime
    changes: list[ContentChange]

    @classmethod
    def from_ts_dict(cls, obj: Any):
        return cls(
            obj["file"],
            datetime_from_milis(int(obj["time"])),
            [ContentChange.from_ts_dict(c) for c in obj["changes"]],
        )


@dataclass(frozen=True)
class FileChangeHistory:
    path: Path
    edits_history: list[Edit]
    last_checkpoint: ConcreteCheckpoint

    def __gather_concrete_checkpoints(self) -> dict[datetime, ConcreteCheckpoint]:
        concrete_checkpoints: dict[datetime, ConcreteCheckpoint] = {}
        for edit in self.edits_history:
            if edit.base_change.mtime not in concrete_checkpoints:
                concrete_checkpoints[edit.base_change.mtime] = edit.base_change
        return concrete_checkpoints

    def write_concrete_checkpoints(self, changes_path: Path) -> None:
        checkpoint_path = changes_path / self.path / CONCRETE_NAME
        concrete_checkpoints = self.__gather_concrete_checkpoints()

        checkpoint_path.mkdir(parents=True, exist_ok=True)
        for mtime, checkpoint in concrete_checkpoints.items():
            mtime_milis = datetime_to_milis(mtime)
            checkpoint_file = checkpoint_path / f"{mtime_milis}"
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(checkpoint.to_ts_dict(), f, indent=2)

    def write_edits(self, changes_path: Path) -> None:
        edits_path = changes_path / self.path / EDITS_NAME
        edits_path.mkdir(parents=True, exist_ok=True)
        for edit in self.edits_history:
            mtime_milis = datetime_to_milis(edit.time)
            edit_file = edits_path / f"{mtime_milis}"
            with open(edit_file, "w", encoding="utf-8") as f:
                json.dump(edit.to_ts_dict(), f, indent=2)

    def write_ts_file_history(self, changes_path: Path) -> None:
        self.write_concrete_checkpoints(changes_path)
        self.write_edits(changes_path)


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


class LocalChangeMetadata(BaseModel):
    type: Literal["local"] = "local"
    hostname: str
    os_username: str
    workspace_name: str

    def to_ts_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "hostname": self.hostname,
            "osUsername": self.os_username,
            "workspaceName": self.workspace_name,
        }

    @classmethod
    def from_ts_dict(cls, obj: Any) -> "LocalChangeMetadata":
        return cls(
            hostname=obj["hostname"],
            os_username=obj["osUsername"],
            workspace_name=obj["workspaceName"],
        )


class Remote(BaseModel):
    name: str
    fetch_url: Optional[str]
    push_url: Optional[str]

    def to_ts_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fetchUrl": self.fetch_url,
            "pushUrl": self.push_url,
        }

    @classmethod
    def from_ts_dict(cls, obj: Any) -> "Remote":
        return cls(
            name=obj["name"],
            fetch_url=obj.get("fetchUrl"),
            push_url=obj.get("pushUrl"),
        )


class GitChangeMetadata(BaseModel):
    type: Literal["git"] = "git"
    hostname: str
    os_username: str
    workspace_name: str
    head: str
    last_tag: Optional[str]
    remotes: list[Remote]

    def to_ts_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "hostname": self.hostname,
            "osUsername": self.os_username,
            "workspaceName": self.workspace_name,
            "head": self.head,
            "lastTag": self.last_tag,
            "remotes": [r.to_ts_dict() for r in self.remotes],
        }

    @classmethod
    def from_ts_dict(cls, obj: Any) -> "GitChangeMetadata":
        return cls(
            hostname=obj["hostname"],
            os_username=obj["osUsername"],
            workspace_name=obj["workspaceName"],
            head=obj["head"],
            last_tag=obj.get("lastTag"),
            remotes=[Remote.from_ts_dict(r) for r in obj["remotes"]],
        )


ChangeMetadata = GitChangeMetadata | LocalChangeMetadata


def metadata_from_ts_dict(obj: Any) -> ChangeMetadata:
    attempted_type = obj["type"]
    match attempted_type:
        case "git":
            return GitChangeMetadata.from_ts_dict(obj)
        case "local":
            return LocalChangeMetadata.from_ts_dict(obj)
        case _:
            raise ValueError(f"Unknown metadata type {attempted_type}")


def write_ts_metadata(metadata: ChangeMetadata, changes_path: Path) -> None:
    metadata_file = changes_path / "metadata.json"
    changes_path.mkdir(parents=True, exist_ok=True)
    with open(metadata_file, "w", encoding="utf-8") as f:
        metadata_dict = metadata.to_ts_dict()
        f.write(json.dumps(metadata_dict, indent=2))


class WorkspaceChangeHistory(BaseModel):
    metadata: ChangeMetadata
    files: list[FileChangeHistory]

    @model_validator(mode="after")
    def files_must_be_sorted(self) -> "WorkspaceChangeHistory":
        assert all(
            self.files[i].path < self.files[i + 1].path
            for i in range(len(self.files) - 1)
        ), "Files in WorkspaceChangeHistory must be sorted by path"
        return self

    def get_dict(self) -> dict[Path, FileChangeHistory]:
        return {f.path: f for f in self.files}

    def write_ts_workspace_history(self, changes_path: Path) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_dir_path = Path(tmp_dir)
            write_ts_metadata(self.metadata, tmp_dir_path)
            for f in self.files:
                f.write_ts_file_history(tmp_dir_path)

            with zipfile.ZipFile(changes_path, "w") as zipped_file:
                for folder_path, _, filenames in os.walk(tmp_dir_path):
                    for filename in filenames:
                        file_path = Path(folder_path) / filename
                        arc_name = file_path.relative_to(tmp_dir_path)
                        zipped_file.write(file_path, arc_name)

            assert changes_path.exists(), f"Failed to create {changes_path}"
