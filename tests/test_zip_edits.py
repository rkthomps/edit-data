import tempfile
import logging
from pathlib import Path

from edit_data.fake_it import get_linear_workspace_history
from edit_data.zip_edits import load_workspace_history

from tests.common import TEST_PROJECT_1


logger = logging.getLogger(__name__)


def test_edit_serialization():
    linear_history = get_linear_workspace_history(TEST_PROJECT_1)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        tmp_zip = tmpdir_path / "changes.zip"
        linear_history.write_ts_workspace_history(tmp_zip)
        reloaded_history = load_workspace_history(tmp_zip)

        logger.info(f"Linear history num files: {len(linear_history.files)}")
        logger.info(f"Reloaded history num files: {len(reloaded_history.files)}")
        assert linear_history.metadata == reloaded_history.metadata
        assert len(linear_history.files) == len(reloaded_history.files)
        assert linear_history == reloaded_history


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_edit_serialization()
