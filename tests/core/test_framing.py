from pathlib import Path

import pytest
import yaml

from studio.core.framing import save_framing
from studio.core.settings import SettingsError, load_settings
from studio.core.workspace import ProjectLockedError, project_lock


def test_framing_preserves_other_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text("sync:\n  mode: camera\nreels:\n  enabled: true\n")
    save_framing(tmp_path, "crop", 720, 1280, 0.8)
    settings = load_settings([path])
    assert settings.sync.mode == "camera"
    assert settings.reels.enabled
    assert settings.reels.crop_x == 0.8
    original = path.read_bytes()
    with pytest.raises(SettingsError):
        save_framing(tmp_path, "crop", 721, 1280, 0.8)
    assert path.read_bytes() == original
    with project_lock(tmp_path), pytest.raises(ProjectLockedError):
        save_framing(tmp_path, "fit", 720, 1280, 0.5)
    assert yaml.safe_load(path.read_text())["reels"]["framing"] == "crop"
