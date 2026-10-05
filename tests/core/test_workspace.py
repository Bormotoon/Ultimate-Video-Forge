from pathlib import Path

import pytest

from studio.core.workspace import ProjectLockedError, project_lock, published


def test_failed_publication_preserves_previous_result(tmp_path: Path) -> None:
    final = tmp_path / "result"
    final.write_text("old", encoding="utf-8")
    with pytest.raises(RuntimeError, match="failure"):
        with published(final) as temporary:
            temporary.write_text("partial", encoding="utf-8")
            raise RuntimeError("failure")
    assert final.read_text(encoding="utf-8") == "old"


def test_project_lock_rejects_second_owner(tmp_path: Path) -> None:
    with project_lock(tmp_path):
        with pytest.raises(ProjectLockedError):
            with project_lock(tmp_path):
                pass
