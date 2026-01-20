import cProfile
import pytest
from pathlib import Path


from edit_data.types import *
from edit_data.edits import (
    get_version_at_edit,
)
from edit_data.fake_it import get_linear_workspace_history

from tests.common import TEST_PROJECT_1


def check_all_prefixes(
    workspace_history: dict[Path, FileChangeHistory],
    file_relpath: Path,
    file_contents: str,
):
    """
    For a linear file history, every prefix of a string should be reproduced
    by the file history.

    TODO: Maybe `get_version_at_edit` could be faster
    """
    for i in range(len(file_contents)):
        workspace_version = get_version_at_edit(file_relpath, workspace_history, i)
        file_version = workspace_version[file_relpath]
        assert file_version == file_contents[: i + 1]


def test_get_linear_workspace_history():
    workspace_history = get_linear_workspace_history(TEST_PROJECT_1)
    file1_relpath = Path("file1.txt")
    file2_relpath = Path("folder1/file2.txt")

    file1_contents = (TEST_PROJECT_1 / file1_relpath).read_text()
    file2_contents = (TEST_PROJECT_1 / file2_relpath).read_text()

    workspace_history_dict = workspace_history.get_dict()

    assert file1_relpath in workspace_history_dict
    assert file2_relpath in workspace_history_dict

    file1_history = workspace_history_dict[file1_relpath]

    assert len(file1_history.edits_history) == len(
        file1_contents
    ), "File1 edit history length mismatch."

    file2_history = workspace_history_dict[file2_relpath]
    assert len(file2_history.edits_history) == len(
        file2_contents
    ), "File2 edit history length mismatch."

    check_all_prefixes(workspace_history_dict, file1_relpath, file1_contents)
    check_all_prefixes(workspace_history_dict, file2_relpath, file2_contents)


if __name__ == "__main__":
    cProfile.run("test_get_linear_workspace_history()")
