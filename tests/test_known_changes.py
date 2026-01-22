"""
Ensures we can correctly load known changes files emitted from the typescript extension
"""

from pathlib import Path

from edit_data.zip_edits import load_workspace_history

CHANGES_LOC = Path("tests/test_data/test_zips/test-changes.zip")


def test_load_known_changes_zip():
    load_workspace_history(CHANGES_LOC)
