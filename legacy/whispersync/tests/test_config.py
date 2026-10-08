"""Tests for config loading (missing file, unknown keys, CLI overrides)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from whispersync.config import ConfigError, WhisperSyncConfig, load_config


def test_load_config_missing_path_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError):
        load_config(missing)


def test_load_config_no_path_uses_defaults() -> None:
    cfg = load_config(None)
    assert cfg == WhisperSyncConfig()


def test_render_master_wav_defaults_off() -> None:
    assert WhisperSyncConfig().render_master_wav is False


def test_render_master_wav_cli_override() -> None:
    cfg = load_config(None, render_master_wav=True)
    assert cfg.render_master_wav is True


def test_load_config_reads_known_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": "medium", "default_strategy": 2}))
    cfg = load_config(path)
    assert cfg.model == "medium"
    assert cfg.default_strategy == 2


def test_load_config_unknown_key_warns_but_does_not_fail(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "config.json"
    # Typo: "pause_duck_dB" instead of "pause_duck_db" — must not silently no-op.
    path.write_text(json.dumps({"model": "medium", "pause_duck_dB": -12.0}))
    with caplog.at_level(logging.WARNING):
        cfg = load_config(path)
    assert cfg.model == "medium"
    assert any("pause_duck_dB" in r.message for r in caplog.records)


def test_load_config_cli_overrides_win(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"model": "medium"}))
    cfg = load_config(path, model="large-v3")
    assert cfg.model == "large-v3"


def test_voice_segment_and_ambience_defaults() -> None:
    cfg = WhisperSyncConfig()
    assert cfg.voice_segment_minutes == 0  # monolith by default
    assert cfg.ambience_track is True  # ambience extraction on by default
    assert cfg.boundary_flex is True
    assert cfg.timebase_source == "camera"


# --- validation -------------------------------------------------------------


def test_default_config_is_valid() -> None:
    WhisperSyncConfig().validate()


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        # A zero bin width is a division by zero later, in estimate_coarse_delta.
        ({"seed_bin_width": 0}, "seed_bin_width"),
        # An unknown enum silently selected a different code path.
        ({"recorder_mode": "everything"}, "recorder_mode"),
        ({"timebase_source": "clock"}, "timebase_source"),
        ({"self_check_mode": "fix"}, "self_check_mode"),
        ({"voice_enhance": "magic"}, "voice_enhance"),
        # JSON's "false" is a TRUE value in Python: the feature stayed on.
        ({"vad_filter": "false"}, "vad_filter"),
        # NaN/Infinity round-trip through JSON and poison every later time sum.
        ({"phrase_gap_threshold": float("nan")}, "phrase_gap_threshold"),
        ({"camera_av_offset_ms": float("inf")}, "camera_av_offset_ms"),
        # Out-of-range numbers.
        ({"anchor_min_confidence": 1.5}, "anchor_min_confidence"),
        ({"min_anchors": 1}, "min_anchors"),
        ({"beam_size": 0}, "beam_size"),
        ({"render_workers": -1}, "render_workers"),
        ({"video_exts": ["mp4"]}, "video_exts"),
        # A deadband larger than the max shift means no correction can apply.
        ({"flex_deadband_s": 1.0, "flex_max_shift_s": 0.1}, "flex_deadband_s"),
    ],
)
def test_validate_rejects_bad_values(kwargs: dict, needle: str) -> None:
    with pytest.raises(ConfigError, match=needle):
        WhisperSyncConfig(**kwargs).validate()


def test_validate_reports_every_problem_at_once(tmp_path: Path) -> None:
    """One message listing everything wrong beats N runs finding one each."""
    with pytest.raises(ConfigError) as excinfo:
        WhisperSyncConfig(recorder_mode="nope", beam_size=0, seed_bin_width=0).validate()
    text = str(excinfo.value)
    assert "recorder_mode" in text
    assert "beam_size" in text
    assert "seed_bin_width" in text


def test_malformed_config_file_is_a_clear_error(tmp_path: Path) -> None:
    """Malformed JSON used to escape as a raw JSONDecodeError traceback."""
    bad = tmp_path / "cfg.json"
    bad.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        WhisperSyncConfig.from_file(bad)


def test_config_file_must_contain_an_object(tmp_path: Path) -> None:
    bad = tmp_path / "cfg.json"
    bad.write_text("[1, 2, 3]")
    with pytest.raises(ConfigError, match="must contain a JSON object"):
        WhisperSyncConfig.from_file(bad)


def test_config_file_with_invalid_utf8_is_a_clear_error(tmp_path: Path) -> None:
    bad = tmp_path / "cfg.json"
    bad.write_bytes(b'{"model": "\xff\xfe"}')
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        WhisperSyncConfig.from_file(bad)


def test_load_config_validates_the_merged_result(tmp_path: Path) -> None:
    """The merged object is what the run uses, so that is what is checked —
    a valid file plus a bad override must still fail."""
    good = tmp_path / "cfg.json"
    good.write_text('{"recorder_mode": "best"}')
    with pytest.raises(ConfigError, match="recorder_mode"):
        load_config(good, recorder_mode="sideways")
