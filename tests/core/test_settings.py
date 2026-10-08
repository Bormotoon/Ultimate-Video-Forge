from pathlib import Path

import pytest

from studio.core.settings import SettingsError, load_settings


def test_layers_and_cli_overrides_merge_by_section(tmp_path: Path) -> None:
    common = tmp_path / "common.yaml"
    project = tmp_path / "project.yaml"
    common.write_text("sync:\n  mode: simple\ntranscribe:\n  batch_size: 8\n")
    project.write_text("sync:\n  max_drift_ms: 12\n")
    settings = load_settings(
        [common, project], ["sync.mode=complex", "roughcut.enabled=false"]
    )
    assert settings.sync.mode == "complex"
    assert settings.sync.max_drift_ms == 12
    assert settings.transcribe.batch_size == 8
    assert not settings.roughcut.enabled


def test_invalid_settings_fail_before_work(tmp_path: Path) -> None:
    cases = [
        ("sync:\n  strategy: 4\n", "strategy"),
        ("roughcut:\n  pause_min_s: .nan\n", "finite"),
        ("unknown:\n  value: true\n", "unknown settings sections"),
        ("transcribe:\n  batch_size: false\n", "batch_size"),
    ]
    path = tmp_path / "settings.yaml"
    for content, message in cases:
        path.write_text(content)
        with pytest.raises(SettingsError, match=message):
            load_settings([path])
