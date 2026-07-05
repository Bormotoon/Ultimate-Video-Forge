"""Tests for the post-render self-check word diagnostics (pure logic, no media)."""

from __future__ import annotations

from whispersync.engine.self_check import diagnose_words
from whispersync.models import Word


def _words(text: str, start: float, dt: float = 0.3, offset: float = 0.0) -> list[Word]:
    """Build word spans from a phrase, one word every ``dt`` seconds from ``start``,
    each shifted by ``offset`` seconds (simulating a mistimed render)."""
    out = []
    t = start
    for tok in text.split():
        out.append(Word(text=tok, start=t + offset, end=t + offset + dt * 0.7, probability=0.95))
        t += dt
    return out


def test_perfectly_matched_transcripts_produce_no_spans() -> None:
    render = _words("привет мир как дела сегодня", 0.0)
    cam = _words("привет мир как дела сегодня", 0.0, offset=0.02)  # sub-threshold jitter
    assert diagnose_words(render, cam) == []


def test_sustained_shift_is_flagged() -> None:
    render = _words("один два три четыре пять шесть", 0.0)
    cam = _words("один два три четыре пять шесть", 0.0, offset=0.5)
    spans = diagnose_words(render, cam)
    assert len(spans) == 1
    assert spans[0].kind == "shifted"


def test_shifted_tail_is_not_diluted_by_synced_prefix() -> None:
    # A long well-synced prefix followed by a genuinely shifted tail: the
    # combined median of the whole run must not hide the tail's local shift.
    render = _words("а б в г д е ж з и к л м н о", 0.0)
    cam_prefix = _words("а б в г д е ж з и к", 0.0)
    cam_tail = _words("л м н о", 3.0, offset=0.6)
    spans = diagnose_words(render, cam_prefix + cam_tail)
    assert any(s.kind == "shifted" for s in spans)


def test_content_mismatch_is_flagged() -> None:
    render = _words("сегодня мы говорим о совершенно других вещах", 0.0)
    cam = _words("сегодня мы говорим о синхронизации звука здесь", 0.0)
    spans = diagnose_words(render, cam)
    assert any(s.kind == "content" for s in spans)


def test_short_disagreement_below_min_words_is_ignored() -> None:
    # A single word transcribed differently on one side, with no timing
    # impact on its neighbours, is routine cross-run Whisper disagreement —
    # too short to count as a content mismatch.
    render = _words("так вот в общем это важно поскольку да", 0.0)
    cam = _words("так вот в общем это ясно поскольку да", 0.0)
    spans = diagnose_words(render, cam, min_content_words=3)
    assert spans == []


def test_empty_transcripts_produce_no_spans() -> None:
    assert diagnose_words([], []) == []
    assert diagnose_words(_words("привет", 0.0), []) == []


def test_jitter_within_normal_whisper_noise_is_not_flagged() -> None:
    import random

    rng = random.Random(7)
    render = []
    cam = []
    t = 0.0
    for i in range(20):
        tok = f"слово{i}"
        render.append(
            Word(text=tok, start=t + rng.uniform(-0.08, 0.08), end=t + 0.2, probability=0.9)
        )
        cam.append(Word(text=tok, start=t, end=t + 0.2, probability=0.9))
        t += 0.3
    spans = diagnose_words(render, cam)
    assert spans == []
