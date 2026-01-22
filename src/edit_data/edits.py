from __future__ import annotations
import zipfile
import json
import os
from typing import Any, Optional
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

from pydantic import BaseModel

from edit_data.common import *
from edit_data.types import *


# TODO: Could load changes directly from a directory instead of a zip file.


class NoChangesError(Exception):
    pass


def get_last_new_concrete_checkpoint(
    checkpoint: ConcreteCheckpoint,
) -> NewConcreteCheckpoint:
    match checkpoint:
        case NewConcreteCheckpoint():
            return checkpoint
        case SameConcreteCheckpoint():
            return get_last_new_concrete_checkpoint(checkpoint.prev)


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
