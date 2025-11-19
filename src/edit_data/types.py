from typing import Any

from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from dataclasses_json import DataClassJsonMixin


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
    def from_ts_dict(cls, obj: Any) -> "NewConcreteCheckpoint":
        return cls(obj["contents"], datetime_from_milis(int(obj["mtime"])))


@dataclass(frozen=True)
class SameConcreteCheckpoint:
    prev: "ConcreteCheckpoint"
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
