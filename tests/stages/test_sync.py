from pathlib import Path

import pytest

from studio.core.transcript import Segment, Transcript, Word
from studio.stages.sync import choose_sync_mode, fit_alignment, text_anchors


def _transcript(times: list[float], words: list[str]) -> Transcript:
    segments = [
        Segment(time, time + 0.2, (Word(word, time, time + 0.2),))
        for time, word in zip(times, words, strict=True)
    ]
    return Transcript(Path("audio.wav"), "en", max(times) + 1, segments)


def test_text_anchor_fit_recovers_offset_and_drift() -> None:
    recorder = _transcript([0, 10, 20, 30], ["alpha", "beta", "gamma", "delta"])
    camera = _transcript(
        [2 + 1.001 * value for value in [0, 10, 20, 30]],
        ["alpha", "beta", "gamma", "delta"],
    )
    anchors = text_anchors(camera, recorder)
    offset, k, residual = fit_alignment(anchors)
    assert offset == pytest.approx(2)
    assert k == pytest.approx(1.001)
    assert residual < 1e-9


def test_repeated_words_are_not_used_as_ambiguous_anchors() -> None:
    recorder = _transcript([0, 1, 2], ["same", "same", "unique"])
    camera = _transcript([3, 4, 5], ["same", "same", "unique"])
    assert [anchor.token for anchor in text_anchors(camera, recorder)] == ["unique"]


def test_auto_mode_uses_accumulated_drift_threshold() -> None:
    assert choose_sync_mode("auto", 10, 1.0005, 0.005) == "simple"
    assert choose_sync_mode("auto", 600, 1.0005, 0.005) == "complex"
    assert choose_sync_mode("camera", 600, 1.2, 1.0) == "camera"
