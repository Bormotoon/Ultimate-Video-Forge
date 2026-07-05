"""Post-render self-check: verify a rendered voice monolith against the
camera clip's own transcript.

``--verify``/``tools/verify_sync.py`` already measures realized LAG between
the rendered voice and the camera audio via GCC-PHAT cross-correlation — but
it is blind to CONTENT defects (a dropped word, a duplicated phrase, a piece
built from the wrong recorder span): it only measures *where* two waveforms
correlate, so a piece with wrong-but-present speech can still show a small
lag if enough of the surrounding audio still lines up.

This module closes that gap by re-transcribing the rendered voice monolith
with Whisper and comparing its words against the camera clip's OWN
transcript (already computed during alignment — the natural "original" to
check against, not the recorder). The two transcripts should describe the
same speech at (very nearly) the same local timestamps if the render is
correct; a span where they diverge — either a timing drift beyond normal
cross-run Whisper jitter, or words that simply don't match — is flagged as a
warning for the user to check in the NLE. v1 is detect-only (no automatic
repair), mirroring how ``engine.retakes`` shipped first as a pure detector
before any editing action was layered on top.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass

from whispersync.engine.matcher import normalize_token
from whispersync.models import Word

__all__ = ["SelfCheckSpan", "check_rendered_clip"]

# A run of at least this many consecutive matched words with a median timing
# delta above this many seconds is flagged as "shifted". Normal cross-run
# Whisper word-timing jitter (two independent transcriptions of conceptually
# the same speech) is on the order of ±50-100 ms per word; a sustained
# multi-word median past 250 ms is well outside that noise band.
DEFAULT_MIN_RUN_WORDS = 3
DEFAULT_SHIFT_THRESHOLD_S = 0.25

# A content run (words present on one side with no match on the other) at
# least this long is flagged as "content" — short 1-2 word gaps are routine
# Whisper transcription disagreement (a dropped filler word, a differently
# split contraction), not evidence the render used the wrong audio.
DEFAULT_MIN_CONTENT_WORDS = 3


@dataclass
class SelfCheckSpan:
    """One flagged span of a rendered clip's local timeline.

    ``kind`` is ``"shifted"`` (both sides have matching words, but their
    timing disagrees by more than normal jitter) or ``"content"`` (a run of
    words present on one side has no counterpart on the other at all).
    """

    start: float
    end: float
    kind: str
    detail: str


@dataclass
class _Tok:
    norm: str
    start: float
    end: float
    text: str


def _tokens(words: list[Word]) -> list[_Tok]:
    out: list[_Tok] = []
    for w in sorted(words, key=lambda x: x.start):
        norm = normalize_token(w.text)
        if norm:
            out.append(_Tok(norm=norm, start=w.start, end=w.end, text=w.text.strip()))
    return out


def _median(values: list[float]) -> float:
    s = sorted(values)
    return s[len(s) // 2]


def _shifted_spans(
    render_toks: list[_Tok],
    cam_toks: list[_Tok],
    equal_pairs: list[tuple[int, int]],
    min_run_words: int,
    shift_threshold_s: float,
) -> list[SelfCheckSpan]:
    """Flag stretches of consecutive matched word-pairs whose LOCAL timing
    delta exceeds ``shift_threshold_s``. ``equal_pairs`` are (render_idx,
    cam_idx) index pairs for tokens difflib judged equal, in matched order.

    A single greedy run merged across the whole matched sequence (grouping
    only by index adjacency) lets a genuinely shifted tail get diluted by a
    long well-synced prefix sharing the same run — its combined median could
    sit under the threshold even though the tail alone is clearly off. A
    sliding window instead judges every ``min_run_words``-wide neighbourhood
    on its own median, so a local anomaly is caught regardless of what comes
    before or after it; overlapping/adjacent flagged windows are then merged
    into one reported span.
    """
    n = len(equal_pairs)
    if n < min_run_words:
        return []
    deltas = [abs(render_toks[ri].start - cam_toks[ci].start) for ri, ci in equal_pairs]

    flagged: list[tuple[int, int]] = []  # (start_idx, end_idx) into equal_pairs, inclusive
    for i in range(n - min_run_words + 1):
        window = deltas[i : i + min_run_words]
        if _median(window) > shift_threshold_s:
            flagged.append((i, i + min_run_words - 1))

    # Merge adjacent/overlapping windows (and windows separated only by a
    # small index gap in equal_pairs, i.e. a couple of unmatched words) into
    # single spans.
    spans: list[SelfCheckSpan] = []
    k = 0
    while k < len(flagged):
        lo, hi = flagged[k]
        k += 1
        while k < len(flagged) and flagged[k][0] <= hi + 1:
            hi = max(hi, flagged[k][1])
            k += 1
        run = deltas[lo : hi + 1]
        median = _median(run)
        ri0, _ci0 = equal_pairs[lo]
        ri1, _ci1 = equal_pairs[hi]
        spans.append(
            SelfCheckSpan(
                start=render_toks[ri0].start,
                end=render_toks[ri1].end,
                kind="shifted",
                detail=(
                    f"{hi - lo + 1} word(s) timed {median * 1000:.0f} ms off "
                    "from the camera transcript"
                ),
            )
        )
    return spans


def _content_spans(
    render_toks: list[_Tok],
    cam_toks: list[_Tok],
    opcodes: Sequence[tuple[str, int, int, int, int]],
    min_content_words: int,
) -> list[SelfCheckSpan]:
    """Flag long replace/delete/insert runs from the difflib opcodes as
    likely content mismatches (missing/duplicated/wrong speech), as opposed
    to the routine 1-2 word disagreements between two independent Whisper
    passes over similar audio."""
    spans: list[SelfCheckSpan] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        render_span = i2 - i1
        cam_span = j2 - j1
        if max(render_span, cam_span) < min_content_words:
            continue
        if render_span > 0:
            start = render_toks[i1].start
            end = render_toks[i2 - 1].end
        else:
            # A pure insert on the camera side (nothing rendered at this
            # point) has no render-side timestamps to anchor on; use the
            # neighbouring rendered word's boundary instead.
            start = render_toks[i1 - 1].end if i1 > 0 else 0.0
            end = render_toks[i1].start if i1 < len(render_toks) else start
        spans.append(
            SelfCheckSpan(
                start=start,
                end=end,
                kind="content",
                detail=(
                    f"{render_span} rendered word(s) vs {cam_span} camera word(s) "
                    f"don't match ({tag})"
                ),
            )
        )
    return spans


def diagnose_words(
    render_words: list[Word],
    cam_words: list[Word],
    min_run_words: int = DEFAULT_MIN_RUN_WORDS,
    shift_threshold_s: float = DEFAULT_SHIFT_THRESHOLD_S,
    min_content_words: int = DEFAULT_MIN_CONTENT_WORDS,
) -> list[SelfCheckSpan]:
    """Compare a rendered clip's transcript words against the camera clip's
    own transcript words (both already in the clip's local time) and return
    flagged spans, sorted by start time. Pure function over word lists —
    kept separate from ``check_rendered_clip`` (which does the transcription
    I/O) so it's directly unit-testable against synthetic word lists."""
    render_toks = _tokens(render_words)
    cam_toks = _tokens(cam_words)
    if not render_toks or not cam_toks:
        return []

    matcher = difflib.SequenceMatcher(
        None, [t.norm for t in render_toks], [t.norm for t in cam_toks], autojunk=False
    )
    opcodes = matcher.get_opcodes()

    equal_pairs: list[tuple[int, int]] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag != "equal":
            continue
        equal_pairs.extend(zip(range(i1, i2), range(j1, j2), strict=True))

    spans = _shifted_spans(
        render_toks, cam_toks, equal_pairs, min_run_words, shift_threshold_s
    ) + _content_spans(render_toks, cam_toks, opcodes, min_content_words)
    spans.sort(key=lambda s: s.start)
    return spans


def check_rendered_clip(
    rendered_words: list[Word],
    camera_words: list[Word],
    config: object,
) -> list[SelfCheckSpan]:
    """Convenience wrapper reading thresholds off ``config`` (duck-typed to
    avoid importing ``WhisperSyncConfig`` here and creating a cycle; the
    pipeline passes the real config)."""
    return diagnose_words(
        rendered_words,
        camera_words,
        min_run_words=getattr(config, "self_check_min_run_words", DEFAULT_MIN_RUN_WORDS),
        shift_threshold_s=getattr(
            config, "self_check_shift_threshold_s", DEFAULT_SHIFT_THRESHOLD_S
        ),
        min_content_words=getattr(
            config, "self_check_min_content_words", DEFAULT_MIN_CONTENT_WORDS
        ),
    )
