"""Retake detection: find lines the speaker re-recorded back-to-back.

In an unedited monologue (a lecture, a course), the speaker flubs a line, stops,
and restarts it — sometimes several times — before continuing past it. In the
recorder transcript this shows up as a run of words repeating moments after they
were first spoken: "...кто ещё учится в школе, а не выпустился... кто ещё учится
в школе, а не выпустился, ну и так далее...". The restart is usually a resumed
sentence, not a whole isolated phrase — Whisper's segment/sentence boundaries
don't reliably fall on it — so detection works at the TOKEN level: scan for a
short run of words that repeats a run spoken shortly before, without assuming
any sentence structure around it.

This module groups consecutive restarts of the same material into "takes" so
the exporter can MARK them for the editor (see engine/export.py) instead of
leaving every flubbed attempt unlabelled on the timeline. Marking, not
restructuring: an audition that switches audio without switching the linked
picture desynchronises the result, so grouping is a review aid here and any
automatic re-cutting is a separate feature with its own timing model.

The detector is deterministic and dependency-free (exact token-run matching,
normalized). It is intentionally the FIRST tier of a two-tier design: an
optional LLM refiner (llama.cpp + a local Gemma, mirroring Podcast Reels
Forge's moment-scoring) can later catch paraphrased restarts an exact match
misses and pick the best-delivered take — see ``refine_retakes`` for the seam
it plugs into.
"""

from __future__ import annotations

from dataclasses import dataclass

from studio.core.transcript import Word
from studio.stages.retake_models import RetakeGroup, RetakeSettings, Take
from studio.stages.sync_matcher import normalize_token

__all__ = ["RetakeGroup", "Take", "detect_retakes", "refine_retakes"]


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


def _find_restart(toks: list[_Tok], i: int, k: int, max_lookahead: int) -> int:
    """Nearest index ``j > i`` (non-overlapping, ``j >= i + k``) at which the
    ``k``-token run beginning at ``i`` repeats verbatim (on normalized tokens),
    within ``max_lookahead`` tokens. This is a fast CANDIDATE finder only — it
    does not judge whether the repeat is a genuine retake; ``_match_extent``
    below extends the match to its true span and is the actual arbiter (via the
    gap it computes). Returns ``-1`` when there is no repeat in range.
    """
    gram = [t.norm for t in toks[i : i + k]]
    j_hi = min(len(toks) - k, i + max_lookahead)
    for j in range(i + k, j_hi + 1):
        if [t.norm for t in toks[j : j + k]] == gram:
            return j
    return -1


def _match_extent(
    toks: list[_Tok], i: int, j: int, k: int, min_index: int = 0
) -> tuple[int, int, int, int]:
    """Extend a verified ``k``-token match at ``(i, j)`` to its maximal span.

    A restart usually repeats more than just the ``k`` tokens used to find it
    (the whole flubbed phrase, not an arbitrary window of it), and the SAME
    underlying repeat can be "found" from several different starting offsets
    within it (any k-token window inside a repeated span matches too). Judging
    each offset independently based only on its own local timing is unreliable
    — an offset deep inside a long-ago phrase can look like it's "immediately
    followed" by the corresponding offset inside a much-later unrelated repeat,
    purely because both sit at the same position within their own (short,
    tightly-spoken) phrase. Extending every match to its full, offset-
    independent extent first — then judging timing on THAT — makes the
    decision the same regardless of which offset happened to be tried.

    ``min_index`` bounds the BACKWARD extension, and it is what makes three or
    more identical attempts work. With the same phrase spoken at 0 s, 3 s and
    6 s, extending the 3 s -> 6 s match backwards found that the tokens before
    3 s also matched — they are the SAME phrase, at 0 s — and walked the span
    all the way into the first attempt. The two occurrences then overlapped,
    the gap between them came out NEGATIVE, the match was rejected as
    implausible, and the group stopped at two attempts with the middle one
    kept. Passing the current attempt's own start keeps the extension inside
    it.

    Returns ``(lo_i, hi_i, lo_j, hi_j)``: the first occurrence spans
    ``[lo_i, hi_i)``, the second (the repeat) spans ``[lo_j, hi_j)``.
    """
    lo_i, lo_j = i, j
    while lo_i > min_index and toks[lo_i - 1].norm == toks[lo_j - 1].norm:
        lo_i -= 1
        lo_j -= 1
    hi_i, hi_j = i + k, j + k
    n = len(toks)
    while hi_i < lo_j and hi_j < n and toks[hi_i].norm == toks[hi_j].norm:
        hi_i += 1
        hi_j += 1
    return lo_i, hi_i, lo_j, hi_j


def _run_end(toks: list[_Tok], start_idx: int, gap_threshold: float) -> int:
    """Index one past the end of the speech run beginning at ``start_idx`` — the
    run ends at the first pause >= ``gap_threshold`` or the transcript's end."""
    j = start_idx
    while j + 1 < len(toks) and toks[j + 1].start - toks[j].end < gap_threshold:
        j += 1
    return j + 1


def _span_text(toks: list[_Tok], a: int, b: int) -> str:
    return " ".join(t.text for t in toks[a:b]).strip()


