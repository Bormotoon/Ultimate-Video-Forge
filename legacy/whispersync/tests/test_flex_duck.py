"""Tests for Boundary Flex and pause ducking (pure logic, no ffmpeg)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from whispersync.config import WhisperSyncConfig
from whispersync.engine import acoustic
from whispersync.engine.pipeline import pause_spans_local
from whispersync.engine.timestretch import _duck_pause_expr
from whispersync.models import AlignmentMap, Anchor


def _am(cam_times: list[float]) -> AlignmentMap:
    anchors = [Anchor(cam_time=t, rec_time=t + 100.0, token="x", confidence=0.9) for t in cam_times]
    return AlignmentMap(anchors=anchors, offset=100.0, k=1.0, residual_ms=5.0)


# --- pause_spans_local -----------------------------------------------------


def test_pause_spans_detects_interior_gap() -> None:
    # speech at 1,2,3 then a 5s gap, then 8,9
    am = _am([1.0, 2.0, 3.0, 8.0, 9.0])
    spans = pause_spans_local(am, clip_duration=10.0, gap_threshold=0.6, min_pause=0.6)
    # interior gap 3->8 is a pause; head 0->1 and tail 9->10 are < min? head=1.0>=0.6 yes
    assert (3.0, 8.0) in spans
    assert (0.0, 1.0) in spans  # head pause
    # tail 9->10 == 1.0s >= min
    assert (9.0, 10.0) in spans


def test_pause_spans_ignores_short_gaps_and_clamps() -> None:
    am = _am([1.0, 1.3, 1.6, 5.0])  # 0.3s gaps are not pauses; 1.6->5.0 is
    spans = pause_spans_local(am, clip_duration=6.0, gap_threshold=0.6, min_pause=0.6)
    assert (1.6, 5.0) in spans
    # no sub-threshold gaps leaked in
    assert all(b - a >= 0.6 for a, b in spans)
    # all spans within [0, clip_duration]
    assert all(0.0 <= a < b <= 6.0 for a, b in spans)


def test_pause_spans_empty_without_anchors() -> None:
    am = AlignmentMap(anchors=[], offset=0.0, k=1.0, residual_ms=0.0)
    assert pause_spans_local(am, 10.0, 0.6, 0.6) == []


# --- pause_spans_local (word-based: duck only where BOTH tracks are silent) --


def test_pause_spans_word_based_requires_both_silent() -> None:
    # k=1, offset=0 -> recorder time == local time, so this is easy to reason about.
    am = AlignmentMap(anchors=[], offset=0.0, k=1.0, residual_ms=0.0)
    # camera has a real gap [3, 8) with no words (a real pause)
    cam_words = [(0.0, 1.0), (2.0, 3.0), (8.0, 9.0)]
    # recorder has a LOW-CONFIDENCE word at [4, 5) inside that window that never
    # became an anchor — it must NOT be ducked, because the recorder is not
    # actually silent there (PROJECT_ANALYSIS.md §2.5).
    rec_words = [(0.0, 1.0), (2.0, 3.0), (4.0, 5.0), (8.0, 9.0)]
    spans = pause_spans_local(
        am,
        clip_duration=10.0,
        gap_threshold=0.6,
        min_pause=0.6,
        cam_words=cam_words,
        rec_words=rec_words,
        rec_duration=10.0,
    )
    # the [3,8) camera gap is split by the recorder's word at [4,5) into two
    # sub-pauses where BOTH tracks are silent: [3,4) and [5,8).
    assert (3.0, 4.0) in spans
    assert (5.0, 8.0) in spans
    # no span covers the recorder's actual word
    assert not any(a < 4.5 < b for a, b in spans)


def test_pause_spans_word_based_no_overlap_means_no_pause() -> None:
    am = AlignmentMap(anchors=[], offset=0.0, k=1.0, residual_ms=0.0)
    # camera silent [3, 8); recorder has continuous speech through that window
    # AND through the tail, so there is no time where both tracks are silent.
    cam_words = [(0.0, 1.0), (2.0, 3.0), (8.0, 10.0)]
    rec_words = [(0.0, 10.0)]  # one long word/segment spanning the whole clip
    spans = pause_spans_local(
        am,
        clip_duration=10.0,
        gap_threshold=0.6,
        min_pause=0.6,
        cam_words=cam_words,
        rec_words=rec_words,
        rec_duration=10.0,
    )
    assert spans == []


def test_pause_spans_use_the_map_in_the_right_direction() -> None:
    """The recorder->camera projection must be ``offset + k*t``, not its inverse.

    Every earlier test used an identity map (offset 0, k 1), where the correct
    formula and its inverse are indistinguishable — so a fully inverted
    projection passed them all. With offset=-100 a recorder word at 104-105 s
    really lands at camera 4-5 s; the inverse sent it to camera 204-205 s,
    outside the clip, leaving the camera's 3-8 s gap looking silent on both
    tracks. The result: ducking 18 dB straight through real speech.
    """
    am = AlignmentMap(anchors=[], offset=-100.0, k=1.0, residual_ms=0.0)
    cam_words = [(0.0, 1.0), (2.0, 3.0), (8.0, 9.0)]
    # Same words as the identity-map test, shifted into recorder time.
    rec_words = [(100.0, 101.0), (102.0, 103.0), (104.0, 105.0), (108.0, 109.0)]
    spans = pause_spans_local(
        am,
        clip_duration=10.0,
        gap_threshold=0.6,
        min_pause=0.6,
        cam_words=cam_words,
        rec_words=rec_words,
        rec_duration=110.0,
    )
    assert (3.0, 4.0) in spans
    assert (5.0, 8.0) in spans
    # The recorder IS speaking at camera 4-5 s; nothing may duck there.
    assert not any(a < 4.5 < b for a, b in spans)


def test_pause_spans_respect_a_non_unit_clock_ratio() -> None:
    """k != 1 must scale the projection, not be ignored."""
    am = AlignmentMap(anchors=[], offset=0.0, k=2.0, residual_ms=0.0)
    cam_words = [(0.0, 1.0), (2.0, 3.0), (8.0, 9.0)]
    # Recorder time 2.0-2.5 maps to camera 4.0-5.0 under k=2.
    rec_words = [(0.0, 0.5), (1.0, 1.5), (2.0, 2.5), (4.0, 4.5)]
    spans = pause_spans_local(
        am,
        clip_duration=10.0,
        gap_threshold=0.6,
        min_pause=0.6,
        cam_words=cam_words,
        rec_words=rec_words,
        rec_duration=5.0,
    )
    assert not any(a < 4.5 < b for a, b in spans)


# --- _duck_pause_expr ------------------------------------------------------


def test_duck_expr_references_edges_and_level() -> None:
    # one pause [2,4], duck to 0.1 linear, 0.1s fades
    expr = _duck_pause_expr(2.0, 4.0, duck_lin=0.1, fade=0.1)
    assert "0.100000" in expr  # the duck level
    assert expr.count("if(") == 4  # nested 4-way if for one pause
    # edges: a-fade=1.9, a=2.0, b=4.0, b+fade=4.1 appear in the expression
    assert "1.9000" in expr and "4.1000" in expr


# --- refine_piece_boundaries (monkeypatched, no ffmpeg) --------------------


def _output_time(lead: float, pieces, rec_time: float) -> float | None:
    """Where a recorder instant lands on the rendered clip's own timeline.

    This is the only question Boundary Flex exists to change, and the only one
    worth asserting on. The old tests checked that ``rec_start`` had moved —
    which it always had — while the compensating change to the neighbour put
    the audio back exactly where it started. Measuring the realized position
    instead is what makes a no-op correction fail.
    """
    out = lead
    for rs, rd, factor in pieces:
        if rs - 1e-9 <= rec_time <= rs + rd + 1e-9:
            return out + (rec_time - rs) / factor
        out += rd / factor
    return None


def _geometry_problems(lead, pieces, clip_duration, rec_duration):
    from whispersync.engine.pipeline import validate_pieces

    problems = validate_pieces(lead, pieces, clip_duration, rec_duration)
    for (s0, d0, _f0), (s1, _d1, _f1) in zip(pieces, pieces[1:], strict=False):
        if abs((s0 + d0) - s1) > 1e-9:
            problems.append(f"recorder discontinuity at {s1}: previous piece ends at {s0 + d0}")
    return problems


def _flex(monkeypatch, pieces, gcc_results, cfg, clip_duration=15.0, rec_duration=600.0):
    fake_track = np.zeros(700 * 16000)
    monkeypatch.setattr(acoustic, "load_mono16k_track", lambda p: fake_track)
    calls = {"i": 0}

    def fake_gcc(cam, rec, sr, max_lag, eps):
        i = calls["i"]
        calls["i"] += 1
        return gcc_results[i]

    monkeypatch.setattr(acoustic, "gcc_phat", fake_gcc)
    return acoustic.refine_piece_boundaries(
        pieces, 0.0, Path("c"), Path("r"), clip_duration, rec_duration, cfg
    )


def test_refine_actually_moves_the_speech(monkeypatch) -> None:
    """A correction must change WHERE THE AUDIO ENDS UP, not just rec_start.

    The regression this locks: the old scheme moved piece i's onset to s+δ and
    its output start to L+δ/f, which cancel exactly. With three factor-1 pieces
    and a −80 ms correction on the middle one, a recorder event at 107 s stayed
    on camera 7 s — the correction was reported and had no effect.
    """
    cfg = WhisperSyncConfig(boundary_flex=True, flex_min_sharpness=80.0, flex_max_shift_s=0.15)
    pieces = [(100.0, 5.0, 1.0), (105.0, 5.0, 1.0), (110.0, 5.0, 1.0)]
    before = _output_time(0.0, pieces, 107.0)
    assert before is not None and abs(before - 7.0) < 1e-9

    # Every boundary measures the same +0.08 lag, so the correction is uniform
    # (-0.08 on each read position) and the whole clip shifts by +0.08.
    lag = 0.08
    lead, refined = _flex(monkeypatch, pieces, [(lag, 200.0)] * 3, cfg)

    after = _output_time(lead, refined, 107.0)
    assert after is not None
    assert after > before + 0.05, f"speech did not move: {before} -> {after}"
    assert abs(after - (before + lag)) < 5e-3
    assert not _geometry_problems(lead, refined, 15.0, 600.0)


def test_refine_correction_direction_matches_measured_lag(monkeypatch) -> None:
    """A negative lag must move the speech the other way."""
    cfg = WhisperSyncConfig(boundary_flex=True, flex_min_sharpness=80.0, flex_max_shift_s=0.15)
    pieces = [(100.0, 5.0, 1.0), (105.0, 5.0, 1.0), (110.0, 5.0, 1.0)]
    lead, refined = _flex(monkeypatch, pieces, [(-0.08, 200.0)] * 3, cfg)
    before = _output_time(0.0, pieces, 107.0)
    after = _output_time(lead, refined, 107.0)
    assert before is not None and after is not None
    assert abs(after - (before - 0.08)) < 5e-3


def test_refine_preserves_timeline_geometry(monkeypatch) -> None:
    """Corrections must never change the plan's total length or leave a
    recorder gap/overlap: later pieces keep their output positions and no
    content is skipped or played twice."""
    cfg = WhisperSyncConfig(boundary_flex=True, flex_min_sharpness=80.0, flex_max_shift_s=0.15)
    pieces = [(100.0, 5.0, 1.0), (105.0, 5.0, 1.0), (110.0, 5.0, 1.0)]
    lead, refined = _flex(monkeypatch, pieces, [(0.08, 200.0), (-0.06, 200.0), (0.12, 200.0)], cfg)
    assert not _geometry_problems(lead, refined, 15.0, 600.0)
    out_before = sum(d / f for _s, d, f in pieces)
    out_after = sum(d / f for _s, d, f in refined)
    assert abs(out_after - out_before) < 1e-9
    assert lead == 0.0


def test_refine_shifts_only_confident_beyond_deadband(monkeypatch) -> None:
    cfg = WhisperSyncConfig(
        boundary_flex=True,
        flex_window_s=4.0,
        flex_min_sharpness=80.0,
        flex_deadband_s=0.025,
        flex_max_shift_s=0.15,
        acoustic_max_lag_s=1.0,
    )
    pieces = [(100.0, 5.0, 1.0), (105.0, 5.0, 1.0), (110.0, 5.0, 1.0)]
    # piece 0: confident, big lag -> shift; piece 1: confident but tiny lag ->
    # inside the deadband; piece 2: low sharpness -> not trusted.
    lead, refined = _flex(
        monkeypatch, pieces, [(-0.08, 200.0), (-0.005, 200.0), (-0.08, 10.0)], cfg
    )
    assert abs(refined[0][0] - 100.08) < 1e-6
    assert abs(refined[1][0] - 105.0) < 1e-6
    assert abs(refined[2][0] - 110.0) < 1e-6
    assert lead == 0.0
    assert not _geometry_problems(lead, refined, 15.0, 600.0)


def test_refine_declines_a_correction_needing_an_impossible_tempo(monkeypatch) -> None:
    """A shift that would need a tempo outside atempo's range is dropped whole,
    not clamped: a clamped factor silently breaks the source/output length
    relationship the rest of the plan depends on."""
    cfg = WhisperSyncConfig(boundary_flex=True, flex_min_sharpness=80.0, flex_max_shift_s=0.15)
    # A very short piece 1 (0.2 s): a 0.15 s boundary move would need a factor
    # far outside [0.5, 2.0].
    pieces = [(100.0, 5.0, 1.0), (105.0, 0.2, 1.0), (105.2, 5.0, 1.0)]
    lead, refined = _flex(monkeypatch, pieces, [(0.0, 10.0), (0.0, 10.0), (-0.15, 300.0)], cfg)
    assert not _geometry_problems(lead, refined, 15.0, 600.0)


def test_refine_clamps_to_max_shift(monkeypatch) -> None:
    cfg = WhisperSyncConfig(boundary_flex=True, flex_max_shift_s=0.15, flex_min_sharpness=80.0)
    pieces = [(100.0, 5.0, 1.0)]
    fake_track = np.zeros(700 * 16000)
    monkeypatch.setattr(acoustic, "load_mono16k_track", lambda p: fake_track)
    monkeypatch.setattr(acoustic, "gcc_phat", lambda *a, **k: (-1.0, 300.0))  # huge lag
    _lead, refined = acoustic.refine_piece_boundaries(
        pieces, 0.0, Path("c"), Path("r"), 15.0, 600.0, cfg
    )
    # clamped to +0.15, not +1.0
    assert abs(refined[0][0] - 100.15) < 1e-6
