"""Integration tests for the self-check repair path: re-align a flagged span
and splice a fresh render of it into an already-assembled voice monolith.

Like test_integration_render.py, these exercise real ffmpeg (synthetic
sources, not decoded speech) and are skipped automatically without it.

The content checks matter more than the format checks here: an earlier
revision of ``_repair_span`` fed ``_sentence_pieces`` window-relative bounds
while its planning math worked in absolute clip-local coordinates, which
produced a "repaired" window of pure silence — and a duration/channels-only
assertion (the original version of this test) passed anyway. Every test now
asserts the repaired window still contains the expected signal (RMS) and
that it is time-aligned with the original content (GCC-PHAT lag ≈ 0).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from whispersync.config import WhisperSyncConfig
from whispersync.engine.acoustic import gcc_phat, load_mono16k_track
from whispersync.engine.media import probe
from whispersync.engine.pipeline import _repair_span
from whispersync.engine.self_check import SelfCheckSpan
from whispersync.models import AlignmentMap, Word

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"),
]

_SR = 16000


def _make_speechlike_wav(path: Path, duration: float) -> None:
    """A synthetic 'speech-like' track: an amplitude-modulated tone whose
    modulation pattern varies over time, so cross-correlation between a
    window of it and a shifted copy has a single sharp peak (a plain
    constant sine is periodic — every cycle matches every other cycle, and
    GCC-PHAT lag against it is meaningless)."""
    expr = "sin(2*PI*220*t)*(0.5+0.5*sin(2*PI*1.3*t))*(0.5+0.5*sin(2*PI*0.37*t+1))"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"aevalsrc={expr}:s=48000:d={duration}",
        "-ar",
        "48000",
        "-ac",
        "1",
        "-acodec",
        "pcm_s24le",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _window_rms(path: Path, start: float, end: float) -> float:
    sig = load_mono16k_track(path, start_s=start, duration_s=end - start)
    return float(np.sqrt(np.mean(sig**2))) if len(sig) else 0.0


def _window_lag_vs(path_a: Path, path_b: Path, start: float, end: float) -> float:
    """GCC-PHAT lag (seconds) between the same [start, end) window of two
    files — ~0 when the repaired window carries the same content at the same
    time as the reference."""
    a = load_mono16k_track(path_a, start_s=start, duration_s=end - start)
    b = load_mono16k_track(path_b, start_s=start, duration_s=end - start)
    lag, _sharp = gcc_phat(a, b, _SR, max_lag_s=0.5, eps=1e-8)
    return lag


def _word_pair(
    count: int, offset: float, k: float, dt: float = 0.28
) -> tuple[list[Word], list[Word]]:
    """Camera/recorder word lists following ``t_cam = offset + k * t_rec``."""
    cam_words: list[Word] = []
    rec_words: list[Word] = []
    t_rec = 0.0
    for i in range(count):
        t_cam = offset + k * t_rec
        cam_words.append(Word(text=f"w{i}", start=t_cam, end=t_cam + 0.2 * k, probability=0.95))
        rec_words.append(Word(text=f"w{i}", start=t_rec, end=t_rec + 0.2, probability=0.95))
        t_rec += dt
    return cam_words, rec_words


def _job(recorder: Path, offset: float, k: float) -> SimpleNamespace:
    return SimpleNamespace(
        am=AlignmentMap(anchors=[], offset=offset, k=k, residual_ms=0.0),
        cam_audio=None,
        rec_path=recorder,
        rec_duration=probe(recorder).duration,
        channels=1,
        codec="pcm_s24le",
    )


def test_repair_span_preserves_length_and_content_identity_map(tmp_path: Path) -> None:
    """offset=0, k=1: the monolith IS the recorder, so a repaired window must
    contain the same signal at the same time — same length, non-silent, and
    zero lag against the original."""
    recorder = tmp_path / "recorder.wav"
    _make_speechlike_wav(recorder, 60.0)
    monolith = tmp_path / "monolith.wav"
    shutil.copy(recorder, monolith)
    duration = probe(monolith).duration

    cam_words, rec_words = _word_pair(200, offset=0.0, k=1.0)
    span = SelfCheckSpan(start=20.0, end=24.0, kind="shifted", detail="test")
    cfg = WhisperSyncConfig(min_anchors=8, anchor_min_confidence=0.6)
    tmp_dir = tmp_path / "repair_tmp"
    tmp_dir.mkdir()

    repaired = _repair_span(
        span,
        _job(recorder, 0.0, 1.0),
        monolith,
        duration,
        cam_words,
        rec_words,
        cfg,
        48000,
        tmp_dir,
    )

    assert repaired is not None and repaired.exists()
    info = probe(repaired)
    assert abs(info.duration - duration) < 0.05
    assert info.audio_channels == 1
    # The repaired window must carry real signal, not silence...
    original_rms = _window_rms(monolith, 19.0, 25.0)
    repaired_rms = _window_rms(repaired, 19.0, 25.0)
    assert repaired_rms > 0.5 * original_rms
    # ...and that signal must sit at the same time as in the original.
    assert abs(_window_lag_vs(repaired, monolith, 18.0, 26.0)) < 0.03
    # Audio OUTSIDE the repaired window is spliced from the original untouched.
    assert abs(_window_lag_vs(repaired, monolith, 40.0, 50.0)) < 0.005


def test_repair_span_with_nonzero_offset_map(tmp_path: Path) -> None:
    """A clip whose local time 0 sits 10s into the recorder (offset=-10, k=1):
    the window-relative re-planning must still pull content from the RIGHT
    part of the recorder — this is exactly the case the absolute-coordinate
    bug broke."""
    recorder = tmp_path / "recorder.wav"
    _make_speechlike_wav(recorder, 60.0)
    # The clip/monolith = recorder from 10s..55s (t_cam = t_rec - 10).
    monolith = tmp_path / "monolith.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(recorder),
            "-ss",
            "10",
            "-to",
            "55",
            "-acodec",
            "pcm_s24le",
            str(monolith),
        ],
        check=True,
        capture_output=True,
    )
    duration = probe(monolith).duration

    cam_words, rec_words = _word_pair(190, offset=-10.0, k=1.0)
    cam_words = [w for w in cam_words if w.start >= 0.0]
    span = SelfCheckSpan(start=20.0, end=24.0, kind="shifted", detail="test")
    cfg = WhisperSyncConfig(min_anchors=8, anchor_min_confidence=0.6)
    tmp_dir = tmp_path / "repair_tmp"
    tmp_dir.mkdir()

    repaired = _repair_span(
        span,
        _job(recorder, -10.0, 1.0),
        monolith,
        duration,
        cam_words,
        rec_words,
        cfg,
        48000,
        tmp_dir,
    )

    assert repaired is not None and repaired.exists()
    assert abs(probe(repaired).duration - duration) < 0.05
    original_rms = _window_rms(monolith, 19.0, 25.0)
    assert _window_rms(repaired, 19.0, 25.0) > 0.5 * original_rms
    # Content pulled from recorder[~29..35] must land at monolith-local ~19..25.
    assert abs(_window_lag_vs(repaired, monolith, 18.0, 26.0)) < 0.03


def test_repair_span_returns_none_when_span_out_of_range(tmp_path: Path) -> None:
    recorder = tmp_path / "recorder.wav"
    _make_speechlike_wav(recorder, 60.0)
    monolith = tmp_path / "monolith.wav"
    shutil.copy(recorder, monolith)
    duration = probe(monolith).duration

    cam_words, rec_words = _word_pair(200, offset=0.0, k=1.0)
    # A span at/after the monolith's own end has nothing to repair.
    span = SelfCheckSpan(start=duration + 5.0, end=duration + 9.0, kind="shifted", detail="test")
    cfg = WhisperSyncConfig()
    tmp_dir = tmp_path / "repair_tmp"
    tmp_dir.mkdir()

    repaired = _repair_span(
        span,
        _job(recorder, 0.0, 1.0),
        monolith,
        duration,
        cam_words,
        rec_words,
        cfg,
        48000,
        tmp_dir,
    )
    assert repaired is None