def _extended_restart(
    toks: list[_Tok], i: int, k: int, max_gap_s: float, max_lookahead: int, min_index: int = 0
) -> tuple[int, int] | None:
    """Find a restart of the phrase beginning at ``i`` and validate it by its
    EXTENDED span (see ``_match_extent``): accepted only when the two
    occurrences do not overlap and the pause between where the first one's
    matched span ends and the second begins is within ``max_gap_s``. Returns
    ``(lo_i, lo_j)`` — the (possibly earlier than ``i``) true start of the
    first occurrence, and the start of the restart — or ``None`` if no
    candidate in range passes.

    ``min_index`` is the earliest token the backward extension may reach: when
    chaining a third or fourth attempt, that is the current attempt's own
    start, so the extension cannot reach back into an attempt already assigned
    to this group.
    """
    j = _find_restart(toks, i, k, max_lookahead)
    if j < 0:
        return None
    lo_i, hi_i, lo_j, _hi_j = _match_extent(toks, i, j, k, min_index)
    if lo_j < hi_i:
        # Overlapping occurrences are not two attempts at a line, they are one
        # span counted twice. (Their "gap" would be negative.)
        return None
    gap = toks[lo_j].start - toks[hi_i - 1].end
    if 0.0 <= gap <= max_gap_s:
        return lo_i, lo_j
    return None


def detect_retakes(words: list[Word], config: RetakeSettings) -> list[RetakeGroup]:
    """Find lines re-recorded back-to-back and group each set of attempts.

    A restart is a run of at least ``retake_min_words`` normalized tokens that
    repeats verbatim, extended to its true span (``_match_extent``), with a
    pause of at most ``retake_max_gap_s`` between where the first occurrence's
    matched material ends and the restart begins. The first attempt spans from
    where the repeated material truly began to the restart; each further
    restart of the same material chains onto the group (2+ attempts); the final
    attempt extends to the end of its own speech run (through the first real
    pause) and is the one kept, since it is the version the speaker completed
    before moving on. Requires ``config.detect_retakes``; returns ``[]``
    otherwise or when nothing qualifies.
    """
    if not config.detect_retakes:
        return []
    toks = _tokens(words)
    k = config.retake_min_words
    if k < 1 or len(toks) < 2 * k:
        return []
    # A restart follows its flub within a handful of words in practice (a short
    # stumble, not a full extra sentence); this bound just keeps the scan local.
    max_lookahead = max(4 * k, 30)

    groups: list[RetakeGroup] = []
    i = 0
    n = len(toks)
    while i <= n - k:
        found = _extended_restart(toks, i, k, config.retake_max_gap_s, max_lookahead)
        if found is None:
            i += 1
            continue
        lo_i, lo_j = found
        starts = [lo_i, lo_j]
        cur = lo_j
        while True:
            # `cur` bounds the backward extension: without it, matching the
            # 2nd attempt against the 3rd extended back through the 1st and
            # produced overlapping spans with a negative gap, which was then
            # rejected — so three identical takes yielded a group of two.
            nxt = _extended_restart(
                toks, cur, k, config.retake_max_gap_s, max_lookahead, min_index=cur
            )
            if nxt is None:
                break
            _cur_lo, nxt_j = nxt
            if nxt_j <= cur:
                break  # never move backwards; that would re-add an attempt
            starts.append(nxt_j)
            cur = nxt_j
        # The keeper's own extent uses phrase_gap_threshold (the same
        # sentence-boundary notion the renderer uses), not retake_max_gap_s:
        # tried the more lenient threshold first, but on the real dataset it
        # let normal speech pauses several minutes later swallow the keeper
        # into one enormous take. phrase_gap_threshold occasionally truncates
        # a keeper a bit early when the speaker adds a longer-than-usual
        # emphatic pause mid-sentence ("Я... уверен. Что будущее — это вы."),
        # but that failure mode is harmless — the audition is non-destructive,
        # so the editor just sees a short "keeper" clip and can extend it in
        # Final Cut — whereas a keeper stretching minutes past the actual
        # retake is a much worse practical outcome.
        last_end = _run_end(toks, cur, config.phrase_gap_threshold)
        # Attempts must tile without overlapping: each take runs from its own
        # start to the next attempt's start, and the last to the end of its
        # speech run. A `last_end` that did not clear the final start would
        # make the keeper zero- or negative-length.
        last_end = max(last_end, cur + 1)
        bounds = [*starts, last_end]
        takes = [
            Take(
                toks[bounds[t]].start,
                toks[bounds[t + 1] - 1].end,
                _span_text(toks, bounds[t], bounds[t + 1]),
            )
            for t in range(len(starts))
        ]
        groups.append(RetakeGroup(takes=takes, keeper_index=-1))
        i = last_end
    return groups


def refine_retakes(
    groups: list[RetakeGroup], words: list[Word], config: RetakeSettings
) -> list[RetakeGroup]:
    """Optional second tier: hand the algorithmic groups (and the surrounding
    transcript) to a local LLM to merge paraphrased attempts the token
    similarity missed and to choose the best-delivered keeper. A no-op until an
    LLM backend is wired in (llama.cpp + Gemma, as in Podcast Reels Forge);
    kept as the stable seam so enabling it later doesn't change call sites.
    """
    return groups
