"""Anchor matching and time alignment between transcripts."""

from __future__ import annotations

import difflib
import logging
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from studio.stages.sync_match_settings import MatchSettings as WhisperSyncConfig
from studio.stages.sync_models import AlignmentMap, Anchor, Transcript, Word

logger = logging.getLogger(__name__)

# Strip anything that isn't a Unicode word character. string.punctuation (the
# old approach) only covers ASCII punctuation, so Russian/typographic marks
# like «», —, … pass through untouched and words end up with stray leading/
# trailing punctuation that never matches its "clean" counterpart on the other
# track — a silent source of lost anchors on non-English (esp. Russian) audio.
_NON_WORD_RE = re.compile(r"[^\w]", re.UNICODE)


def normalize_token(text: str) -> str:
    return _NON_WORD_RE.sub("", text.lower())


def normalize_words(words: list[Word], min_confidence: float) -> list[Word]:
    result: list[Word] = []
    for w in words:
        if w.probability < min_confidence:
            continue
        norm = normalize_token(w.text)
        if not norm:
            continue
        w.norm = norm
        result.append(w)
    return result


def _mid(w: Word) -> float:
    return (w.start + w.end) / 2


@dataclass
class RecorderIndex:
    """Rare-word position index for one recorder's normalized words, used by
    ``estimate_coarse_delta``. Building it (a ``Counter`` + position lists over
    every recorder word) is the same work regardless of which camera clip is
    being coarse-located, but the whole recorder is re-indexed from scratch for
    every clip aligned against it — expensive for a multi-hour recorder aligned
    against dozens of clips. Build once per recorder with ``build_recorder_index``
    and pass it into ``align``/``estimate_coarse_delta`` to reuse it.
    See PROJECT_ANALYSIS.md §6.5.
    """

    rec_positions: dict[str, list[float]]


def build_recorder_index(rec_words: list[Word], config: WhisperSyncConfig) -> RecorderIndex:
    rec_count = Counter(w.norm for w in rec_words)
    rec_positions: dict[str, list[float]] = defaultdict(list)
    for w in rec_words:
        if rec_count[w.norm] <= config.seed_max_occurrences:
            rec_positions[w.norm].append(_mid(w))
    return RecorderIndex(rec_positions=dict(rec_positions))


