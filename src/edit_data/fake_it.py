"""
For testing purposes.
"""

import os
import socket
from pathlib import Path
from datetime import datetime, timedelta

from edit_data.types import *


def get_local_state() -> LocalChangeMetadata:
    return LocalChangeMetadata(
        hostname=socket.gethostname(),
        os_username=os.getlogin(),
        workspace_name="test-workspace",
    )


def get_linear_file_history(
    file: Path, contents: str, start_time: datetime, delta_milis: int
) -> FileChangeHistory:
    concrete_orig = NewConcreteCheckpoint(contents="", mtime=start_time)
    edits_history: list[Edit] = []
    cur_time = start_time
    cur_line = 0
    cur_col = 0
    for i, ch in enumerate(contents):
        cur_time = cur_time + timedelta(milliseconds=delta_milis)
        ch_range = Range(
            start=Position(line=cur_line, character=cur_col),
            end=Position(line=cur_line, character=cur_col + 1),
        )
        edit = Edit(
            file=str(file),
            time=cur_time,
            base_change=concrete_orig,
            changes=[
                # A simple change that appends one character at the end
                ContentChange(
                    range=ch_range,
                    text=ch,
                    rangeOffset=i,
                    rangeLength=1,
                )
            ],
        )
        if ch == "\n":
            cur_line += 1
            cur_col = 0
        else:
            cur_col += 1
        edits_history.append(edit)
    return FileChangeHistory(
        path=file,
        edits_history=edits_history,
        last_checkpoint=concrete_orig,
    )


def get_linear_workspace_history(root: Path) -> WorkspaceChangeHistory:
    workspace_history: dict[Path, FileChangeHistory] = {}
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            file_path = Path(dirpath) / filename
            with open(file_path, "r", encoding="utf-8") as f:
                contents = f.read()
            file_history = get_linear_file_history(
                file=file_path.relative_to(root),
                contents=contents,
                start_time=datetime(2024, 1, 1, 0, 0, 0),
                delta_milis=100,
            )
            workspace_history[file_history.path] = file_history
    metadata = get_local_state()

    sorted_files = sorted(list(workspace_history.values()), key=lambda fh: fh.path)
    return WorkspaceChangeHistory(metadata=metadata, files=sorted_files)
