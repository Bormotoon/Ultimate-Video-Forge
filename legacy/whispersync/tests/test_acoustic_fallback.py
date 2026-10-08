"""Tests for the acoustic fallback ("Strategy 0") — pure logic with
monkeypatched track loading/correlation, no ffmpeg. Verified separately
against real ffmpeg-generated synthetic audio during development (see
PROJECT_ANALYSIS.md §10.2); these tests lock the geometry/sign conventions
and the pipeline integration without needing real media files.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from whispersync.config import WhisperSyncConfig
from whispersync.engine import acoustic
from whispersync.engine.pipeline import _try_acoustic_fallback
from whispersync.models import AlignmentMap

SR = 16000


def _noise(seconds: float, seed: int) -> np.ndarray:
    """A band-limited noise track — broadband enough for a sharp GCC-PHAT peak,
    which is what real ambience/speech gives the correlator."""
    rng = np.random.default_rng(seed)
    return rng.standard_normal(int(seconds * SR)).astype(np.float64)


def _patch_tracks(monkeypatch, cam: np.ndarray, rec: np.ndarray) -> None:
    """Feed the scan two REAL signal arrays. Unlike mocking gcc_phat, this
    exercises the actual correlation, so a coverage regression (a stretch of
    the recorder never looked at) shows up as a failure rather than as a
    passing test over a fake."""

    def loader(path, start_s=None, duration_s=None):
        return cam if "cam" in str(path) else rec

    monkeypatch.setattr(acoustic, "load_mono16k_track", loader)


def test_acoustic_coarse_align_recovers_offset_and_k(monkeypatch) -> None:
    # A 20 s clip whose content sits 5 s into a 40 s recorder: the camera's
    # t=0 is the recorder's t=5, i.e. cam = -5 + 1.0 * rec.
    rec = _noise(40.0, seed=1)
    cam = rec[5 * SR : 25 * SR].copy()
    _patch_tracks(monkeypatch, cam, rec)

    result = acoustic.acoustic_coarse_align(
        Path("cam.wav"),
        Path("rec.wav"),
        clip_duration=20.0,
        rec_duration=40.0,
        grid_s=5.0,
        window_s=4.0,
        min_sharpness=20.0,
    )
    assert result is not None
    assert abs(result.offset - (-5.0)) < 0.05
    assert abs(result.k - 1.0) < 1e-6
    assert result.points >= 3


@pytest.mark.parametrize("shift_s", [0.0, 3.0, 10.0, 17.5, 23.0, 47.0])
def test_acoustic_coarse_align_covers_offsets_between_grid_points(
    monkeypatch, shift_s: float
) -> None:
    """The recorder sweep must have no blind spots.

    The old scan stepped the recorder by ``grid_s`` (30 s) while searching only
    ±``max_lag_s`` (1 s) around each probe, so it inspected 2 s out of every 30:
    shifts of 0 s and 30 s were found and a shift of 10 s returned None. Any
    true offset must now be recoverable, wherever it falls between probes.
    """
    rec = _noise(120.0, seed=2)
    start = int(shift_s * SR)
    cam = rec[start : start + 30 * SR].copy()
    _patch_tracks(monkeypatch, cam, rec)

    result = acoustic.acoustic_coarse_align(
        Path("cam.wav"),
        Path("rec.wav"),
        clip_duration=30.0,
        rec_duration=120.0,
        grid_s=10.0,
        window_s=8.0,
        min_sharpness=20.0,
    )
    assert result is not None, f"no match found for shift {shift_s}s"
    assert abs(result.offset - (-shift_s)) < 0.1, f"shift {shift_s}s -> offset {result.offset}"


def test_acoustic_coarse_align_rejects_ambiguous_matches(monkeypatch) -> None:
    """Two identical copies of the material in one recorder is not a confident
    answer. Resolving that by argmax reports one of them with full confidence;
    the scan must decline instead."""
    chunk = _noise(20.0, seed=3)
    filler = _noise(20.0, seed=4)
    # The same 20 s of content appears twice, 40 s apart.
    rec = np.concatenate([chunk, filler, chunk])
    cam = chunk.copy()
    _patch_tracks(monkeypatch, cam, rec)

    result = acoustic.acoustic_coarse_align(
        Path("cam.wav"),
        Path("rec.wav"),
        clip_duration=20.0,
        rec_duration=60.0,
        grid_s=5.0,
        window_s=4.0,
        min_sharpness=20.0,
    )
    assert result is None


def test_acoustic_fit_does_not_invent_a_slope_from_concentrated_evidence() -> None:
    """polyfit through points spanning a few seconds returns nonsense slopes
    (a periodic signal produced k ~= 4.25). Below a usable span, only the
    offset is estimated and k stays exactly 1."""
    points = [(1.0, 6.0), (1.5, 6.4), (2.0, 7.1)]
    fit = acoustic._fit_robust_line(points, max_k_deviation=0.05)
    assert fit is not None
    _offset, k, _n = fit
    assert k == 1.0


def test_acoustic_fit_rejects_implausible_clock_ratio() -> None:
    """A slope of k=2 between two devices recording the same event is not clock
    drift, it is a wrong match; the fit must not export it."""
    points = [(0.0, 0.0), (10.0, 5.0), (20.0, 10.0), (30.0, 15.0), (60.0, 30.0), (120.0, 60.0)]
    fit = acoustic._fit_robust_line(points, max_k_deviation=0.05)
    assert fit is not None
    _offset, k, _n = fit
    assert abs(k - 1.0) < 1e-9


def test_acoustic_coarse_align_no_confident_points_returns_none(monkeypatch) -> None:
    fake_track = np.zeros(50 * 16000)
    monkeypatch.setattr(acoustic, "load_mono16k_track", lambda p: fake_track)
    monkeypatch.setattr(acoustic, "gcc_phat", lambda *a, **k: (0.0, 5.0))  # always below gate

    result = acoustic.acoustic_coarse_align(
        Path("cam.wav"),
        Path("rec.wav"),
        clip_duration=20.0,
        rec_duration=30.0,
        grid_s=5.0,
        window_s=4.0,
        min_sharpness=50.0,
    )
    assert result is None


def test_try_acoustic_fallback_wraps_result_in_alignment_map(monkeypatch) -> None:
    monkeypatch.setattr(
        "whispersync.engine.pipeline.acoustic_coarse_align",
        lambda *a, **k: acoustic.AcousticFit(offset=-5.0, k=1.0, points=6, inliers=6, span_s=300.0),
    )
    cfg = WhisperSyncConfig()
    am = _try_acoustic_fallback(Path("clip.wav"), 20.0, Path("rec.wav"), 40.0, cfg)
    assert isinstance(am, AlignmentMap)
    assert am.anchors == []  # no text breakpoints -> clip_pieces uses one global stretch
    assert am.offset == -5.0
    assert am.k == 1.0
    # The evidence rides along on the map: an acoustic match has no anchors, so
    # anything judging it by len(anchors) would score it zero and discard it.
    assert am.provenance == "acoustic"
    assert am.inliers == 6
    assert am.evidence_span_s == 300.0


def test_acoustic_map_passes_the_alignment_gate(monkeypatch) -> None:
    """A good acoustic map must be ACCEPTED — the whole point of T05.

    Before provenance existed, `len(am.anchors)` was the confidence measure,
    so every successful acoustic fallback (anchors=[]) was rejected and the run
    died with "No camera clip could be aligned to any recorder audio".
    """
    from whispersync.engine.matcher import evaluate_alignment

    cfg = WhisperSyncConfig()
    am = AlignmentMap(
        anchors=[],
        offset=-5.0,
        k=1.0,
        residual_ms=0.0,
        provenance="acoustic",
        inliers=6,
        evidence_span_s=300.0,
    )
    verdict = evaluate_alignment(am, clip_duration=300.0, config=cfg)
    assert verdict.accepted, verdict.reason_text


def test_try_acoustic_fallback_returns_none_when_no_match(monkeypatch) -> None:
    monkeypatch.setattr("whispersync.engine.pipeline.acoustic_coarse_align", lambda *a, **k: None)
    cfg = WhisperSyncConfig()
    am = _try_acoustic_fallback(Path("clip.wav"), 20.0, Path("rec.wav"), 40.0, cfg)
    assert am is None


def test_try_acoustic_fallback_swallows_ffmpeg_errors(monkeypatch) -> None:
    def raise_runtime(*a, **k):
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr("whispersync.engine.pipeline.acoustic_coarse_align", raise_runtime)
    cfg = WhisperSyncConfig()
    am = _try_acoustic_fallback(Path("clip.wav"), 20.0, Path("rec.wav"), 40.0, cfg)
    assert am is None


def test_acoustic_fallback_config_defaults() -> None:
    cfg = WhisperSyncConfig()
    assert cfg.acoustic_fallback is True
    assert cfg.acoustic_fallback_grid_s > 0
    assert cfg.acoustic_fallback_window_s > 0


@pytest.mark.parametrize("min_sharpness", [10.0, 200.0])
def test_acoustic_fallback_min_sharpness_is_configurable(min_sharpness: float) -> None:
    cfg = WhisperSyncConfig(acoustic_fallback_min_sharpness=min_sharpness)
    assert cfg.acoustic_fallback_min_sharpness == min_sharpness