def estimate_coarse_delta(
    cam_words: list[Word],
    rec_words: list[Word],
    config: WhisperSyncConfig,
    rec_index: RecorderIndex | None = None,
) -> float | None:
    """Roughly locate the clip inside a (possibly very long) reference by voting
    on the time delta ``rec_time - cam_time`` of shared rare words. Returns the
    estimated delta (recorder time of the clip's start ≈ cam time + delta), or
    None if there is no confident peak.

    Assumes K ≈ 1 for the coarse pass, which is accurate enough over a single
    clip to pick the right window; the fine pass recovers the exact K.

    Pass a pre-built ``rec_index`` (``build_recorder_index``) to skip
    re-indexing the same recorder for every clip aligned against it.
    """
    rec_positions = (
        rec_index.rec_positions
        if rec_index is not None
        else build_recorder_index(rec_words, config).rec_positions
    )

    bin_width = config.seed_bin_width
    votes: Counter[int] = Counter()
    delta_sum: dict[int, float] = defaultdict(float)
    for cw in cam_words:
        ct = _mid(cw)
        for rt in rec_positions.get(cw.norm, ()):
            d = rt - ct
            b = round(d / bin_width)
            votes[b] += 1
            delta_sum[b] += d

    if not votes:
        return None

    best_bin, best_votes = votes.most_common(1)[0]
    if best_votes < max(3, config.min_anchors // 2):
        return None

    # weighted mean over the winning bin and its immediate neighbours
    total_n = 0
    total_d = 0.0
    for b in (best_bin - 1, best_bin, best_bin + 1):
        if b in votes:
            total_n += votes[b]
            total_d += delta_sum[b]
    return total_d / total_n


def _anchors_from_words(cam_words: list[Word], rec_words: list[Word]) -> list[Anchor]:
    cam_norms = [w.norm for w in cam_words]
    rec_norms = [w.norm for w in rec_words]

    all_tokens = cam_norms + rec_norms
    token_counts = Counter(all_tokens)

    matcher = difflib.SequenceMatcher(None, cam_norms, rec_norms, autojunk=False)
    raw_anchors: list[Anchor] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            continue
        for ci, ri in zip(range(i1, i2), range(j1, j2), strict=True):
            cw = cam_words[ci]
            rw = rec_words[ri]
            raw_anchors.append(
                Anchor(
                    cam_time=(cw.start + cw.end) / 2,
                    rec_time=(rw.start + rw.end) / 2,
                    token=cw.norm,
                    confidence=min(cw.probability, rw.probability),
                )
            )

    seen_tokens: set[str] = set()
    unique_anchors: list[Anchor] = []
    for a in raw_anchors:
        if token_counts[a.token] <= 4:
            if a.token in seen_tokens:
                continue
            seen_tokens.add(a.token)
        unique_anchors.append(a)

    monotonic: list[Anchor] = []
    last_cam = -float("inf")
    last_rec = -float("inf")
    for a in unique_anchors:
        if a.cam_time > last_cam and a.rec_time > last_rec:
            monotonic.append(a)
            last_cam = a.cam_time
            last_rec = a.rec_time

    logger.info(
        "Found %d anchors (%d raw, %d after uniqueness)",
        len(monotonic),
        len(raw_anchors),
        len(unique_anchors),
    )
    return monotonic


def find_anchors(
    cam_transcript: Transcript,
    rec_transcript: Transcript,
    min_confidence: float = 0.6,
) -> list[Anchor]:
    cam_words = normalize_words(list(cam_transcript.words), min_confidence)
    rec_words = normalize_words(list(rec_transcript.words), min_confidence)
    if not cam_words or not rec_words:
        return []
    return _anchors_from_words(cam_words, rec_words)


def reject_gross_outliers(
    anchors: list[Anchor], window: int = 10, tol_s: float = 0.30
) -> list[Anchor]:
    """Drop anchors whose ``cam_time - rec_time`` delta disagrees with their local
    neighbourhood — i.e. a word matched to the wrong (far-away) occurrence.

    Unlike a global-linear inlier test, this keeps anchors that follow a *smooth*
    (possibly non-linear) drift, because each is only compared to its neighbours.
    ``anchors`` must be sorted by rec_time (as produced by ``_anchors_from_words``).

    The window shrinks (down to a minimum of 2) for short anchor lists instead of
    disabling the filter outright — a clip with, say, 12 anchors used to skip this
    check entirely (it needed >= 2*window=20), so a single 5-second-off outlier
    would ride straight through to the piecewise warp. See PROJECT_ANALYSIS.md §2.6.
    """
    n = len(anchors)
    if n < 4:
        return anchors
    eff_window = min(window, max(2, n // 4))
    deltas = [a.cam_time - a.rec_time for a in anchors]
    kept: list[Anchor] = []
    for i, a in enumerate(anchors):
        lo = max(0, i - eff_window)
        hi = min(n, i + eff_window + 1)
        local = sorted(deltas[lo:hi])
        median = local[len(local) // 2]
        if abs(deltas[i] - median) <= tol_s:
            kept.append(a)
    return kept


def reject_residual_outliers(
    anchors: list[Anchor],
    offset: float,
    k: float,
    min_factor: float = 3.0,
    min_residual_s: float = 0.25,
) -> list[Anchor]:
    """Drop anchors whose residual against the fitted line (offset, k) is both
    far from the pack (> ``min_factor`` times the median residual) AND
    absolutely large (> ``min_residual_s``), so a lone isolated mismatch that
    slipped past ``reject_gross_outliers`` (e.g. because its local neighbourhood
    happened to also be sparse/noisy) doesn't feed the piecewise warp — a single
    such anchor can force a 0.5-5s stretch on its piece. Requires both
    conditions so a genuinely noisy-but-honest alignment (all residuals modestly
    elevated) isn't gutted. See PROJECT_ANALYSIS.md §2.6.
    """
    if len(anchors) < 4:
        return anchors
    residuals = [abs((offset + k * a.rec_time) - a.cam_time) for a in anchors]
    sorted_res = sorted(residuals)
    median = sorted_res[len(sorted_res) // 2]
    threshold = max(min_factor * median, min_residual_s)
    kept = [a for a, r in zip(anchors, residuals, strict=True) if r <= threshold]
    return kept if len(kept) >= 2 else anchors


def ransac_linear_fit(
    anchors: list[Anchor],
    n_iterations: int = 200,
    inlier_threshold_ms: float = 100.0,
) -> tuple[float, float, list[Anchor]]:
    rng = random.Random(42)
    best_inliers: list[Anchor] = []

    for _ in range(n_iterations):
        sample = rng.sample(anchors, 2)
        dr = sample[1].rec_time - sample[0].rec_time
        if abs(dr) < 1e-9:
            continue
        k = (sample[1].cam_time - sample[0].cam_time) / dr
        offset = sample[0].cam_time - k * sample[0].rec_time

        inliers: list[Anchor] = []
        for a in anchors:
            predicted = offset + k * a.rec_time
            residual_ms = abs(predicted - a.cam_time) * 1000
            if residual_ms < inlier_threshold_ms:
                inliers.append(a)

        if len(inliers) > len(best_inliers):
            best_inliers = inliers

    if len(best_inliers) < 2:
        best_inliers = anchors

    rec_times = np.array([a.rec_time for a in best_inliers])
    cam_times = np.array([a.cam_time for a in best_inliers])
    coeffs = np.polyfit(rec_times, cam_times, 1)
    k_final = float(coeffs[0])
    offset_final = float(coeffs[1])

    return offset_final, k_final, best_inliers


def _window_recorder(
    cam_words: list[Word],
    rec_words: list[Word],
    config: WhisperSyncConfig,
    rec_index: RecorderIndex | None = None,
) -> list[Word]:
    """If the recorder is much longer than the clip, restrict matching to a
    window around the coarse estimate; otherwise return all recorder words."""
    rec_span = _mid(rec_words[-1]) - _mid(rec_words[0])
    cam_lo = min(w.start for w in cam_words)
    cam_hi = max(w.end for w in cam_words)
    margin = config.match_window_margin

    # Only worth windowing when the reference dwarfs the needed window.
    if rec_span <= (cam_hi - cam_lo) + 4 * margin:
        return rec_words

    delta = estimate_coarse_delta(cam_words, rec_words, config, rec_index)
    if delta is None:
        return rec_words

    lo = cam_lo + delta - margin
    hi = cam_hi + delta + margin
    windowed = [w for w in rec_words if lo <= _mid(w) <= hi]
    if len(windowed) < 2:
        return rec_words
    logger.info(
        "Windowed match: delta=%.1fs window=[%.0f,%.0f]s, %d -> %d recorder words",
        delta,
        lo,
        hi,
        len(rec_words),
        len(windowed),
    )
    return windowed


def _match_words(cam_words: list[Word], rec_words: list[Word]) -> list[Anchor]:
    """Produce anchors from normalized words via the difflib LCS matcher.

    This used to also dispatch to a banded-DTW backend (``config.align_mode
    == "dtw"``); real-data measurements showed it performed worse than
    difflib on this project's actual failure modes (see the git history and
    PROJECT_ANALYSIS.md for the investigation) and it was removed along with
    ``engine/dtw.py``. Kept as a thin wrapper — a single call site for word
    matching — in case a future backend is worth adding here again.
    """
    return _anchors_from_words(cam_words, rec_words)


# Upper bound on (camera words x recorder words) for the full-reference
# re-match. difflib's matcher is ~quadratic on repetitive tokens; 4e6 pairs is
# roughly a second of CPU on the measurements above, which is an acceptable
# per-clip fallback cost, while an unbounded retry against a multi-hour
# recorder is minutes of it.
_FULL_RETRY_MAX_WORK = 4_000_000


def align(
    cam_transcript: Transcript,
    rec_transcript: Transcript,
    config: WhisperSyncConfig,
    rec_index: RecorderIndex | None = None,
) -> AlignmentMap:
    """Align a camera clip's transcript to a recorder's.

    ``rec_index`` (``build_recorder_index``) lets a caller aligning many clips
    against the SAME recorder build its rare-word position index once instead
    of on every call — the index only depends on ``rec_transcript`` and
    ``config``, not on the clip. Optional; built on the fly if omitted.
    """
    cam_words = normalize_words(list(cam_transcript.words), config.anchor_min_confidence)
    rec_words = normalize_words(list(rec_transcript.words), config.anchor_min_confidence)
    if not cam_words or not rec_words:
        raise ValueError("No usable words to align (check confidence threshold / speech content).")

    rec_used = _window_recorder(cam_words, rec_words, config, rec_index)
    anchors = _match_words(cam_words, rec_used)

    # If the coarse window was misleading, retry once against the full
    # reference — but only when that is affordable.
    #
    # `SequenceMatcher(autojunk=False)` is roughly quadratic on repetitive
    # tokens: measured at 500/1000/2000 words it took 0.060/0.219/0.949 s, so a
    # multi-hour recorder against a long clip is minutes of un-cancellable CPU
    # per clip, entered precisely when the cheap path already struggled. The
    # cap bounds that instead of silently paying it.
    #
    # `autojunk` is deliberately NOT enabled: it would suppress tokens
    # appearing in more than 1% of positions, which on this material includes
    # ordinary vocabulary, and the effect on anchor recall has not been
    # measured on a corpus with repeats, pauses and ASR hallucinations. Bounding
    # the work is safe; changing which words can anchor is not, without that
    # measurement.
    if len(anchors) < config.min_anchors and rec_used is not rec_words:
        work = len(cam_words) * len(rec_words)
        if work > _FULL_RETRY_MAX_WORK:
            logger.warning(
                "Windowed match weak (%d anchors) but the full reference is too large to "
                "re-match (%d x %d word pairs, limit %d) — keeping the windowed result. "
                "Raise match_window_margin if this clip is genuinely misplaced.",
                len(anchors),
                len(cam_words),
                len(rec_words),
                _FULL_RETRY_MAX_WORK,
            )
        else:
            logger.info("Windowed match weak (%d anchors); retrying full reference", len(anchors))
            anchors = _match_words(cam_words, rec_words)

    if len(anchors) < 2:
        raise ValueError(
            f"Only {len(anchors)} anchor(s) found — need at least 2. "
            "The transcripts may not contain enough matching speech."
        )

    # Drop gross mismatches (a word matched to a wrong far-away occurrence) while
    # KEEPING anchors that follow the smooth local drift. These feed the per-clip
    # piecewise warp, so they must track the real (non-linear) drift — not be
    # flattened onto a single line.
    kept = reject_gross_outliers(anchors)
    if len(kept) < 2:
        kept = anchors

    # A robust global line (offset, K) still drives timeline placement and the
    # Global-Linear strategy; it is fit on the cleaned anchors.
    offset, k, line_inliers = ransac_linear_fit(kept)

    # A second pass against the fitted line catches an ISOLATED outlier that
    # reject_gross_outliers missed (its own local neighbourhood was sparse/noisy
    # enough to not flag it). Applied to `kept` (not just the RANSAC inlier set)
    # so the piecewise warp — which uses every kept anchor, not only inliers —
    # never sees a single-anchor 0.5-5s mismatch. See PROJECT_ANALYSIS.md §2.6.
    kept = reject_residual_outliers(kept, offset, k)

    residuals_ms = [abs((offset + k * a.rec_time) - a.cam_time) * 1000 for a in line_inliers]
    residual_ms = float(np.median(residuals_ms)) if residuals_ms else 0.0

    if len(line_inliers) < config.min_anchors:
        logger.warning(
            "Only %d inlier anchors (minimum recommended: %d). Alignment may be inaccurate.",
            len(line_inliers),
            config.min_anchors,
        )

    logger.info(
        "Alignment: offset=%.4fs, K=%.6f, anchors=%d (kept %d of %d raw), residual=%.1fms",
        offset,
        k,
        len(line_inliers),
        len(kept),
        len(anchors),
        residual_ms,
    )

    span = 0.0
    if len(line_inliers) >= 2:
        rec_times = [a.rec_time for a in line_inliers]
        span = max(rec_times) - min(rec_times)

    return AlignmentMap(
        anchors=kept,
        offset=offset,
        k=k,
        residual_ms=residual_ms,
        provenance="text",
        inliers=len(line_inliers),
        evidence_span_s=span,
    )


# Recommendation thresholds. Below _AUTO_LINEAR_RESIDUAL_MS of residual, a
# single global tempo conform already tracks the drift closely enough that
# per-segment corrections wouldn't measurably improve sync. Non-linearity is
# detected by fitting a SEPARATE least-squares clock ratio K to each half of
# the clip's anchors: real crystal drift is constant-rate (both halves agree
# within noise), so the halves' K disagreeing by more than
# _AUTO_K_SPREAD_PERMILLE (1‰ = 1 ms of extra drift per second) means the
# tempo genuinely bends within the clip. Half-fits are only trusted with
# enough anchors spread over enough time — slopes over a short/thin half
# amplify Whisper's ±50-100 ms word-timing jitter into nonsense. (The
# previous heuristic compared CONSECUTIVE anchor pairs, where two anchors
# half a second apart turn that jitter into local-K "spreads" of hundreds of
# ‰, and its threshold had mismatched units — so it cried "non-linear" on
# essentially every recording with residual above the linear gate.)
_AUTO_LINEAR_RESIDUAL_MS = 15.0
_AUTO_K_SPREAD_PERMILLE = 1.0
_AUTO_MIN_HALF_ANCHORS = 4
_AUTO_MIN_HALF_SPAN_S = 20.0


def _half_slope(anchors: list[Anchor]) -> float | None:
    """Least-squares clock ratio K over ``anchors``, or None when there are
    too few of them / they span too little time for the slope to be trusted."""
    if len(anchors) < _AUTO_MIN_HALF_ANCHORS:
        return None
    rec = np.array([a.rec_time for a in anchors])
    cam = np.array([a.cam_time for a in anchors])
    if float(rec.max() - rec.min()) < _AUTO_MIN_HALF_SPAN_S:
        return None
    return float(np.polyfit(rec, cam, 1)[0])


def recommend_strategy(alignment: AlignmentMap) -> tuple[int, str]:
    """Recommend a sync strategy id from an already-computed alignment.

    Looks at two signals a caller already paid for by calling ``align()``:
    the fitted line's residual (how well a single global K already explains
    the anchors) and whether the clock ratio K genuinely changes between the
    first and second half of the clip (see the threshold comment above).
    Returns ``(strategy_id, reason)``, where ``reason`` is a short
    human-readable justification suitable for a warning/log line. See
    PROJECT_ANALYSIS.md §10.1.
    """
    if alignment.residual_ms <= _AUTO_LINEAR_RESIDUAL_MS:
        return 1, f"drift is linear (residual {alignment.residual_ms:.1f} ms)"

    anchors = sorted(alignment.anchors, key=lambda a: a.rec_time)
    mid = len(anchors) // 2
    k_first = _half_slope(anchors[:mid])
    k_second = _half_slope(anchors[mid:])
    if k_first is not None and k_second is not None:
        spread = abs(k_second - k_first) * 1000.0  # ‰ (1‰ = 1 ms drift per second)
        if spread > _AUTO_K_SPREAD_PERMILLE:
            return (
                2,
                f"drift is non-linear (clock rate changes {spread:.2f}‰ "
                f"between clip halves, residual {alignment.residual_ms:.1f} ms)",
            )

    return 3, f"drift needs per-phrase correction (residual {alignment.residual_ms:.1f} ms)"


# ---------------------------------------------------------------------------
# Acceptance gate
# ---------------------------------------------------------------------------
#
# A map is not "good" because it exists. Two anchors define a line exactly, so
# a residual near zero proves nothing about a two-point fit — a synthetic pair
# of falsely matched words reproduced k ~= 10 and offset ~= -1004 s with a
# residual under a millisecond. Nor does a small residual over three seconds
# of a ten-minute clip say anything about the other 597 seconds.
#
# So every use of a map — timeline placement AND the render itself, which used
# to read straight from the raw per-recorder matrix and so could render from a
# map placement had already rejected — passes through `evaluate_alignment`
# first. It checks the four things a clock map can be wrong about
# independently: how much evidence there is, how far that evidence reaches,
# how well the line actually fits it, and whether the resulting clock ratio is
# physically possible for two devices recording the same event.


@dataclass
class AlignmentVerdict:
    """Whether a map may be used, and why not when it may not."""

    accepted: bool
    reasons: list[str] = field(default_factory=list)
    # Coverage of the clip's own duration by the evidence, 0..1.
    coverage: float = 0.0

    @property
    def reason_text(self) -> str:
        return "; ".join(self.reasons)


def evaluate_alignment(
    am: AlignmentMap | None,
    clip_duration: float,
    config: WhisperSyncConfig,
) -> AlignmentVerdict:
    """Decide whether ``am`` is trustworthy enough to place AND render with.

    The thresholds live on the config (``alignment_*``) rather than being
    hard-coded, because the honest limits differ by material and device: a
    ±5% clock-ratio bound is generous for two crystal-clocked recorders and
    far too tight for deliberately speed-changed footage. What must not vary
    is that the check happens at all, on every map, before any audio is cut.
    """
    if am is None:
        return AlignmentVerdict(False, ["no alignment"])

    reasons: list[str] = []

    # Clock ratio: two devices recording the same event drift by parts per
    # million, not by percent. k far from 1 is not drift, it is a wrong match.
    max_dev = config.alignment_max_k_deviation
    if not (am.k and abs(am.k - 1.0) <= max_dev):
        reasons.append(f"implausible clock ratio k={am.k:.4f} (allowed 1±{max_dev:g})")

    span = am.evidence_span_s
    coverage = span / clip_duration if clip_duration > 0 else 0.0

    if am.provenance == "acoustic":
        # An acoustic map has no word anchors by construction; its evidence is
        # the number of confident grid points behind the fit.
        if am.inliers < config.alignment_min_acoustic_points:
            reasons.append(
                f"only {am.inliers} confident acoustic point(s) "
                f"(minimum {config.alignment_min_acoustic_points})"
            )
    else:
        if am.inliers < config.min_anchors:
            reasons.append(f"only {am.inliers} inlier anchor(s) (minimum {config.min_anchors})")
        if am.residual_ms > config.alignment_max_residual_ms:
            reasons.append(
                f"residual {am.residual_ms:.0f} ms exceeds "
                f"{config.alignment_max_residual_ms:.0f} ms"
            )

    if clip_duration > 0 and coverage < config.alignment_min_coverage:
        reasons.append(
            f"evidence spans {span:.1f}s of a {clip_duration:.1f}s clip "
            f"({coverage:.0%} < {config.alignment_min_coverage:.0%})"
        )

    return AlignmentVerdict(not reasons, reasons, coverage)
