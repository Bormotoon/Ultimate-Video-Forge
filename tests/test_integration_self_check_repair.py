"""Integration test for the self-check repair path: re-align a flagged span
and splice a fresh render of it into an already-assembled voice monolith.

Like test_integration_render.py, this exercises real ffmpeg (synthetic
sources, not decoded speech) and is skipped automatically without it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from whispersync.config import WhisperSyncConfig
from whispersync.engine.media import probe
from whispersync.engine.pipeline import _repair_span
from whispersync.engine.self_check import SelfCheckSpan
from whispersync.models import AlignmentMap, Word

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"),
]


def _make_tone_wav(path: Path, duration: float, freq: int = 440) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:duration={duration}",
        "-ar",
        "48000",
        "-ac",
        "1",
        "-acodec",
        "pcm_s24le",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture
def recorder_and_monolith(tmp_path: Path) -> tuple[Path, Path, list[Word], list[Word]]:
    """A 60s recorder track and a monolith that is IDENTICAL to it (so any
    repaired stretch, cut from the same recorder at the right offset, must
    reproduce the source's audio characteristics — format/length invariants
    are what this test actually checks, not perceptual content)."""
    recorder = tmp_path / "recorder.wav"
    _make_tone_wav(recorder, 60.0)
    monolith = tmp_path / "monolith.wav"
    shutil.copy(recorder, monolith)

    # Word lists following t_cam = offset + k * t_rec with offset=0, k=1 —
    # the clip's local time equals the recorder's own time exactly.
    cam_words: list[Word] = []
    rec_words: list[Word] = []
    t = 0.0
    for i in range(200):
        cam_words.append(Word(text=f"w{i}", start=t, end=t + 0.2, probability=0.95))
        rec_words.append(Word(text=f"w{i}", start=t, end=t + 0.2, probability=0.95))
        t += 0.28
    return recorder, monolith, cam_words, rec_words


def test_repair_span_splices_a_same_length_monolith(
    recorder_and_monolith: tuple[Path, Path, list[Word], list[Word]], tmp_path: Path
) -> None:
    recorder, monolith, cam_words, rec_words = recorder_and_monolith
    monolith_duration = probe(monolith).duration

    job = SimpleNamespace(
        am=AlignmentMap(anchors=[], offset=0.0, k=1.0, residual_ms=0.0),
        cam_audio=None,
        rec_path=recorder,
        rec_duration=60.0,
        channels=1,
        codec="pcm_s24le",
    )
    span = SelfCheckSpan(start=20.0, end=24.0, kind="shifted", detail="test")
    cfg = WhisperSyncConfig(min_anchors=8, anchor_min_confidence=0.6)
    tmp_dir = tmp_path / "repair_tmp"
    tmp_dir.mkdir()

    repaired = _repair_span(
        span, job, monolith, monolith_duration, cam_words, rec_words, cfg, 48000, tmp_dir
    )

    assert repaired is not None
    assert repaired.exists()
    info = probe(repaired)
    assert abs(info.duration - monolith_duration) < 0.05
    assert info.audio_channels == 1


def test_repair_span_returns_none_when_span_out_of_range(
    recorder_and_monolith: tuple[Path, Path, list[Word], list[Word]], tmp_path: Path
) -> None:
    recorder, monolith, cam_words, rec_words = recorder_and_monolith
    monolith_duration = probe(monolith).duration

    job = SimpleNamespace(
        am=AlignmentMap(anchors=[], offset=0.0, k=1.0, residual_ms=0.0),
        cam_audio=None,
        rec_path=recorder,
        rec_duration=60.0,
        channels=1,
        codec="pcm_s24le",
    )
    # A span at/after the monolith's own end has nothing to repair.
    span = SelfCheckSpan(
        start=monolith_duration + 5.0, end=monolith_duration + 9.0, kind="shifted", detail="test"
    )
    cfg = WhisperSyncConfig()
    tmp_dir = tmp_path / "repair_tmp2"
    tmp_dir.mkdir()

    repaired = _repair_span(
        span, job, monolith, monolith_duration, cam_words, rec_words, cfg, 48000, tmp_dir
    )
    assert repaired is None
