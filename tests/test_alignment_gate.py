"""The alignment acceptance gate: a map must earn its use.

A map existing is not a map being right. Two anchors define a line exactly, so
a near-zero residual over a two-point fit is evidence of nothing — a synthetic
false match reproduced k~=10 with offset~=-1004 s and sub-millisecond residual.
Placement and rendering also used to judge maps SEPARATELY, so a map placement
had rejected as unreliable could still be used to cut the audio.
"""

from __future__ import annotations

import pytest

from whispersync.config import WhisperSyncConfig
from whispersync.engine.matcher import evaluate_alignment
from whispersync.models import AlignmentMap, Anchor


def _text_map(
    *,
    k: float = 1.0,
    offset: float = 0.0,
    inliers: int = 20,
    residual_ms: float = 10.0,
    span_s: float = 300.0,
) -> AlignmentMap:
    return AlignmentMap(
        anchors=[Anchor(cam_time=0.0, rec_time=0.0, token="x", confidence=1.0)] * inliers,
        offset=offset,
        k=k,
        residual_ms=residual_ms,
        provenance="text",
        inliers=inliers,
        evidence_span_s=span_s,
    )


CFG = WhisperSyncConfig()


def test_a_good_map_is_accepted() -> None:
    assert evaluate_alignment(_text_map(), clip_duration=300.0, config=CFG).accepted


def test_no_map_is_rejected() -> None:
    verdict = evaluate_alignment(None, clip_duration=300.0, config=CFG)
    assert not verdict.accepted


def test_two_anchor_fit_with_absurd_clock_ratio_is_rejected() -> None:
    """THE reproduction: two falsely matched words give k=10, offset=-1004 s
    and a residual under a millisecond, because a line through two points fits
    them exactly. Residual alone can never catch this."""
    am = _text_map(k=10.0, offset=-1004.5, inliers=2, residual_ms=0.2, span_s=3.0)
    verdict = evaluate_alignment(am, clip_duration=600.0, config=CFG)
    assert not verdict.accepted
    assert "clock ratio" in verdict.reason_text
    assert "anchor" in verdict.reason_text


def test_implausible_clock_ratio_alone_is_rejected() -> None:
    """Two devices recording one event drift by parts per million. A percent
    is not drift, it is a wrong match."""
    am = _text_map(k=1.5)
    verdict = evaluate_alignment(am, clip_duration=300.0, config=CFG)
    assert not verdict.accepted
    assert "clock ratio" in verdict.reason_text


def test_zero_k_is_rejected() -> None:
    """k=0 makes the inverse map a division by zero downstream."""
    assert not evaluate_alignment(_text_map(k=0.0), clip_duration=300.0, config=CFG).accepted


def test_thin_anchor_count_is_rejected() -> None:
    am = _text_map(inliers=3)
    verdict = evaluate_alignment(am, clip_duration=300.0, config=CFG)
    assert not verdict.accepted
    assert "inlier anchor" in verdict.reason_text


def test_evidence_covering_a_sliver_of_the_clip_is_rejected() -> None:
    """Anchors clustered in the first 3 s of a 10-minute clip make the other
    597 seconds an extrapolation, however tight the residual looks."""
    am = _text_map(span_s=3.0)
    verdict = evaluate_alignment(am, clip_duration=600.0, config=CFG)
    assert not verdict.accepted
    assert "spans" in verdict.reason_text


def test_high_residual_is_rejected() -> None:
    am = _text_map(residual_ms=2000.0)
    verdict = evaluate_alignment(am, clip_duration=300.0, config=CFG)
    assert not verdict.accepted
    assert "residual" in verdict.reason_text


def test_acoustic_map_is_judged_on_its_own_evidence() -> None:
    """An acoustic map has NO anchors by construction. Judging it by
    len(anchors) scored every successful waveform match as zero and threw it
    away — the run then died with "No camera clip could be aligned"."""
    good = AlignmentMap(
        anchors=[],
        offset=-5.0,
        k=1.0,
        residual_ms=0.0,
        provenance="acoustic",
        inliers=6,
        evidence_span_s=300.0,
    )
    assert evaluate_alignment(good, clip_duration=300.0, config=CFG).accepted

    thin = AlignmentMap(
        anchors=[],
        offset=-5.0,
        k=1.0,
        residual_ms=0.0,
        provenance="acoustic",
        inliers=1,
        evidence_span_s=300.0,
    )
    verdict = evaluate_alignment(thin, clip_duration=300.0, config=CFG)
    assert not verdict.accepted
    assert "acoustic point" in verdict.reason_text


@pytest.mark.parametrize("deviation", [0.01, 0.2])
def test_clock_ratio_bound_is_configurable(deviation: float) -> None:
    """The honest limit differs by material: parts-per-million for two
    crystal-clocked recorders, much wider for deliberately speed-changed
    footage. What must not vary is that the check happens."""
    cfg = WhisperSyncConfig(alignment_max_k_deviation=deviation)
    am = _text_map(k=1.1)
    assert evaluate_alignment(am, 300.0, cfg).accepted is (deviation >= 0.1)
