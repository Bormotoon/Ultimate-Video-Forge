"""End-to-end orchestration pipeline with progress signals.

The recorder is the continuous reference. Each camera clip is transcribed and
aligned to the recorder independently, so a clip's position on the master
timeline comes from matched timecodes — clips need not be contiguous.
"""

from __future__ import annotations

import contextlib
import logging
import multiprocessing as mp
import os
import shutil
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from whispersync.config import WhisperSyncConfig
from whispersync.engine.acoustic import acoustic_coarse_align, refine_piece_boundaries
from whispersync.engine.export import check_fcpxml, generate_fcpxml
from whispersync.engine.matcher import (
    align,
    build_recorder_index,
    evaluate_alignment,
    normalize_words,
    recommend_strategy,
)
from whispersync.engine.media import (
    MediaInfo,
    extract_audio_master,
    extract_audio_to_wav,
    pcm_codec_for,
    probe,
)
from whispersync.engine.naming import natural_key
from whispersync.engine.retakes import detect_retakes
from whispersync.engine.sources import assign_source_ids
from whispersync.engine.strategies import strategy_name
from whispersync.engine.timestretch import (
    assemble_continuous,
    cut_wav_segment,
    mix_clips_on_timeline,
    render_piece,
)
from whispersync.engine.transcriber import WhisperEngine
from whispersync.engine.transcript_export import save_transcript
from whispersync.engine.workspace import RunWorkspace, output_lock, published
from whispersync.models import (
    AlignmentMap,
    Anchor,
    MediaClip,
    RetakeGroup,
    SyncResult,
    Take,
    Transcript,
    Word,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineProgress:
    stage: str
    progress: float = 0.0
    message: str = ""
    # Optional timeline snapshot (list of clip dicts) for the GUI timeline view.
    clips: list[dict[str, Any]] | None = None


ProgressCallback = Callable[[PipelineProgress], None]


def make_timeline_entries(
    cameras: list[CameraGroup],
    clip_camera: list[int],
    video_clips: list[MediaClip],
    video_status: list[str],
    audio_clips: list[MediaClip],
    audio_speed: list[float],
    audio_track: list[str],
    audio_status: list[str],
) -> list[dict[str, Any]]:
    """Build a flat list of timeline clip dicts for the GUI.

    Video clips occupy one row per camera (row = camera index); audio lanes are
    stacked below in first-seen order. ``speed`` is the playback rate applied to
    the recorder audio (1.0 = untouched); ``status`` is pending/working/done.
    """
    entries: list[dict[str, Any]] = []
    for clip, ci, status in zip(video_clips, clip_camera, video_status, strict=True):
        entries.append(
            {
                "track": cameras[ci].name,
                "row": ci,
                "kind": "video",
                "lane": clip.lane,
                "offset": clip.offset,
                "duration": clip.duration,
                "in_point": clip.in_point,
                "speed": 1.0,
                "status": status,
                "name": clip.path.stem,
            }
        )

    audio_rows: dict[str, int] = {}
    next_row = len(cameras)
    for label in audio_track:
        if label not in audio_rows:
            audio_rows[label] = next_row
            next_row += 1

    for clip, speed, label, status in zip(
        audio_clips, audio_speed, audio_track, audio_status, strict=True
    ):
        entries.append(
            {
                "track": label,
                "row": audio_rows[label],
                "kind": "audio",
                "lane": clip.lane,
                "offset": clip.offset,
                "duration": clip.duration,
                "in_point": clip.in_point,
                "speed": speed,
                "status": status,
                "name": clip.path.stem,
            }
        )
    return entries


@dataclass
class CameraGroup:
    name: str
    lane: int
    infos: list[MediaInfo]
    clips: list[MediaClip]


def scan_cameras(video_dir: Path, config: WhisperSyncConfig) -> tuple[list[CameraGroup], list[str]]:
    """Discover cameras and probe their clips.

    If ``video_dir`` contains sub-folders with video files, each sub-folder is
    treated as a separate camera placed on its own positive lane (1, 2, 3, …).
    Otherwise the flat folder is a single camera on lane 1. Clip offsets are
    left at 0 — real timeline positions come later from matched timecodes.

    Returns ``(cameras, warnings)``. When camera sub-folders exist, video files
    left directly in ``video_dir`` are ignored (only sub-folder contents become
    cameras) — a warning names them so this doesn't silently drop footage the
    user expected to be included. See PROJECT_ANALYSIS.md §2.8.
    """
    exts = tuple(config.video_exts)
    warnings: list[str] = []

    def videos_in(d: Path) -> list[Path]:
        return sorted(
            (p for p in d.iterdir() if p.suffix.lower() in exts),
            key=lambda p: natural_key(p.name),
        )

    subdirs = sorted(
        (d for d in video_dir.iterdir() if d.is_dir() and videos_in(d)),
        key=lambda d: natural_key(d.name),
    )
    sources: list[tuple[str, list[Path]]]
    if subdirs:
        sources = [(d.name, videos_in(d)) for d in subdirs]
        root_files = videos_in(video_dir)
        if root_files:
            names = ", ".join(p.name for p in root_files)
            warnings.append(
                f"{len(root_files)} video file(s) in the root of --video-dir "
                f"ignored because camera sub-folders exist: {names}"
            )
    else:
        sources = [("camera", videos_in(video_dir))]

    if not any(paths for _, paths in sources):
        raise RuntimeError(f"No video files found in {video_dir}")

    cameras: list[CameraGroup] = []
    for cam_index, (name, paths) in enumerate(sources):
        infos: list[MediaInfo] = []
        clips: list[MediaClip] = []
        lane = cam_index + 1
        for path in paths:
            info = probe(path, timeout=config.probe_timeout_s)
            infos.append(info)
            clips.append(
                MediaClip(
                    path=path,
                    kind="video",
                    offset=0.0,
                    in_point=0.0,
                    duration=info.duration,
                    lane=lane,
                    role="Video",
                )
            )
        cameras.append(CameraGroup(name=name, lane=lane, infos=infos, clips=clips))

    return cameras, warnings


def scan_video_clips(
    video_dir: Path, config: WhisperSyncConfig
) -> tuple[list[MediaInfo], list[MediaClip]]:
    """Flat view over all cameras (used by the dry-run path)."""
    cameras, _warnings = scan_cameras(video_dir, config)
    infos: list[MediaInfo] = []
    clips: list[MediaClip] = []
    for cam in cameras:
        infos.extend(cam.infos)
        clips.extend(cam.clips)
    return infos, clips


def compute_master_offsets(
    alignments: list[AlignmentMap | None],
    durations: list[float],
    clip_camera: list[int] | None = None,
) -> tuple[list[float], list[int]]:
    """Place each clip on the master timeline (recorder seconds) from its
    matched recorder start time ``-offset/k``, anchored so the earliest clip
    sits at 0.

    A clip with no accepted alignment has NO measured position. It is placed
    after the previous clip OF ITS OWN CAMERA and reported, so the caller can
    mark it unresolved. A single shared cursor (the previous behaviour) put
    camera B's first unplaceable clip after camera A's last clip — a position
    invented out of an unrelated camera's running time, presented on the
    timeline exactly like a measured one. ``clip_camera`` (camera index per
    clip) is optional only so existing callers/tests keep working; without it
    every clip is treated as one camera.

    Returns (offsets, unaligned_indices).
    """
    rec_starts: list[float | None] = []
    for am in alignments:
        if am is not None and am.k != 0:
            rec_starts.append(-am.offset / am.k)
        else:
            rec_starts.append(None)

    aligned = [r for r in rec_starts if r is not None]
    ref = min(aligned) if aligned else 0.0
    cams = clip_camera if clip_camera is not None else [0] * len(durations)

    offsets: list[float] = []
    unaligned: list[int] = []
    prev_end: dict[int, float] = {}
    for i, (rs, dur, cam) in enumerate(zip(rec_starts, durations, cams, strict=True)):
        if rs is not None:
            off = rs - ref
        else:
            off = prev_end.get(cam, 0.0)
            unaligned.append(i)
        offsets.append(off)
        prev_end[cam] = off + dur
    return offsets, unaligned


def preliminary_offsets(video_clips: list[MediaClip], clip_camera: list[int]) -> list[float]:
    """Rough timeline positions before alignment: lay each camera's clips
    end-to-end in (natural) name order. Used only to populate the GUI timeline
    while transcription runs; real positions come from matched timecodes."""
    running: dict[int, float] = {}
    offsets: list[float] = []
    for clip, cam in zip(video_clips, clip_camera, strict=True):
        start = running.get(cam, 0.0)
        offsets.append(start)
        running[cam] = start + clip.duration
    return offsets


def sequence_order_warnings(
    video_clips: list[MediaClip],
    clip_camera: list[int],
    aligned: list[bool],
) -> list[str]:
    """Flag clips whose matched timeline order contradicts their filename order
    within a camera — a strong hint of a bad alignment for that clip."""
    warnings: list[str] = []
    by_cam: dict[int, list[int]] = {}
    for i, cam in enumerate(clip_camera):
        by_cam.setdefault(cam, []).append(i)
    for indices in by_cam.values():
        prev_off: float | None = None
        prev_name = ""
        for i in indices:  # indices are already in natural name order
            if not aligned[i]:
                continue
            off = video_clips[i].offset
            if prev_off is not None and off < prev_off - 1e-6:
                warnings.append(
                    f"{video_clips[i].path.name}: placed before {prev_name} despite later "
                    "filename — possible misalignment"
                )
            prev_off = off
            prev_name = video_clips[i].path.name
    return warnings


def recorder_word_gaps(rec_words: list[tuple[float, float]]) -> list[float]:
    """Midpoints of the silent gaps between consecutive recorder words, sorted.

    ``rec_words`` is a list of ``(start, end)`` word spans (need not be sorted).
    Used by ``clip_pieces`` to snap piece boundaries away from mid-word cuts —
    see ``_snap_to_word_gap``.
    """
    spans = sorted(rec_words)
    return [(a[1] + b[0]) / 2.0 for a, b in zip(spans, spans[1:], strict=False) if b[0] > a[1]]


def _snap_to_word_gap(rec_time: float, gaps: list[float], max_snap_s: float) -> float:
    """Nudge ``rec_time`` to the nearest word-gap midpoint within ``max_snap_s``.

    A piece boundary that falls in the middle of a spoken word (rather than in
    the silence between words) creates an audible mid-word tempo break — a
    stutter like "подготовил" -> "подга-га-товил" when the neighbouring piece's
    atempo factor differs. Snapping the cut point to the nearest inter-word
    silence removes the artifact without touching any piece's tempo factor
    (unlike the old, now-removed, factor-smoothing approach, which fixed the
    stutter by averaging factors but let speech drift off the picture by up to
    ~1.4s). Returns ``rec_time`` unchanged if no gap is close enough.
    """
    if not gaps:
        return rec_time
    import bisect

    i = bisect.bisect_left(gaps, rec_time)
    candidates = [
        g
        for g in (gaps[i - 1] if i > 0 else None, gaps[i] if i < len(gaps) else None)
        if g is not None
    ]
    if not candidates:
        return rec_time
    best = min(candidates, key=lambda g: abs(g - rec_time))
    return best if abs(best - rec_time) <= max_snap_s else rec_time


def _sentence_blocks(
    rec_words: list[tuple[float, float]], min_pause_s: float
) -> list[tuple[float, float]]:
    """Group recorder words into sentences: a pause of at least ``min_pause_s``
    between consecutive words ends a sentence. Returns ``(start, end)`` spans in
    recorder time, sorted. This is the acoustic definition of a sentence — a
    stretch of speech with no safe cut point inside it — which is exactly what
    the renderer needs (punctuation without an actual pause is not cuttable)."""
    blocks: list[tuple[float, float]] = []
    for start, end in sorted(rec_words):
        if blocks and start - blocks[-1][1] < min_pause_s:
            blocks[-1] = (blocks[-1][0], max(blocks[-1][1], end))
        else:
            blocks.append((start, end))
    return blocks


# Sentence-mode rendering constants (strategy 3). The pad keeps a hair of room
# tone attached to each sentence so the cut never clips a consonant's attack;
# it's safe because sentences are separated by at least phrase_gap_threshold
# (>= 2x the pad). The map window is the span of anchors each smoothed-map
# evaluation sees — wide enough to average Whisper's ±50-100 ms word-timing
# jitter down to a few ms, narrow enough to track genuine non-linear drift.
_SENTENCE_PAD_S = 0.08
_MAP_WINDOW_S = 30.0
_PAUSE_FACTOR_MIN = 0.5
_PAUSE_FACTOR_MAX = 2.0
_MIN_PIECE_S = 0.02

# Report a dropped head longer than this. See `head_trim_seconds`.
HEAD_TRIM_WARN_S = 1.0


def _smoothed_map_at(
    am: AlignmentMap, rec_t: float, window_s: float = _MAP_WINDOW_S
) -> tuple[float, float]:
    """The smoothed drift map evaluated at recorder time ``rec_t``: returns
    ``(cam_time, local_rate)``.

    A weighted local linear regression over the anchors within ``±window_s``
    (tricube weights) — individual anchors carry Whisper's word-timing jitter,
    but a 30-second neighbourhood averages it down to a few milliseconds while
    still following genuine non-linear drift. Falls back to the global RANSAC
    line where the neighbourhood is too thin to fit."""
    k = am.k or 1.0
    near = [(a.rec_time, a.cam_time) for a in am.anchors if abs(a.rec_time - rec_t) <= window_s]
    if len(near) >= 4:
        rec = np.array([p[0] for p in near])
        cam = np.array([p[1] for p in near])
        span = float(rec.max() - rec.min())
        if span >= 5.0:
            w = (1.0 - (np.abs(rec - rec_t) / window_s) ** 3) ** 3
            slope, intercept = np.polyfit(rec, cam, 1, w=np.maximum(w, 1e-6))
            # A locally insane slope (all anchors bunched + jitter) must never
            # leak into a speech factor; keep it within a sane drift range.
            if 0.9 <= slope <= 1.1:
                return float(intercept + slope * rec_t), float(slope)
    return am.offset + k * rec_t, k


def _sentence_pieces(
    am: AlignmentMap,
    rec0: float,
    rec1: float,
    rec_words: list[tuple[float, float]],
    config: WhisperSyncConfig,
) -> tuple[float, list[tuple[float, float, float]]] | None:
    """Sentence-wise piece plan (strategy 3): cut ONLY between sentences, warp
    speech ONLY at the smoothed drift rate, absorb ALL placement residue in the
    inter-sentence pauses.

    Pieces alternate [pause][sentence][pause][sentence]...[tail], tiling
    ``[rec0, rec1]`` contiguously (no content gap or overlap anywhere — repeats
    are impossible by construction):

    - a SENTENCE piece spans one uncuttable stretch of speech (plus a small
      room-tone pad on each side); its factor is the smoothed local drift rate
      — a fraction of a percent, rendered as a transparent resample. Anchor
      jitter never reaches a speech factor.
    - a PAUSE piece is stationary room tone between sentences; its factor is
      whatever places the NEXT sentence exactly on its smoothed target
      (clamped to [0.5, 2.0] — stretching room tone is inaudible where
      stretching speech is not). Placement error therefore dies in every
      pause instead of accumulating.

    Returns ``None`` when no sentence overlaps the span (caller falls back to
    a single global piece).
    """
    k = am.k or 1.0
    blocks = [
        (max(s, rec0), min(e, rec1))
        for s, e in _sentence_blocks(rec_words, config.phrase_gap_threshold)
        if e > rec0 and s < rec1
    ]
    blocks = [(s, e) for s, e in blocks if e - s > _MIN_PIECE_S]
    if not blocks:
        return None

    # Pad each sentence into the surrounding pause (never past the neighbour).
    padded: list[tuple[float, float]] = []
    for i, (s, e) in enumerate(blocks):
        lo = blocks[i - 1][1] if i > 0 else rec0
        hi = blocks[i + 1][0] if i + 1 < len(blocks) else rec1
        padded.append((max(s - _SENTENCE_PAD_S, lo, rec0), min(e + _SENTENCE_PAD_S, hi, rec1)))

    # Smoothed target position + local rate for every sentence start.
    targets: list[tuple[float, float]] = [_smoothed_map_at(am, s) for s, _e in padded]

    pieces: list[tuple[float, float, float]] = []
    lead = 0.0
    local = 0.0  # running output (camera-local) time after `lead`

    # Head room tone before the first sentence: stretch it to put sentence 0 on
    # target; trim it (cutting silence is free) when even max compression can't
    # fit, pad with lead silence when there isn't enough of it.
    head_in = padded[0][0] - rec0
    head_out = max(targets[0][0], 0.0)
    head_start = rec0
    if head_out <= _MIN_PIECE_S:
        head_in = 0.0
    elif head_in > _MIN_PIECE_S:
        # Whatever will not fit in the available output at maximum compression
        # is necessarily left out — the geometry allows nothing else. See
        # `head_trim_seconds`: the caller reports how much was dropped, because
        # "no transcribed words here" is not the same as "silence here".
        used = min(head_in, head_out * _PAUSE_FACTOR_MAX)
        head_start = padded[0][0] - used
        factor = max(_PAUSE_FACTOR_MIN, min(_PAUSE_FACTOR_MAX, used / head_out))
        out = used / factor
        lead = max(0.0, head_out - out)
        pieces.append((head_start, used, factor))
        local = out
    else:
        lead = head_out

    for j, (s, e) in enumerate(padded):
        if j > 0:
            # Pause piece between sentence j-1 and j: absorb the residue.
            pause_in = s - padded[j - 1][1]
            needed = max(targets[j][0] - lead - local, _MIN_PIECE_S)
            if pause_in > _MIN_PIECE_S:
                factor = max(_PAUSE_FACTOR_MIN, min(_PAUSE_FACTOR_MAX, pause_in / needed))
                out = pause_in / factor
                pieces.append((padded[j - 1][1], pause_in, factor))
                local += out
        # Sentence piece: transparent conform at the smoothed local rate only.
        rate = targets[j][1]
        in_dur = e - s
        factor = max(0.5, min(2.0, 1.0 / rate if rate else 1.0))
        pieces.append((s, in_dur, factor))
        local += in_dur / factor

    # Tail room tone after the last sentence, at the global rate (the assembly
    # pads/trims to the exact clip length anyway).
    tail_in = rec1 - padded[-1][1]
    if tail_in > _MIN_PIECE_S:
        pieces.append((padded[-1][1], tail_in, max(0.5, min(2.0, k and 1.0 / k or 1.0))))

    return lead, pieces


def _retake_groups_for_clip(
    am: AlignmentMap,
    clip_duration: float,
    rec_duration: float,
    rec_transcript_words: list[Word],
    config: WhisperSyncConfig,
) -> list[RetakeGroup]:
    """Retake groups for one camera clip, converted to the clip's own LOCAL
    time (seconds from the clip's own start) so they can be attached directly
    to its rendered voice ``MediaClip``.

    Detection runs on the recorder's full transcript restricted to this clip's
    aligned span ``[rec0, rec1]`` (the same span ``clip_pieces`` warps), so a
    retake found here genuinely occurred while this clip was rolling. Take
    spans are placed via the alignment's global affine map (``offset + k *
    rec_time``) — the same anchor ``clip_pieces`` uses for its own piece
    edges. This is an approximation under the piecewise (sentence-wise) warp
    used by strategy 3, which can nudge actual playback position by up to a
    per-phrase factor's worth of drift (real clock drift is a fraction of a
    percent, so in practice this is well under a second) — acceptable for a
    non-destructive editorial hint the user reviews in Final Cut before
    committing to any cut, rather than a frame-exact placement.
    """
    if not config.detect_retakes:
        return []
    k = am.k or 1.0
    rec0 = min(max((0.0 - am.offset) / k, 0.0), rec_duration)
    rec1 = min(max((clip_duration - am.offset) / k, 0.0), rec_duration)
    if rec1 - rec0 <= 1e-3:
        return []
    words = [w for w in rec_transcript_words if rec0 <= w.start < rec1]
    groups = detect_retakes(words, config)
    if not groups:
        return []

    def r2l(t_rec: float) -> float:
        return am.offset + k * t_rec

    local_groups: list[RetakeGroup] = []
    for g in groups:
        takes = [Take(start=r2l(t.start), end=r2l(t.end), text=t.text) for t in g.takes]
        local_groups.append(RetakeGroup(takes=takes, keeper_index=g.keeper_index))
    return local_groups


def _retake_groups_in_segment(
    groups: list[RetakeGroup] | None, seg_start: float, seg_end: float
) -> list[RetakeGroup]:
    """Re-base retake groups onto one N-minute voice segment's own local time.

    Called once per output segment when ``voice_segment_minutes`` splits a
    clip's rendered voice monolith (see the segmentation pass in
    ``run_pipeline``): a group is kept only if it falls ENTIRELY within
    ``[seg_start, seg_end)`` — a group straddling a segment boundary can't be
    represented as a single audition in either segment, and splitting it
    there would be rarer still since segment cuts are themselves snapped to
    silence, i.e. away from speech. Take times shift so 0 means this
    segment's own start.
    """
    if not groups:
        return []
    kept: list[RetakeGroup] = []
    for g in groups:
        g_start, g_end = g.span
        if g_start < seg_start or g_end > seg_end:
            continue
        kept.append(
            RetakeGroup(
                takes=[Take(t.start - seg_start, t.end - seg_start, t.text) for t in g.takes],
                keeper_index=g.keeper_index,
            )
        )
    return kept


# atempo's usable range. A piece outside it is not "a piece that needs
# clamping" — it is evidence that the two anchors bounding it disagree.
MIN_TEMPO_FACTOR = 0.5
MAX_TEMPO_FACTOR = 2.0


def _drop_implausible_breakpoints(
    bps: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Remove interior breakpoints whose piece would need an out-of-range tempo.

    Removing a breakpoint merges the two pieces it separated, so the surviving
    plan still tiles exactly the same recorder span onto exactly the same
    output span — the invariant a clamp destroys. Repeats until every remaining
    piece is plausible or only the two clip edges are left (which is the Global
    Linear fallback, and is always self-consistent).
    """
    if len(bps) <= 2:
        return bps
    kept = list(bps)
    changed = True
    while changed and len(kept) > 2:
        changed = False
        for i in range(len(kept) - 1):
            (ra, la), (rb, lb) = kept[i], kept[i + 1]
            in_dur, out_dur = rb - ra, lb - la
            factor = float("inf") if out_dur <= 1e-4 else in_dur / out_dur
            if MIN_TEMPO_FACTOR <= factor <= MAX_TEMPO_FACTOR:
                continue
            # Drop whichever endpoint of this piece is interior; prefer the
            # later one so earlier (already-validated) pieces stay put.
            drop = i + 1 if i + 1 < len(kept) - 1 else i
            if drop == 0 or drop == len(kept) - 1:
                # Both endpoints are clip edges: nothing left to drop. The
                # global stretch itself is out of range, which the caller
                # surfaces via validate_pieces.
                return kept
            del kept[drop]
            changed = True
            break
    return kept


def validate_pieces(
    lead: float,
    pieces: list[tuple[float, float, float]],
    clip_duration: float,
    rec_duration: float,
    tol_s: float = 1e-3,
) -> list[str]:
    """Structural problems in a render plan, as human-readable strings.

    The invariants a piece list must satisfy for the rendered audio to land
    where the plan says it does:

    * every source duration and tempo factor is positive and finite;
    * every piece reads inside the recorder;
    * the output tiles ``[lead, lead + sum(in_dur / factor)]`` without exceeding
      the clip — ``sum(in_dur / factor)`` is the ONLY thing that decides where
      the last piece's speech lands, so a plan whose total output length
      disagrees with the clip has already lost sync somewhere in the middle.

    Returns an empty list when the plan is sound. Used as a runtime guard and
    directly in tests, where checking "the boundary moved" is not the same
    question as "the audio ends up in the right place".
    """
    problems: list[str] = []
    if lead < -tol_s:
        problems.append(f"negative lead silence ({lead:.4f}s)")
    total_out = lead
    for i, (rs, rd, factor) in enumerate(pieces):
        if not (rd > 0 and factor > 0):
            problems.append(f"piece {i}: non-positive duration/factor ({rd:.4f}s, {factor:.4f})")
            continue
        if not (MIN_TEMPO_FACTOR - 1e-9 <= factor <= MAX_TEMPO_FACTOR + 1e-9):
            problems.append(f"piece {i}: tempo factor {factor:.4f} outside atempo range")
        if rs < -tol_s or rs + rd > rec_duration + tol_s:
            problems.append(
                f"piece {i}: reads [{rs:.3f}, {rs + rd:.3f}]s outside the "
                f"{rec_duration:.3f}s recorder"
            )
        total_out += rd / factor
    if total_out > clip_duration + tol_s:
        problems.append(
            f"pieces occupy {total_out:.4f}s of a {clip_duration:.4f}s clip "
            f"(overflow {total_out - clip_duration:.4f}s)"
        )
    return problems


def head_trim_seconds(pieces: list[tuple[float, float, float]], rec0: float) -> float:
    """Recorder seconds the plan never reads, before its first piece.

    The sentence planner places the first recognised sentence on its target and
    compresses the room tone before it; whatever cannot fit even at maximum
    compression is left out. That is forced by the geometry — there is nowhere
    else for it to go — but it is NOT necessarily silence. Whisper drops quiet,
    accented or overlapping speech, so a dropped head can contain a real
    opening phrase, and the difference between "trimmed room tone" and "deleted
    speech" is invisible from inside the planner (which has no audio, only word
    timings). Measuring it lets the caller say so rather than the user
    discovering a missing sentence in the edit.
    """
    if not pieces:
        return 0.0
    return max(0.0, pieces[0][0] - rec0)


def clip_pieces(
    am: AlignmentMap,
    clip_duration: float,
    rec_duration: float,
    strategy_id: int,
    config: WhisperSyncConfig,
    rec_word_gaps: list[float] | None = None,
    rec_words: list[tuple[float, float]] | None = None,
) -> tuple[float, list[tuple[float, float, float]]]:
    """Contiguous recorder pieces that tile a camera clip, for a continuous warp.

    Returns ``(lead_silence, pieces)`` where each piece is
    ``(rec_start, rec_in_duration, atempo_factor)`` and pieces are in playback
    order with no gaps between them — the recorder span for the clip is simply
    time-stretched (globally or piecewise between sync points) so its speech
    lands under the picture. ``lead_silence`` is the silence (seconds) before the
    first piece, non-zero only when the recorder does not reach the clip start.

    Strategy controls the breakpoint density: 1 = one global stretch (Global
    Linear), 2 = a piece per anchor (Local Time-Stretch, tightest), 3 = a piece
    per phrase (Hybrid — gentle per-phrase stretch, smoother, fewer seams; the
    recommended default).

    ``rec_word_gaps`` (recorder inter-word silence midpoints, from
    ``recorder_word_gaps``) lets interior breakpoints snap away from mid-word
    cuts — see ``_snap_to_word_gap``. Optional so callers/tests that don't have
    a transcript handy can omit it (breakpoints then land exactly on anchors,
    as before).
    """
    k = am.k or 1.0

    def l2r(t_local: float) -> float:
        return (t_local - am.offset) / k

    def r2l(t_rec: float) -> float:
        return am.offset + k * t_rec

    rec0 = min(max(l2r(0.0), 0.0), rec_duration)
    rec1 = min(max(l2r(clip_duration), 0.0), rec_duration)
    if rec1 - rec0 <= 1e-3:
        return 0.0, []

    # Interior breakpoints come from the REAL matched word times — each is
    # (recorder_time, local_clip_time), where local time is the anchor's camera
    # time. Warping between these tracks the actual (non-linear) drift instead of
    # a single global slope.
    pts = sorted(
        (a.rec_time, a.cam_time)
        for a in am.anchors
        if rec0 < a.rec_time < rec1 and 0.0 <= a.cam_time <= clip_duration
    )

    # Strategy 1 (or no usable anchors): one global stretch across the clip.
    if strategy_id == 1 or not pts:
        out_dur = r2l(rec1) - r2l(rec0)
        lead = max(0.0, r2l(rec0))
        if out_dur <= 1e-3:
            return lead, []
        return lead, [(rec0, rec1 - rec0, (rec1 - rec0) / out_dur)]

    # Strategy 3 (Hybrid): sentence-wise rendering — cut ONLY in the real
    # pauses between sentences, conform speech ONLY at the smoothed drift
    # rate, absorb all placement residue in the pause pieces. Falls back to
    # the anchor-thinning path when the recorder's word list isn't available
    # (older callers/tests).
    if strategy_id == 3 and rec_words:
        sentence_plan = _sentence_pieces(am, rec0, rec1, rec_words, config)
        if sentence_plan is not None:
            return sentence_plan

    if strategy_id == 3:
        spacing = max(config.phrase_gap_threshold, 1.0)
        thinned: list[tuple[float, float]] = []
        for rt, ct in pts:
            if not thinned or rt - thinned[-1][0] >= spacing:
                thinned.append((rt, ct))
        pts = thinned

    # Snap each interior breakpoint's recorder time to the nearest inter-word
    # silence (seam-snap-to-silence), so no piece boundary lands mid-word. The
    # camera-time side moves WITH it (scaled by the clip's global rate): moving
    # only the recorder side used to change one neighbour's input length while
    # both output lengths stayed put, kicking the two adjacent tempo factors
    # apart by up to ±30% — an audible tempo see-saw at every snapped seam.
    if rec_word_gaps:
        snapped: list[tuple[float, float]] = []
        for rt, ct in pts:
            nrt = _snap_to_word_gap(rt, rec_word_gaps, config.seam_snap_max_s)
            snapped.append((nrt, ct + (nrt - rt) * k))
        pts = sorted(snapped)

    # Clip edges use the global line; interior uses matched word times. Keep only
    # strictly-increasing (rec, local) breakpoints so every piece is sane.
    raw_bps = [(rec0, r2l(rec0)), *pts, (rec1, r2l(rec1))]
    bps: list[tuple[float, float]] = []
    for rt, lt in raw_bps:
        if not bps or (rt > bps[-1][0] + 1e-3 and lt > bps[-1][1] + 1e-3):
            bps.append((rt, lt))

    # An interior breakpoint implying a tempo outside atempo's usable range is
    # not a piece to be clamped — it is an anchor pair that disagrees with its
    # neighbours, i.e. a bad match. Clamping it kept the WRONG breakpoint and
    # silently broke the timeline geometry instead: a piece asked to play
    # `in_dur` of recorder at a clamped factor no longer occupies `out_dur` of
    # output, so the pieces stopped tiling the clip (a synthetic 10 s clip came
    # out 9.8 s long) and the trailing pad papered over the gap without putting
    # any speech back where it belonged. Dropping the breakpoint MERGES its two
    # neighbours, which keeps `sum(in_dur / factor) == total output` exact by
    # construction. See ``validate_pieces``.
    bps = _drop_implausible_breakpoints(bps)

    pieces: list[tuple[float, float, float]] = []
    for i in range(len(bps) - 1):
        (ra, la), (rb, lb) = bps[i], bps[i + 1]
        in_dur = rb - ra
        out_dur = lb - la
        if in_dur <= 1e-4 or out_dur <= 1e-4:
            continue
        pieces.append((ra, in_dur, in_dur / out_dur))

    lead = max(0.0, bps[0][1])
    return lead, pieces


def _silence_spans(
    word_times: list[tuple[float, float]], track_duration: float, gap_threshold: float
) -> list[tuple[float, float]]:
    """Silence spans (no words) in a track: before the first word, between words
    farther apart than ``gap_threshold``, and after the last word. Unsorted input
    is sorted first; overlapping/out-of-order words are tolerated."""
    words = sorted(word_times)
    if not words:
        return [(0.0, track_duration)] if track_duration > 0 else []
    spans: list[tuple[float, float]] = []
    if words[0][0] > 0:
        spans.append((0.0, words[0][0]))
    prev_end = words[0][1]
    for start, end in words[1:]:
        if start - prev_end > gap_threshold:
            spans.append((prev_end, start))
        prev_end = max(prev_end, end)
    if track_duration - prev_end > 0:
        spans.append((prev_end, track_duration))
    return spans


def _intersect_spans(
    a_spans: list[tuple[float, float]], b_spans: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Overlap of two lists of [start, end) spans (each internally non-overlapping
    and sorted, as produced by ``_silence_spans``)."""
    out: list[tuple[float, float]] = []
    i = j = 0
    while i < len(a_spans) and j < len(b_spans):
        a0, a1 = a_spans[i]
        b0, b1 = b_spans[j]
        lo, hi = max(a0, b0), min(a1, b1)
        if hi > lo:
            out.append((lo, hi))
        if a1 < b1:
            i += 1
        else:
            j += 1
    return out


def pause_spans_local(
    am: AlignmentMap,
    clip_duration: float,
    gap_threshold: float,
    min_pause: float,
    cam_words: list[tuple[float, float]] | None = None,
    rec_words: list[tuple[float, float]] | None = None,
    rec_duration: float | None = None,
) -> list[tuple[float, float]]:
    """Inter-phrase pause spans in LOCAL (camera) time for the rendered clip —
    where it is safe to duck because BOTH tracks are silent.

    When ``cam_words``/``rec_words`` (full word spans, not just matched anchors)
    are given, a pause is the overlap of the camera's silence and the recorder's
    silence (recorder words projected to local time via ``am``). This avoids
    ducking real speech that simply failed to become an anchor (low confidence,
    a word Whisper missed, or speech before the first/after the last matched
    word) — see PROJECT_ANALYSIS.md §2.5. Falls back to the old anchor-gap
    heuristic when word lists aren't available (e.g. existing callers/tests).
    Spans shorter than ``min_pause`` are dropped; output is clamped to
    ``[0, clip_duration]`` and sorted.
    """
    if cam_words is not None and rec_words is not None:
        cam_silence = _silence_spans(cam_words, clip_duration, gap_threshold)
        k = am.k or 1.0
        # The map is t_cam = offset + k * t_rec, so recorder -> local is
        # `offset + k*t`, NOT `(t - offset)/k` (that is the INVERSE, local ->
        # recorder). With the inverse, a recorder word at 104-105 s under
        # offset=-100, k=1 — which really lands at camera 4-5 s — was projected
        # to camera 204-205 s, i.e. outside the clip, so its speech left no
        # silence gap and the whole 3-8 s stretch was marked "pause" and
        # ducked. Real speech was attenuated by 18 dB. The bug is invisible on
        # an identity map (offset 0, k 1), which is what the old tests used.
        rec_local = [(am.offset + k * s, am.offset + k * e) for s, e in rec_words]
        rec_local_duration = (
            am.offset + k * rec_duration if rec_duration is not None else clip_duration
        )
        rec_silence_local = _silence_spans(
            [(min(s, e), max(s, e)) for s, e in rec_local], rec_local_duration, gap_threshold
        )
        spans = _intersect_spans(cam_silence, rec_silence_local)
    else:
        cam_times = sorted(a.cam_time for a in am.anchors)
        if not cam_times:
            return []
        spans = []
        if cam_times[0] > min_pause:
            spans.append((0.0, cam_times[0]))
        for t0, t1 in zip(cam_times, cam_times[1:], strict=False):
            if t1 - t0 > gap_threshold:
                spans.append((t0, t1))
        if clip_duration - cam_times[-1] > min_pause:
            spans.append((cam_times[-1], clip_duration))

    out: list[tuple[float, float]] = []
    for a, b in spans:
        a = max(0.0, a)
        b = min(clip_duration, b)
        if b - a >= min_pause:
            out.append((a, b))
    return out


def resolve_workers(requested: int) -> int:
    """Number of parallel render processes: ``requested`` if >0, else os.cpu_count()."""
    if requested and requested > 0:
        return requested
    return max(1, os.cpu_count() or 1)


def _pool_context() -> Any:
    """Multiprocessing context for the render pool. Never ``fork``.

    Forking a process that has ANY other thread is a classic deadlock source:
    the child inherits a snapshot of the other threads' malloc/logging/CUDA
    lock state, with no thread alive to release it. The old heuristic —
    ``threading.active_count() == 1`` — cannot see the threads that matter.
    It counts PYTHON threads only, so Qt's own event/worker threads, and the
    native thread pools inside CTranslate2 and CUDA, are all invisible to it:
    probed from inside a real ``QThread`` the count was 1, and ``fork`` was
    duly selected in exactly the situation the check existed to prevent.

    There is no reliable way to ask "is this process really single-threaded",
    so the answer is to stop asking. ``forkserver`` gets the same cheap startup
    from a template process that is genuinely single-threaded by construction,
    and ``spawn`` is the portable fallback. The one shared pool per run
    amortises the difference. Python 3.12+ warns about fork-in-threads and 3.14
    changed the Linux default away from it for the same reason.
    """
    for method in ("forkserver", "spawn"):
        try:
            return mp.get_context(method)
        except ValueError:  # pragma: no cover - platform dependent
            continue
    return mp.get_context()  # pragma: no cover - platform dependent


_SEAM_CONTIGUITY_TOL_S = 1e-3


def _piece_seam_fades(pieces: list[tuple[float, float, float]]) -> list[tuple[bool, bool]]:
    """Per-piece ``(fade_in, fade_out)`` flags from recorder-time contiguity.

    Two consecutive pieces are acoustically continuous when the second's
    ``rec_start`` picks up exactly where the first's ``rec_start + rec_dur``
    left off — the usual case for ``clip_pieces``, since its breakpoints tile
    the recorder span with no gaps. Only a genuine discontinuity (a piece that
    doesn't start where its neighbour ended — e.g. after Boundary Flex nudges a
    boundary) gets a fade on that edge, so the fade never carves an audible
    dip into an otherwise-continuous recording. See PROJECT_ANALYSIS.md §2.0.
    """
    n = len(pieces)
    flags: list[tuple[bool, bool]] = []
    for i, (rs, rd, _factor) in enumerate(pieces):
        prev_end = pieces[i - 1][0] + pieces[i - 1][1] if i > 0 else None
        next_start = pieces[i + 1][0] if i + 1 < n else None
        fade_in = prev_end is None or abs(rs - prev_end) > _SEAM_CONTIGUITY_TOL_S
        fade_out = next_start is None or abs((rs + rd) - next_start) > _SEAM_CONTIGUITY_TOL_S
        flags.append((fade_in, fade_out))
    return flags


def _render_pieces_sequential(
    pieces: list[tuple[float, float, float]],
    rec_path: Path,
    tmp_dir: Path,
    fade_ms: int,
    sample_rate: int = 48000,
    channels: int = 1,
    codec: str = "pcm_s24le",
    stretch_method: str = "auto",
    cancel_event: Any = None,
) -> list[Path]:
    """Render every piece to its own indexed WAV, one after another, and return
    the paths in piece order — the ``render_workers <= 1`` path. (With more
    workers, ``run_pipeline`` submits pieces of ALL jobs to one shared process
    pool instead; see the render phase there and PROJECT_ANALYSIS.md §6.4.)

    Fades are applied only on seams that are not acoustically contiguous (see
    ``_piece_seam_fades``). ``cancel_event`` (a ``threading.Event``), if given
    and set, raises ``InterruptedError`` between pieces — this doesn't kill an
    already-running ffmpeg subprocess, but it stops starting new ones, which is
    the practical bulk of the wait. See PROJECT_ANALYSIS.md §3.5.
    """
    fades = _piece_seam_fades(pieces)
    results: list[Path] = []
    for k, ((rs, rd, fac), (fi, fo)) in enumerate(zip(pieces, fades, strict=True)):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")
        results.append(
            render_piece(
                rec_path,
                tmp_dir,
                rs,
                rd,
                fac,
                k,
                fade_ms,
                fade_in=fi,
                fade_out=fo,
                sample_rate=sample_rate,
                channels=channels,
                codec=codec,
                stretch_method=stretch_method,
            )
        )
    return results


def _submit_piece_jobs(
    pool: ProcessPoolExecutor,
    pieces: list[tuple[float, float, float]],
    rec_path: Path,
    tmp_dir: Path,
    fade_ms: int,
    sample_rate: int,
    channels: int,
    codec: str,
    stretch_method: str,
) -> list[Any]:
    """Submit one ``render_piece`` task per piece to the shared pool and return
    the futures in piece order. ``render_piece`` lives in ``timestretch`` (a
    module with only stdlib imports), so non-fork workers unpickling the task
    import that light module — not this one and not the ML stack."""
    fades = _piece_seam_fades(pieces)
    return [
        pool.submit(
            render_piece,
            rec_path,
            tmp_dir,
            rs,
            rd,
            fac,
            k,
            fade_ms,
            fade_in=fi,
            fade_out=fo,
            sample_rate=sample_rate,
            channels=channels,
            codec=codec,
            stretch_method=stretch_method,
        )
        for k, ((rs, rd, fac), (fi, fo)) in enumerate(zip(pieces, fades, strict=True))
    ]


def _collect_piece_futures(
    futures: list[Any], cancel_event: Any = None, poll_s: float = 0.5
) -> list[Path]:
    """Gather rendered piece paths from ``futures`` in submit (= piece) order.

    Waits with a short timeout so ``cancel_event`` is honoured mid-job even
    while pieces are rendering in pool workers — the old per-job pool only
    checked cancellation on the sequential path, so cancelling during a big
    multi-core render job silently waited for the whole job to finish.

    Completion is decided by ``future.done()``, NOT by catching
    ``TimeoutError``. Those are two different events that Python spells the
    same way: ``concurrent.futures.TimeoutError`` is ``TimeoutError`` on 3.11+,
    so a worker whose own task raised ``TimeoutError`` (an ffmpeg call that
    exceeded its limit, say) was indistinguishable from "the 0.5 s wait
    elapsed" — the loop treated a permanently failed piece as still pending and
    spun on it forever. Asking whether the future is done first makes the
    distinction unambiguous, and a worker exception then re-raises here, on the
    piece that failed.
    """
    results: list[Path] = []
    for fut in futures:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Cancelled by user")
            if fut.done():
                # Re-raises the worker's own exception, whatever its type.
                results.append(fut.result())
                break
            try:
                fut.result(timeout=poll_s)
            except FutureTimeoutError:
                # Genuinely still running: `done()` was False before the wait.
                continue
            except BaseException:
                # The task failed; let `done()` above hand the real exception
                # to the caller on the next pass.
                continue
    return results


def _timeline_end(clips: list[MediaClip]) -> float:
    return max((c.offset + c.duration for c in clips), default=0.0)


def normalize_plan_origin(clips: list[MediaClip]) -> float:
    """Shift the whole plan so nothing starts before time zero. Returns the shift.

    A negative ``camera_av_offset_ms`` asks for the synced voice to run AHEAD
    of the picture, which puts the first audio clip at a negative timeline
    offset. Nothing on a timeline can start before zero, so both serializers
    independently clamped it — ``mix_clips_on_timeline`` with
    ``max(0, delay_ms)`` and the FCPXML exporter with ``max(0.0, rel)``. Two
    independent clamps are two chances to disagree, and both are wrong the same
    way: clamping DROPS the requested lead rather than delivering it, so a
    -100 ms calibration produced exactly the un-calibrated result while
    reporting that it had been applied.

    Moving the ORIGIN instead preserves every clip's position relative to every
    other, which is the only thing that matters for sync, and leaves nothing
    for a downstream clamp to do. Applied once, here, to the whole plan.
    """
    if not clips:
        return 0.0
    earliest = min(c.offset for c in clips)
    if earliest >= 0.0:
        return 0.0
    shift = -earliest
    for clip in clips:
        clip.offset += shift
    logger.info(
        "Timeline origin shifted by %+.3fs so no clip starts before zero "
        "(negative A/V calibration)",
        shift,
    )
    return shift


# Extra local-time margin added on each side of a flagged span before
# re-rendering it: the span itself is only where the MISMATCH was detected,
# not necessarily the full extent of the affected audio (a shifted run's
# first/last word already carries some of the delta) — padding out to a
# nearby word gap avoids re-cutting exactly at the edge of the bad material.
_REPAIR_MARGIN_S = 1.0


def _repair_span(
    span: Any,  # self_check.SelfCheckSpan; typed loosely to avoid an import cycle at module load
    job: Any,  # RenderJob (a locally-defined class; see run_pipeline)
    monolith_path: Path,
    monolith_duration: float,
    cam_words: list[Word],
    rec_words: list[Word],
    config: WhisperSyncConfig,
    out_sr: int,
    tmp_dir: Path,
) -> Path | None:
    """Re-render just the audio covering ``span`` and splice it into an
    already-assembled voice monolith, returning the path to the repaired
    monolith (a new file; ``monolith_path`` itself is left untouched) or
    ``None`` if no repair could be made (the caller then leaves the span as
    a plain warning instead of risking a worse edit).

    The repair re-derives where in the recorder this stretch of speech
    actually comes from (``self_check.realign_span``) rather than touching
    only timing: an ffmpeg render is deterministic, so re-rendering the same
    recorder span verbatim reproduces a content defect byte-for-byte — only a
    fresh local alignment can fix either a `shifted` or a `content` span.
    """
    from whispersync.engine.self_check import realign_span

    new_am = realign_span(
        span,
        job.am,
        cam_words,
        rec_words,
        job.cam_audio,
        job.rec_path,
        job.rec_duration,
        config,
    )
    if new_am is None:
        return None

    # Splice boundaries snap to the CAMERA transcript's inter-word silences:
    # the re-rendered chunk's first/last piece carries a declick fade from
    # zero, so a splice landing mid-word would put an audible 10 ms dip inside
    # speech — in a word gap the same dip sits in room tone and disappears.
    lo = max(0.0, span.start - _REPAIR_MARGIN_S)
    hi = min(monolith_duration, span.end + _REPAIR_MARGIN_S)
    cam_gaps = recorder_word_gaps([(w.start, w.end) for w in cam_words])
    lo = min(_snap_to_word_gap(lo, cam_gaps, config.seam_snap_max_s), span.start)
    hi = max(_snap_to_word_gap(hi, cam_gaps, config.seam_snap_max_s), span.end)
    lo = max(0.0, lo)
    hi = min(monolith_duration, hi)
    if hi - lo <= 1e-3:
        return None

    # _sentence_pieces plans in the map's own LOCAL-time coordinates, assuming
    # they start at 0 (its head logic places sentence 0 at its absolute target
    # position) — so hand it a WINDOW-relative map: local time 0 = `lo`. Both
    # the offset and every anchor's cam_time shift by -lo; recorder times are
    # untouched.
    k = new_am.k or 1.0
    win_am = AlignmentMap(
        anchors=[
            Anchor(
                cam_time=a.cam_time - lo,
                rec_time=a.rec_time,
                token=a.token,
                confidence=a.confidence,
            )
            for a in new_am.anchors
        ],
        offset=new_am.offset - lo,
        k=k,
        residual_ms=new_am.residual_ms,
    )
    rec0 = max(0.0, (0.0 - win_am.offset) / k)
    rec1 = min(job.rec_duration, ((hi - lo) - win_am.offset) / k)
    if rec1 - rec0 <= 1e-3:
        return None

    rec_word_spans = [(w.start, w.end) for w in rec_words]
    plan = _sentence_pieces(win_am, rec0, rec1, rec_word_spans, config)
    if plan is None:
        out_dur = hi - lo
        plan = (0.0, [(rec0, rec1 - rec0, (rec1 - rec0) / out_dur)])
    lead, pieces = plan
    if not pieces:
        return None

    fade_ms = config.crossfade_ms if config.crossfade_enabled else 0
    seg_paths = _render_pieces_sequential(
        pieces,
        job.rec_path,
        tmp_dir,
        fade_ms,
        sample_rate=out_sr,
        channels=job.channels,
        codec=job.codec,
        stretch_method=config.stretch_method,
    )
    repaired_piece = tmp_dir / "repaired_span.wav"
    assemble_continuous(
        seg_paths, lead, hi - lo, out_sr, repaired_piece, channels=job.channels, codec=job.codec
    )

    before_path = tmp_dir / "before_span.wav"
    after_path = tmp_dir / "after_span.wav"
    segments = []
    if lo > 1e-3:
        cut_wav_segment(monolith_path, before_path, 0.0, lo, codec=job.codec)
        segments.append(before_path)
    segments.append(repaired_piece)
    if monolith_duration - hi > 1e-3:
        cut_wav_segment(monolith_path, after_path, hi, monolith_duration, codec=job.codec)
        segments.append(after_path)

    repaired_monolith = tmp_dir / f"repaired_{monolith_path.name}"
    assemble_continuous(
        segments,
        0.0,
        monolith_duration,
        out_sr,
        repaired_monolith,
        channels=job.channels,
        codec=job.codec,
    )
    return repaired_monolith


# Voice segmentation: how far a cut may wander from its nominal N-minute mark
# to find silence, and how short a final tail is allowed to be before it is
# merged into the previous segment instead of becoming its own tiny file.
_SEGMENT_SEARCH_S = 15.0
_SEGMENT_MIN_TAIL_S = 10.0


def _quiet_cut_points(voice_wav: Path, duration_s: float, segment_s: float) -> list[float]:
    """Cut points for splitting a rendered voice WAV into ~``segment_s`` pieces,
    each snapped to the quietest moment within ``±_SEGMENT_SEARCH_S`` of its
    nominal mark — a segment boundary must never land inside speech, because
    an NLE re-aligning each segment independently (the whole point of the
    feature) would turn a mid-word cut into an audible glitch.

    Energy is measured on a 16 kHz mono decode in 50 ms hops; every hop within
    10% of the window's noise floor counts as "silent enough", and among those
    the one closest to the nominal mark wins.
    """
    if segment_s <= 0 or duration_s <= segment_s + _SEGMENT_MIN_TAIL_S:
        return []
    from whispersync.engine.acoustic import load_mono16k_track

    track = load_mono16k_track(voice_wav)
    sr = 16000
    hop = int(0.05 * sr)
    n_hops = max(1, len(track) // hop)
    rms = np.array(
        [float(np.sqrt(np.mean(track[i * hop : (i + 1) * hop] ** 2))) for i in range(n_hops)]
    )
    # A cut must sit in a NEIGHBOURHOOD of silence, not in one quiet 50 ms hop
    # between two syllables: smooth the energy over ~250 ms before judging.
    if len(rms) >= 5:
        rms = np.convolve(rms, np.ones(5) / 5.0, mode="same")
    cuts: list[float] = []
    t = segment_s
    while t < duration_s - _SEGMENT_MIN_TAIL_S:
        lo = max(0, int((t - _SEGMENT_SEARCH_S) * sr / hop))
        hi = min(len(rms), int((t + _SEGMENT_SEARCH_S) * sr / hop))
        if hi <= lo:
            cuts.append(t)
        else:
            window = rms[lo:hi]
            floor = float(window.min())
            # "Silent enough" = within 15% of the way from the window's noise
            # floor to its MEDIAN (speech level) — strict enough to reject
            # decaying word tails, which a max-based threshold let through.
            thr = floor + 0.15 * (float(np.median(window)) - floor) + 1e-9
            good = np.flatnonzero(window <= thr)
            nominal_idx = int(t * sr / hop) - lo
            best = int(good[np.argmin(np.abs(good - nominal_idx))])
            cuts.append((lo + best) * hop / sr)
        t = cuts[-1] + segment_s
    return cuts


def _anchor_count(am: AlignmentMap | None) -> int:
    return len(am.anchors) if am is not None else 0


def _map_evidence(am: AlignmentMap | None) -> float:
    """How much a map is actually worth, comparably across provenances.

    ``len(anchors)`` is not that number. An acoustic map has no anchors at all
    and would score zero next to a two-anchor text fit that happens to be
    nonsense, which is how a working waveform match lost every contest it
    entered. Inliers times the recorder span they cover rewards evidence that
    is both plentiful AND spread out — the two things that make a line fit mean
    something — and is defined the same way for both kinds of map.
    """
    if am is None:
        return 0.0
    return float(max(am.inliers, len(am.anchors))) * (1.0 + am.evidence_span_s)


def _try_acoustic_fallback(
    clip_audio: Path,
    clip_duration: float,
    rec_path: Path,
    rec_duration: float,
    config: WhisperSyncConfig,
) -> AlignmentMap | None:
    """Acoustic fallback ("Strategy 0") for a clip the transcript couldn't
    align to this recorder: estimate offset/K directly from the waveforms via
    a GCC-PHAT scan covering the whole recorder. Returns an ``AlignmentMap``
    with ``provenance="acoustic"`` and an EMPTY anchor list (so ``clip_pieces``
    falls back to one global tempo conform — there are no text breakpoints to
    build a piecewise warp from), or ``None`` when the scan found no confident,
    unambiguous match.

    The empty anchor list is why ``provenance`` exists: code that measured a
    map's quality by ``len(anchors)`` scored every successful acoustic match as
    zero and discarded it, so this fallback never actually reached a render.
    The evidence the scan DID gather (points, inliers, span) rides along on the
    map instead, and ``matcher.evaluate_alignment`` judges it on those.

    ``clip_audio`` is the 16kHz mono WAV already extracted for transcription
    (reused here instead of re-extracting, same as Boundary Flex's cam_audio).
    """
    try:
        result = acoustic_coarse_align(
            clip_audio,
            rec_path,
            clip_duration=clip_duration,
            rec_duration=rec_duration,
            grid_s=config.acoustic_fallback_grid_s,
            window_s=config.acoustic_fallback_window_s,
            max_lag_s=config.acoustic_max_lag_s,
            min_sharpness=config.acoustic_fallback_min_sharpness,
            gcc_eps=config.gcc_eps,
            max_k_deviation=config.alignment_max_k_deviation,
            min_points=config.alignment_min_acoustic_points,
        )
    except (RuntimeError, ValueError, OSError):
        return None
    if result is None:
        return None
    return AlignmentMap(
        anchors=[],
        offset=result.offset,
        k=result.k,
        residual_ms=0.0,
        provenance="acoustic",
        inliers=result.inliers,
        evidence_span_s=result.span_s,
    )


def run_pipeline(
    config: WhisperSyncConfig,
    video_dir: Path,
    audio_files: list[Path],
    strategy_id: int,
    output_path: Path,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Any = None,
) -> SyncResult:
    """Run the full sync pipeline.

    ``cancel_event`` (a ``threading.Event``), if given, is checked between
    render pieces within a job — on the sequential path between pieces (see
    ``_render_pieces_sequential``) and on the pooled path while waiting for
    each piece future (see ``_collect_piece_futures``) — in addition to the
    existing between-job/stage cancellation that ``progress_callback``
    raising ``InterruptedError`` already provides. A job with hundreds of
    pieces was previously only cancellable once the WHOLE job finished. See
    PROJECT_ANALYSIS.md §3.5.
    """

    def _check_cancel() -> None:
        """Raise if the user asked to stop.

        Cancellation used to depend on the progress callback choosing to raise,
        which meant it only took effect where a callback happened to be
        installed and only between whole stages. Extraction, the Flex scan,
        enhancement, self-check and segmentation each ran to completion first,
        so pressing Cancel could appear to do nothing for many minutes. This is
        the one check, called on every stage transition and inside every long
        loop. It cannot interrupt a single in-flight ffmpeg or model
        invocation — those are bounded by their own timeouts — but it stops
        the pipeline from starting the next one.
        """
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Cancelled by user")

    def _notify(
        stage: str,
        progress: float = 0.0,
        message: str = "",
        clips: list[dict[str, Any]] | None = None,
    ) -> None:
        _check_cancel()
        if progress_callback is not None:
            progress_callback(
                PipelineProgress(stage=stage, progress=progress, message=message, clips=clips)
            )

    if not audio_files:
        raise ValueError("At least one recorder audio file is required.")

    engine: WhisperEngine | None = None
    cleanup_paths: list[Path] = []
    warnings: list[str] = []

    # One owner for this run's scratch and one lock over its output namespace.
    # Runs used to share `audio_synced/`, `.master/` and `enhance_tmp/` under
    # ffmpeg's `-y`, so a second run pointed at the same folder overwrote the
    # audio the first run's FCPXML still referenced, and either run's cleanup
    # deleted files the other was reading. Scratch now lives in a directory
    # unique to this run (on the output volume — piece WAVs are the size of the
    # final render, so /tmp is the wrong place for them), and a concurrent run
    # on the same output is refused with an explanation instead of silently
    # interleaving. See engine/workspace.py.
    with output_lock(output_path.parent), RunWorkspace(output_path.parent) as workspace:
        return _run_pipeline_locked(
            config=config,
            video_dir=video_dir,
            audio_files=audio_files,
            strategy_id=strategy_id,
            output_path=output_path,
            workspace=workspace,
            warnings=warnings,
            cleanup_paths=cleanup_paths,
            engine=engine,
            notify=_notify,
            check_cancel=_check_cancel,
            cancel_event=cancel_event,
        )


def _run_pipeline_locked(  # noqa: C901
    *,
    config: WhisperSyncConfig,
    video_dir: Path,
    audio_files: list[Path],
    strategy_id: int,
    output_path: Path,
    workspace: RunWorkspace,
    warnings: list[str],
    cleanup_paths: list[Path],
    engine: WhisperEngine | None,
    notify: Callable[..., None],
    check_cancel: Callable[[], None],
    cancel_event: Any,
) -> SyncResult:
    """The body of ``run_pipeline``, with the run's workspace and output lock
    already held. Split out only so the lock/scratch lifetime is expressed by a
    ``with`` block rather than by a hand-written ``finally`` that has to
    remember every temporary it created."""
    _notify = notify
    _check_cancel = check_cancel
    job_scratch_dirs: list[Path] = []

    try:
        # --- pre-flight: report unusable optional environments NOW ---
        # Both of these features run at the very END of the pipeline, so a
        # missing environment used to surface only after hours of
        # transcription and rendering ("voice enhancement failed — using
        # unenhanced audio" as the last line of a 3-hour run). The work is
        # still worth doing without them, so this warns rather than aborts —
        # but it warns while the user can still cancel and fix the setup.
        _repo_root = Path(__file__).resolve().parents[2]
        if config.voice_enhance != "off":
            from whispersync.engine import enhance as _enhance

            _reason = _enhance.unavailable_reason(config.voice_enhance, _repo_root)
            if _reason:
                _msg = f"{_reason} The run continues; the voice will be left unenhanced."
                logger.warning(_msg)
                warnings.append(_msg)
                _notify("scanning", 0.0, _msg)
        if config.ambience_track:
            from whispersync.engine import separation as _separation

            if not _separation.is_available(_repo_root):
                _msg = (
                    "Ambience track requested but the separation environment "
                    "(.sep-venv) is missing — run setup_sep_venv.sh. The run "
                    "continues without an ambience lane."
                )
                logger.warning(_msg)
                _notify("scanning", 0.0, _msg)

        # --- scanning (group clips by camera) ---
        _notify("scanning", 0.0, "Scanning video directory...")
        cameras, scan_warnings = scan_cameras(video_dir, config)
        warnings.extend(scan_warnings)
        video_clips: list[MediaClip] = []
        video_infos: list[MediaInfo] = []
        clip_camera: list[int] = []  # camera index per clip
        for ci, cam in enumerate(cameras):
            video_infos.extend(cam.infos)
            for clip in cam.clips:
                video_clips.append(clip)
                clip_camera.append(ci)
        camera_list = ", ".join(c.name for c in cameras)
        _notify(
            "scanning",
            1.0,
            f"Found {len(video_clips)} clip(s) across {len(cameras)} camera(s): {camera_list}",
        )

        # STABLE IDENTITY for every physical input, before anything is named
        # after it. File stems are not identifiers: /A/take.wav and /B/take.wav
        # both produced `.master/take_master.wav`, so the second extraction
        # overwrote the first and BOTH recorders then rendered from one file;
        # clip.mov and clip.mp4 from one camera collided the same way, as did
        # two cameras' identically named clips in `transcripts/`. Every
        # artifact below is named from `sid` — which is unique by construction
        # — and display names are used only for the UI and the FCPXML.
        # See engine/sources.py.
        used_sids: set[str] = set()
        cam_sources = assign_source_ids(
            [
                ("camera", cameras[ci].name if len(cameras) > 1 else "", clip.path)
                for ci, clip in zip(clip_camera, video_clips, strict=True)
            ],
            used_sids,
        )
        rec_sources = assign_source_ids([("recorder", "", path) for path in audio_files], used_sids)

        # A path appearing twice is a different failure from a name collision:
        # the same file would be transcribed, rendered and exported twice under
        # two identities, doubling the work and the timeline.
        _seen_inputs: dict[Path, str] = {}
        for ref in [*cam_sources, *rec_sources]:
            resolved = ref.path.resolve()
            previous = _seen_inputs.get(resolved)
            if previous is not None:
                raise ValueError(
                    f"{ref.path} is listed twice (as {previous} and {ref.sid}). "
                    "Each video clip and recorder file may appear only once."
                )
            _seen_inputs[resolved] = ref.sid

        # Preliminary layout from filenames so the timeline is populated while
        # transcription runs; real positions replace it after alignment.
        video_status = ["pending"] * len(video_clips)
        prelim = preliminary_offsets(video_clips, clip_camera)
        for clip, off in zip(video_clips, prelim, strict=True):
            clip.offset = off

        def _video_snapshot() -> list[dict[str, Any]]:
            return make_timeline_entries(
                cameras, clip_camera, video_clips, video_status, [], [], [], []
            )

        _notify("scanning", 1.0, "Preliminary layout", clips=_video_snapshot())

        # --- transcribe every recorder once ---
        # The engine checks the disk first and reports which slow thing is
        # happening: "found on disk — loading" vs "not found — downloading".
        def _on_model_loading(message: str) -> None:
            _notify("transcribing_recorder", 0.0, message)

        engine = WhisperEngine(config, on_model_loading=_on_model_loading)
        transcripts_dir = workspace.output_subdir("transcripts")

        def _save_tx(transcript: Transcript, stem: str, audio_path: Path) -> None:
            if not config.save_transcripts:
                return
            save_transcript(
                transcript,
                transcripts_dir,
                stem,
                audio_path=audio_path,
                model=config.model,
                device=engine.device if engine else config.device,
                compute_type=engine.compute_type if engine else config.compute_type,
                mode=config.transcribe_mode,
            )

        rec_infos = [probe(p, timeout=config.probe_timeout_s) for p in audio_files]
        for ref, info in zip(rec_sources, rec_infos, strict=True):
            ref.stream_index = info.audio_stream_index
        for ref, info in zip(cam_sources, video_infos, strict=True):
            ref.stream_index = info.audio_stream_index

        rec_transcripts = []
        for ri, rp in enumerate(audio_files):
            _check_cancel()
            _notify("transcribing_recorder", ri / len(audio_files), f"Recorder: {rp.name}")
            rt = engine.transcribe(
                rp,
                lambda p: _notify("transcribing_recorder", p),
                stream_index=rec_sources[ri].stream_index,
            )
            rec_transcripts.append(rt)
            # Named by source id, not stem: two recorders called take.wav wrote
            # one another's transcripts under a shared `transcripts/take.json`.
            _save_tx(rt, rec_sources[ri].sid, rp)

        # Inter-word silence midpoints per recorder, for seam-snap-to-silence
        # (clip_pieces nudges piece boundaries away from mid-word cuts).
        rec_word_gaps = [
            recorder_word_gaps([(w.start, w.end) for w in rt.words]) for rt in rec_transcripts
        ]

        # Coarse-match rare-word index per recorder, built once and reused for
        # every camera clip aligned against it — estimate_coarse_delta's index
        # only depends on the recorder's words, not the clip, so re-indexing it
        # per clip (the previous behaviour) repeated the same Counter/position
        # pass over a possibly multi-hour recorder for every single clip. See
        # PROJECT_ANALYSIS.md §6.5.
        rec_indices = [
            build_recorder_index(
                normalize_words(list(rt.words), config.anchor_min_confidence), config
            )
            for rt in rec_transcripts
        ]

        # --- align each clip against each recorder ---
        # aligns[clip_idx][rec_idx] = AlignmentMap | None
        aligns: list[list[AlignmentMap | None]] = []
        clip_transcripts: list[Transcript] = []
        n = len(video_clips)
        for idx, clip in enumerate(video_clips):
            _check_cancel()
            video_status[idx] = "working"
            # A clip with no audio track (timelapse, silent b-roll) has nothing to
            # transcribe/align — extract_audio_to_wav would raise and abort the
            # whole run. Skip straight to "placed by order" for this one clip
            # instead. See PROJECT_ANALYSIS.md §3.4.
            if video_infos[idx].audio_codec is None:
                _notify(
                    "transcribing_camera",
                    idx / max(n, 1),
                    f"Skipping {clip.path.name} (no audio track)",
                    clips=_video_snapshot(),
                )
                warnings.append(f"{clip.path.name}: no audio track — placed by order")
                video_status[idx] = "pending"
                aligns.append([None] * len(rec_transcripts))
                # Keep clip_transcripts index-aligned with video_clips even though
                # this clip has nothing to transcribe — _add_job (below) never
                # dereferences it for an unaligned clip, but the list must stay
                # positionally correct for clips after this one.
                clip_transcripts.append(
                    Transcript(source_path=clip.path, language="", duration=0.0, segments=[])
                )
                continue
            _notify(
                "transcribing_camera",
                idx / max(n, 1),
                f"Transcribing camera clip {idx + 1}/{n}: {clip.path.name}",
                clips=_video_snapshot(),
            )
            clip_audio = extract_audio_to_wav(clip.path, stream_index=cam_sources[idx].stream_index)
            cleanup_paths.append(clip_audio)

            # Per-clip progress, not just a stage message: without this the
            # progress bar sits frozen at the same percentage for the whole
            # duration of a long camera clip's transcription. Progress ticks
            # carry NO message — the clip was already announced by the
            # "Transcribing camera clip i/n: ..." line above, and a message
            # here would be re-logged on every transcription segment,
            # flooding the log with dozens of identical lines per clip. See
            # PROJECT_ANALYSIS.md §6.6.
            def _clip_progress(p: float, idx: int = idx) -> None:
                _notify("transcribing_camera", (idx + p) / max(n, 1))

            # The cache is keyed by the ORIGINAL clip (path/size/mtime plus the
            # chosen stream), not by the throw-away scratch WAV the decode
            # produced: that scratch file gets a fresh random name and mtime on
            # every run, so its key never repeated and the expensive
            # transcription was redone in full each time — while leaving a
            # single-use cache entry behind. See transcriber._cache_key.
            clip_transcript = engine.transcribe(
                clip_audio,
                _clip_progress,
                identity=cam_sources[idx].path,
                stream_index=cam_sources[idx].stream_index,
            )
            clip_transcripts.append(clip_transcript)
            _save_tx(clip_transcript, cam_sources[idx].sid, clip.path)
            row: list[AlignmentMap | None] = []
            for ri, rt in enumerate(rec_transcripts):
                try:
                    row.append(align(clip_transcript, rt, config, rec_indices[ri]))
                except ValueError:
                    row.append(None)
            video_status[idx] = "pending"
            if all(a is None for a in row):
                if config.acoustic_fallback:
                    row = [
                        _try_acoustic_fallback(
                            clip_audio,
                            video_infos[idx].duration,
                            audio_files[ri],
                            rec_infos[ri].duration,
                            config,
                        )
                        for ri in range(len(audio_files))
                    ]
                if all(a is None for a in row):
                    warnings.append(f"{clip.path.name}: not aligned to any recorder")
                elif config.acoustic_fallback:
                    warnings.append(
                        f"{clip.path.name}: no usable transcript match — used acoustic "
                        "fallback (coarse waveform cross-correlation) instead"
                    )
            aligns.append(row)

        # --- ACCEPTANCE GATE: decide once which maps may be used at all ---
        #
        # Placement and rendering used to judge maps separately: placement
        # required `min_anchors`, while `_add_job` rendered from the raw
        # per-recorder matrix. A map placement had just rejected as unreliable
        # was therefore still used to cut audio — the clip was laid out by
        # filename order while its voice came from a fit nobody trusted. And
        # neither check looked at the clock ratio, the residual or how much of
        # the clip the evidence covered, so a two-anchor fit with k=10 and a
        # sub-millisecond residual passed both.
        #
        # One gate, applied here, produces `accepted[clip][rec]`; nothing below
        # reads `aligns` directly.
        accepted: list[list[AlignmentMap | None]] = []
        for ci in range(n):
            gated: list[AlignmentMap | None] = []
            for ri, am in enumerate(aligns[ci]):
                verdict = evaluate_alignment(am, video_clips[ci].duration, config)
                if am is not None and not verdict.accepted:
                    warnings.append(
                        f"{video_clips[ci].path.name}: alignment to "
                        f"{audio_files[ri].name} rejected — {verdict.reason_text}"
                    )
                gated.append(am if verdict.accepted else None)
            accepted.append(gated)

        # --- pick the primary recorder for placement ---
        # Scored on ACCEPTED maps only, and on evidence rather than raw anchor
        # count, so a recorder whose every map failed the gate can never be
        # chosen as the reference for the whole timeline.
        rec_scores = [
            sum(_map_evidence(accepted[ci][ri]) for ci in range(n))
            for ri in range(len(audio_files))
        ]
        if max(rec_scores, default=0.0) <= 0.0:
            raise RuntimeError(
                "No camera clip could be aligned to any recorder audio with enough "
                "confidence to use. Check that the recorder covers the same take as "
                "the video, and see the warnings above for each clip's reason."
            )
        primary = max(range(len(audio_files)), key=lambda ri: rec_scores[ri])
        if len(audio_files) > 1:
            warnings.append(f"Timeline placement uses recorder '{audio_files[primary].name}'")

        # --- place clips on the master timeline from primary-recorder timecodes ---
        _notify("aligning", 1.0, "Placing clips on timeline...")
        primary_aligns = [accepted[ci][primary] for ci in range(n)]
        durations = [c.duration for c in video_clips]
        offsets, unaligned = compute_master_offsets(primary_aligns, durations, clip_camera)
        for clip, off in zip(video_clips, offsets, strict=True):
            clip.offset = off
        for i in unaligned:
            warnings.append(
                f"{video_clips[i].path.name}: UNRESOLVED — no accepted alignment to the "
                "primary recorder; placed after the previous clip of its own camera. "
                "Its position on the timeline is a guess, not a measurement."
            )

        aligned_primary = [primary_aligns[i] is not None for i in range(n)]
        for i in range(n):
            video_status[i] = "done" if aligned_primary[i] else "pending"
        warnings.extend(sequence_order_warnings(video_clips, clip_camera, aligned_primary))

        # --- choose which camera the synced audio is derived from ---
        anchors_per_cam: dict[int, float] = {}
        for ci, row in zip(clip_camera, accepted, strict=True):
            best_in_row = max((_map_evidence(a) for a in row), default=0.0)
            anchors_per_cam[ci] = anchors_per_cam.get(ci, 0.0) + best_in_row
        if config.audio_source_camera:
            audio_ci = next(
                (i for i, c in enumerate(cameras) if c.name == config.audio_source_camera),
                max(anchors_per_cam, key=lambda i: anchors_per_cam[i]),
            )
        else:
            audio_ci = max(anchors_per_cam, key=lambda i: anchors_per_cam[i])
        if len(cameras) > 1:
            warnings.append(f"Audio synced from camera '{cameras[audio_ci].name}'")

        # Constant per-camera lip-sync calibration (see config.camera_av_offset_ms):
        # a fixed mic-to-lips delay in the camera's own audio pipeline that no
        # acoustic method can see (it aligns recorder audio to camera AUDIO, not
        # to the video frames/lips). Applied uniformly to every synced clip's
        # timeline offset below.
        av_offset_s = (
            config.camera_av_offset_ms_by_camera.get(
                cameras[audio_ci].name, config.camera_av_offset_ms
            )
            / 1000.0
        )
        if av_offset_s != 0.0:
            warnings.append(
                f"Applying {av_offset_s * 1000:+.1f} ms lip-sync calibration for "
                f"camera '{cameras[audio_ci].name}'"
            )

        # --- plan + render synced audio (one WAV per audio-source video clip) ---
        # Video files are referenced untouched. For each one we render a single
        # recorder-audio WAV of identical length, with speech placed at its synced
        # position and silence filling the gaps — so the FCPXML carries ~2 clips
        # per video instead of thousands of segment clips.
        _notify("planning", 0.0, "Planning sync strategy...")
        audio_synced_dir = workspace.output_subdir("audio_synced")

        out_sr: int
        if config.timebase_source == "recorder" and rec_infos[primary].audio_sample_rate:
            out_sr = int(rec_infos[primary].audio_sample_rate or 48000)
        elif video_infos and video_infos[0].audio_sample_rate:
            out_sr = int(video_infos[0].audio_sample_rate or 48000)
        else:
            out_sr = 48000

        # Render-path audio format (PROJECT_ANALYSIS.md §2.0): preserve each
        # recorder's own channel count and a lossless PCM codec matching its bit
        # depth, instead of collapsing everything to 16-bit mono. Channels/codec
        # are picked per-recorder (a multi-recorder "all" setup may mix a mono lav
        # and a stereo Zoom); pieces cut from recorder ``ri`` always use that
        # recorder's own format so nothing is upmixed/downmixed along the way.
        out_channels_by_rec = [max(1, info.audio_channels or 1) for info in rec_infos]
        # Codec follows the source's SAMPLE FORMAT as well as its depth. A
        # 32-bit float recorder — whose entire point is that samples above
        # ±1.0 stay recoverable — was conformed to pcm_s32le purely because it
        # was "32-bit", and ffmpeg hard-clips at full scale on the way in:
        # [0, 0.5, 1, 1.5, 2, -1.5] came back as [0, 0.5, 1, 1, 1, -1]. Those
        # peaks are gone for good; no later gain reduction brings them back.
        out_codec_by_rec = [pcm_codec_for(info) for info in rec_infos]

        # Normalize each recorder to a lossless PCM master at the render's target
        # sample rate once, up front. Cutting pieces directly from a lossy source
        # (mp3/m4a) with -ss before -i is not sample-accurate (seek lands on a
        # frame boundary), and re-decoding a lossy file on every cut compounds
        # artifacts; cutting from an already-uncompressed, already-resampled
        # master fixes both and makes every piece/lead-silence/concat operate on
        # identical PCM (concat demuxer requires matching stream parameters).
        # Masters are an INTERMEDIATE, not an output: they live in this run's
        # private scratch, named by source id. The shared `audio_synced/.master`
        # directory with stem-derived names collided two ways at once — two
        # recorders called take.wav produced one file (so both "recorders"
        # rendered from whichever was extracted last), and a concurrent run
        # rewrote the master a running render was still cutting pieces from.
        master_dir = workspace.scratch_dir("master")
        rec_master_paths: list[Path] = []
        for ri, rp in enumerate(audio_files):
            _check_cancel()
            master_path = master_dir / rec_sources[ri].artifact_name("master.wav")
            extract_audio_master(
                rp,
                master_path,
                out_sr,
                out_channels_by_rec[ri],
                out_codec_by_rec[ri],
                stream_index=rec_sources[ri].stream_index,
            )
            rec_master_paths.append(master_path)

        audio_clips: list[MediaClip] = []
        audio_speed: list[float] = []
        audio_track: list[str] = []

        @dataclass
        class RenderJob:
            clip_idx: int
            rec_path: Path  # lossless PCM master (see rec_master_paths above)
            lead: float
            pieces: list[tuple[float, float, float]]
            duration: float
            cam_audio: Path | None  # camera clip audio (mono 16k), for Boundary Flex
            rec_duration: float
            pauses: list[tuple[float, float]]  # local-time pause spans, for ducking
            channels: int
            codec: str
            video_clip_idx: int  # index into video_clips/clip_transcripts, for self-check
            recorder_idx: int  # index into rec_transcripts/rec_master_paths, for self-check repair
            am: AlignmentMap  # this job's own alignment, for self-check repair re-planning

        render_jobs: list[RenderJob] = []

        def _add_job(vclip: MediaClip, am: AlignmentMap, ri: int, lane: int, ci: int) -> None:
            lead, pieces = clip_pieces(
                am,
                vclip.duration,
                rec_infos[ri].duration,
                strategy_id,
                config,
                rec_word_gaps=rec_word_gaps[ri],
                rec_words=[(w.start, w.end) for w in rec_transcripts[ri].words],
            )
            if not pieces:
                return
            # A dropped head is forced by the geometry, but it is not
            # necessarily silence — Whisper misses quiet or overlapping speech,
            # so say what was left out instead of letting the user find a
            # missing opening phrase in the edit.
            k_map = am.k or 1.0
            rec_start = min(max((0.0 - am.offset) / k_map, 0.0), rec_infos[ri].duration)
            dropped = head_trim_seconds(pieces, rec_start)
            if dropped > HEAD_TRIM_WARN_S:
                warnings.append(
                    f"{vclip.path.name}: {dropped:.1f}s of recorder audio before the first "
                    "recognised sentence could not fit and was left out. It is assumed to "
                    "be room tone; if the take opens with quiet or overlapping speech, "
                    "check the start of this clip."
                )
            label = f"Audio: {audio_files[ri].stem}" if config.recorder_mode == "all" else "Audio"
            # Rendered-file name comes from the SOURCE IDS, which are unique by
            # construction; two cameras' identically named clips used to render
            # to the same "clip_voice.wav" and one overwrote the other. Role =
            # Dialogue so FCPX colours/groups the synced voice as dialogue.
            voice_name = f"{cam_sources[ci].sid}_voice"
            if config.recorder_mode == "all":
                voice_name = f"{cam_sources[ci].sid}_{rec_sources[ri].sid}_voice"
            retake_groups = _retake_groups_for_clip(
                am, vclip.duration, rec_infos[ri].duration, rec_transcripts[ri].words, config
            )
            if retake_groups:
                warnings.append(
                    f"{vclip.path.name}: found {len(retake_groups)} likely retake(s) — "
                    "exported as Final Cut auditions (press Q to browse takes)"
                )
            audio_clips.append(
                MediaClip(
                    path=audio_files[ri],
                    kind="audio",
                    offset=vclip.offset + av_offset_s,
                    in_point=0.0,
                    duration=vclip.duration,
                    lane=lane,
                    display_name=voice_name,
                    role="Dialogue",
                    retake_groups=retake_groups or None,
                    # The explicit link verification needs: this voice covers
                    # [0, duration) of THIS camera clip. No name matching.
                    source_ref=(vclip.path, 0.0, vclip.duration),
                )
            )
            audio_speed.append(1.0 / am.k if am.k else 1.0)
            audio_track.append(label)
            # This clip's dialogue is now supplied by the recorder, so its own
            # microphone must be muted in the export — otherwise the timeline
            # plays two copies of the same voice tens of milliseconds apart.
            vclip.source_audio_enabled = False

            # Camera clip audio is only needed for Boundary Flex; extract once here.
            cam_audio: Path | None = None
            if config.boundary_flex:
                cam_audio = extract_audio_to_wav(vclip.path)
                cleanup_paths.append(cam_audio)
            pauses = (
                pause_spans_local(
                    am,
                    vclip.duration,
                    config.phrase_gap_threshold,
                    config.pause_duck_min_pause_s,
                    cam_words=[(w.start, w.end) for w in clip_transcripts[ci].words],
                    rec_words=[(w.start, w.end) for w in rec_transcripts[ri].words],
                    rec_duration=rec_infos[ri].duration,
                )
                if config.pause_duck_enabled
                else []
            )
            render_jobs.append(
                RenderJob(
                    clip_idx=len(audio_clips) - 1,
                    rec_path=rec_master_paths[ri],
                    lead=lead,
                    pieces=pieces,
                    duration=vclip.duration,
                    cam_audio=cam_audio,
                    rec_duration=rec_infos[ri].duration,
                    pauses=pauses,
                    channels=out_channels_by_rec[ri],
                    codec=out_codec_by_rec[ri],
                    video_clip_idx=ci,
                    recorder_idx=ri,
                    am=am,
                )
            )

        for ci in range(n):
            if clip_camera[ci] != audio_ci:
                continue
            vclip = video_clips[ci]
            row = accepted[ci]  # gated maps only — never the raw matrix
            if config.recorder_mode == "all":
                for ri, am in enumerate(row):
                    if am is not None:
                        _add_job(vclip, am, ri, lane=-(ri + 1), ci=ci)
            else:  # "best": one lane, strongest recorder per clip
                candidates = [(ri, am) for ri, am in enumerate(row) if am is not None]
                if candidates:
                    best_ri, best_am = max(candidates, key=lambda t: _map_evidence(t[1]))
                    _add_job(vclip, best_am, best_ri, lane=-1, ci=ci)

        # Clips with no replacement dialogue keep their own sound: muting them
        # would hand the editor silent footage with no way back.
        for vclip in video_clips:
            if vclip.source_audio_enabled is None:
                vclip.source_audio_enabled = True

        audio_status = ["pending"] * len(audio_clips)

        def _timeline_snapshot() -> list[dict[str, Any]]:
            return make_timeline_entries(
                cameras,
                clip_camera,
                video_clips,
                video_status,
                audio_clips,
                audio_speed,
                audio_track,
                audio_status,
            )

        # Show the full planned layout (video placed, audio pending) up front.
        _notify("planning", 1.0, "Timeline planned", clips=_timeline_snapshot())

        from whispersync.models import SyncPlan

        all_clips = video_clips + audio_clips
        plan = SyncPlan(
            strategy_id=strategy_id,
            clips=all_clips,
            total_duration=_timeline_end(all_clips),
        )
        _notify("planning", 1.0, f"Strategy {strategy_id}: {strategy_name(strategy_id)}")

        # Whisper is not needed past this point (rendering is pure ffmpeg, and
        # ambience extraction below runs its own GPU model in .sep-venv). Free
        # its VRAM now rather than in the `finally` block at the very end —
        # holding it through rendering/ambience is a common cause of GPU OOM on
        # cards with 8-12 GB VRAM. See PROJECT_ANALYSIS.md §6.1. Self-check
        # (below) creates its OWN engine with its own transcribe mode, so
        # keeping this one loaded "for later" would only mean two copies of
        # the model in VRAM at once — always unload here.
        if engine is not None:
            engine.unload()
            engine = None

        # --- render one continuous synced WAV per clip ---
        _notify("processing", 0.0, "Rendering synced audio...")
        # Small length-preserving fades declick the seams between stretched pieces.
        fade_ms = config.crossfade_ms if config.crossfade_enabled else 0
        workers = resolve_workers(config.render_workers)
        n_jobs = max(len(render_jobs), 1)

        # Phase 1 — Boundary Flex + a scratch dir per job. Flex must finish
        # before a job's pieces can render (it rewrites their start times);
        # it's in-memory FFT work, thread-parallel internally. Scratch dirs
        # live on the OUTPUT volume (next to the result — /tmp may be small or
        # full) and persist until each job is assembled in phase 2; any
        # leftovers are force-cleaned in the outer `finally`.
        flex_frac = 0.3 if config.boundary_flex else 0.0
        prepared: list[tuple[Any, list[tuple[float, float, float]], Path]] = []
        # One decoded analysis array per RECORDER, reused by every job that
        # reads it. Each job used to decode the whole recorder again — ~230 MB
        # per hour at 16 kHz mono float32, plus the ffmpeg pass to produce it,
        # multiplied by the number of clips. Jobs are processed in recorder
        # order below so at most one such array is alive at a time; caching
        # them all would trade the repeated I/O for unbounded memory.
        rec_tracks: dict[int, Any] = {}
        flex_order = sorted(range(len(render_jobs)), key=lambda i: render_jobs[i].recorder_idx)
        prepared_by_index: dict[int, tuple[Any, list[tuple[float, float, float]], Path]] = {}
        for step, j in enumerate(flex_order):
            job = render_jobs[j]
            _check_cancel()
            tdp = workspace.temp_dir("seg")
            job_scratch_dirs.append(tdp)
            pieces = job.pieces
            # Boundary Flex: acoustically nudge each piece's recorder start so
            # speech lands under the picture to sub-frame accuracy.
            if config.boundary_flex and job.cam_audio is not None:
                _notify(
                    "processing",
                    flex_frac * step / n_jobs,
                    f"Boundary Flex {step + 1}/{n_jobs}",
                )
                if job.recorder_idx not in rec_tracks:
                    rec_tracks.clear()  # only the current recorder stays resident
                    from whispersync.engine.acoustic import load_mono16k_track

                    rec_tracks[job.recorder_idx] = load_mono16k_track(job.rec_path)
                job.lead, pieces = refine_piece_boundaries(
                    pieces,
                    job.lead,
                    job.cam_audio,
                    job.rec_path,
                    job.duration,
                    job.rec_duration,
                    config,
                    tmp_dir=tdp,
                    workers=workers,
                    rec_track=rec_tracks[job.recorder_idx],
                )
            problems = validate_pieces(job.lead, pieces, job.duration, job.rec_duration)
            if problems:
                # A plan that does not tile the clip cannot land speech where it
                # says it will, so say so rather than rendering it silently.
                warnings.append(
                    f"{audio_clips[job.clip_idx].display_name}: render plan problem — "
                    f"{'; '.join(problems)}"
                )
            prepared_by_index[j] = (job, pieces, tdp)
        rec_tracks.clear()
        prepared = [prepared_by_index[j] for j in range(len(render_jobs))]

        # Phase 2 — render the pieces of EVERY job through ONE shared process
        # pool, then assemble jobs in order as their pieces complete (ffmpeg
        # has no GPU audio filters — cores are the speed-up). One pool for the
        # whole run instead of one per job (PROJECT_ANALYSIS.md §6.4): with
        # per-job pools, the single-threaded assembly of job N stalled the
        # piece rendering of job N+1, idling every core at each job boundary;
        # here assembly overlaps the next jobs' rendering, and the (non-fork,
        # see _pool_context) worker startup cost is paid once. Piece WAVs wait
        # on disk until their job assembles — the high-water mark is bounded
        # by the rendered voice itself, the same order of size as the final
        # outputs. channels/codec match the source recorder — no forced
        # mono/16-bit downgrade (PROJECT_ANALYSIS.md §2.0); fades apply only
        # on seams that Boundary Flex actually made discontinuous.
        pool: ProcessPoolExecutor | None = None
        job_futures: list[list[Any]] = []
        try:
            if workers > 1 and prepared:
                pool = ProcessPoolExecutor(max_workers=workers, mp_context=_pool_context())
                for job, pieces, tdp in prepared:
                    job_futures.append(
                        _submit_piece_jobs(
                            pool,
                            pieces,
                            job.rec_path,
                            tdp,
                            fade_ms,
                            out_sr,
                            job.channels,
                            job.codec,
                            config.stretch_method,
                        )
                    )
            for j, (job, pieces, tdp) in enumerate(prepared):
                clip_idx = job.clip_idx
                audio_status[clip_idx] = "working"
                _notify(
                    "processing",
                    flex_frac + (1.0 - flex_frac) * j / n_jobs,
                    f"Rendering synced audio {j + 1}/{n_jobs}",
                    clips=_timeline_snapshot(),
                )
                if pool is None:
                    seg_paths = _render_pieces_sequential(
                        pieces,
                        job.rec_path,
                        tdp,
                        fade_ms,
                        sample_rate=out_sr,
                        channels=job.channels,
                        codec=job.codec,
                        stretch_method=config.stretch_method,
                        cancel_event=cancel_event,
                    )
                else:
                    seg_paths = _collect_piece_futures(job_futures[j], cancel_event)
                # Name the output after the clip (e.g. "DJI_0832_voice.wav") so the
                # media file matches its timeline clip; fall back to an index.
                voice_name = audio_clips[clip_idx].display_name or f"synced_{clip_idx:03d}"
                out = audio_synced_dir / f"{voice_name}.wav"
                # Assembled to a temporary beside the destination and moved into
                # place only once complete. Writing straight to `out` under
                # ffmpeg's `-y` meant a failure or cancellation mid-assembly
                # left a TRUNCATED file exactly where a previous run's good
                # render had been — the old project's FCPXML then pointed at
                # audio that had been silently replaced by a fragment.
                #
                # Pause-ducking (if enabled) is folded into this same assembly
                # pass instead of a second full decode/encode over an
                # intermediate file.
                with published(out) as staged_voice:
                    assemble_continuous(
                        seg_paths,
                        job.lead,
                        job.duration,
                        out_sr,
                        staged_voice,
                        channels=job.channels,
                        codec=job.codec,
                        duck_pauses=job.pauses if config.pause_duck_enabled else None,
                        duck_db=config.pause_duck_db,
                        duck_fade_ms=config.pause_duck_fade_ms,
                    )
                shutil.rmtree(tdp, ignore_errors=True)
                audio_clips[clip_idx].path = out
                audio_clips[clip_idx].in_point = 0.0
                audio_status[clip_idx] = "done"
                _notify(
                    "processing",
                    flex_frac + (1.0 - flex_frac) * (j + 1) / n_jobs,
                    f"Rendered {j + 1}/{n_jobs}",
                    clips=_timeline_snapshot(),
                )
        except BaseException:
            # Drop QUEUED pieces, then WAIT for the ones already running.
            #
            # `shutdown(wait=False)` returns while worker processes are still
            # executing their current task: the `finally` below then deleted
            # the scratch directories and recorder masters those very ffmpeg
            # processes were reading and writing, and the run returned while
            # its children were still alive — free to interleave with the next
            # run. Waiting costs at most one piece's render (each ffmpeg call
            # is bounded by its own timeout) and is what makes "cancelled"
            # actually mean stopped.
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            if pool is not None:
                pool.shutdown(wait=True)

        # --- optional voice enhancement: run a third-party model over each
        # rendered voice monolith BEFORE self-check, so self-check validates
        # whatever audio the user actually gets. Batched across every clip in
        # one call per backend (same reasoning as the ambience separator
        # below — one model load, not one per clip). A missing environment or
        # a backend failure is reported as a warning and the unenhanced
        # monolith is kept, never a hard failure — see engine/enhance.py. ---
        if config.voice_enhance != "off" and audio_clips:
            _check_cancel()
            _notify("processing", 1.0, f"Enhancing voice ({config.voice_enhance})...")
            from whispersync.engine import enhance

            repo_root = Path(__file__).resolve().parents[2]
            enhance_dir = workspace.temp_dir("enhance")
            try:
                enhanced = enhance.run_batch(
                    config.voice_enhance,
                    [aclip.path for aclip in audio_clips],
                    enhance_dir,
                    repo_root,
                )
                not_enhanced: list[str] = []
                for aclip in audio_clips:
                    out_path = enhanced.get(aclip.path)
                    if out_path is not None:
                        os.replace(out_path, aclip.path)
                    else:
                        not_enhanced.append(aclip.path.name)
                if not_enhanced:
                    warnings.append(
                        f"Voice enhancement ({config.voice_enhance}) produced no output "
                        f"for {len(not_enhanced)} of {len(audio_clips)} clip(s) "
                        f"({', '.join(not_enhanced[:5])}"
                        f"{', …' if len(not_enhanced) > 5 else ''}) — those keep "
                        "their unenhanced audio."
                    )
            except (RuntimeError, OSError) as e:
                warnings.append(
                    f"Voice enhancement ({config.voice_enhance}) failed ({e}) — "
                    "using unenhanced audio."
                )
            finally:
                shutil.rmtree(enhance_dir, ignore_errors=True)

        # --- optional self-check: re-transcribe each rendered voice monolith
        # and compare it against the camera clip's own transcript, to catch
        # CONTENT defects (--verify's acoustic lag measurement can't see a
        # dropped/duplicated word or a piece built from the wrong recorder
        # span). Runs on the MONOLITH, before the voice-segmentation split
        # below, since a segment boundary is just a silence-snapped cut of the
        # same audio and re-checking each piece separately would only re-find
        # the same spans (or split one flagged span across two segments).
        # "repair" additionally re-aligns and re-renders each flagged span's
        # own small stretch of audio, then re-transcribes the (now possibly
        # repaired) monolith ONCE more to report what actually still needs a
        # human look — a span left after repair (couldn't be re-aligned
        # confidently, or still doesn't match) is reported exactly like a
        # "warn"-mode finding. ---
        if config.self_check_mode != "off" and render_jobs:
            _notify("processing", 1.0, "Self-check: verifying rendered audio...")
            from whispersync.engine.self_check import check_rendered_clip

            check_config = replace(config, transcribe_mode=config.self_check_transcribe_mode)
            check_engine = WhisperEngine(check_config)
            try:
                for j, job in enumerate(render_jobs):
                    _check_cancel()
                    aclip = audio_clips[job.clip_idx]
                    _notify(
                        "processing",
                        j / max(len(render_jobs), 1),
                        f"Self-check {j + 1}/{len(render_jobs)}: {aclip.path.name}",
                    )
                    cam_words = clip_transcripts[job.video_clip_idx].words
                    rendered_transcript = check_engine.transcribe(aclip.path)
                    outcome = check_rendered_clip(
                        rendered_transcript.words, cam_words, config, aclip.duration
                    )
                    if outcome.status == "inconclusive":
                        # "Could not be checked" is not "checked and fine".
                        warnings.append(
                            f"{aclip.path.name}: self-check INCONCLUSIVE — {outcome.detail}"
                        )
                        continue
                    spans = outcome.spans

                    if spans and config.self_check_mode == "repair":
                        rec_words = rec_transcripts[job.recorder_idx].words
                        repaired_path = aclip.path
                        fixed_any = False
                        repair_tmp = workspace.temp_dir("repair")
                        job_scratch_dirs.append(repair_tmp)
                        for span in spans:
                            candidate = _repair_span(
                                span,
                                job,
                                repaired_path,
                                aclip.duration,
                                cam_words,
                                rec_words,
                                config,
                                out_sr,
                                repair_tmp,
                            )
                            if candidate is not None:
                                repaired_path = candidate
                                fixed_any = True
                        if fixed_any:
                            # Replace the original monolith IN PLACE: the
                            # repaired file currently lives in repair_tmp — a
                            # scratch dir deleted when the run's workspace is
                            # cleaned up — so leaving aclip.path pointing there
                            # would hand the FCPXML a path that no longer
                            # exists by the time the user opens it.
                            os.replace(repaired_path, aclip.path)
                            rendered_transcript = check_engine.transcribe(aclip.path)
                            spans = check_rendered_clip(
                                rendered_transcript.words, cam_words, config, aclip.duration
                            ).spans

                    for span in spans:
                        status = (
                            "still flagged after repair attempt"
                            if (config.self_check_mode == "repair")
                            else "found"
                        )
                        warnings.append(
                            f"{aclip.path.name}: self-check {status} a {span.kind} span "
                            f"[{span.start:.1f}s-{span.end:.1f}s] — {span.detail}"
                        )
            finally:
                check_engine.unload()

        # --- optionally split each rendered voice monolith into N-minute
        # segments (cut in silence), so an NLE's own audio sync (e.g. FCPX
        # "Synchronize Clips") can re-align every few minutes instead of once
        # per clip — residual intra-clip drift then resets at each boundary. ---
        if config.voice_segment_minutes > 0 and audio_clips:
            _notify("processing", 1.0, "Splitting voice into segments...")
            seg_s = config.voice_segment_minutes * 60.0
            new_clips: list[MediaClip] = []
            new_speed: list[float] = []
            new_track: list[str] = []
            new_status: list[str] = []
            for ci_a, aclip in enumerate(audio_clips):
                _check_cancel()
                cuts = (
                    _quiet_cut_points(aclip.path, aclip.duration, seg_s)
                    if aclip.path.suffix.lower() == ".wav"
                    else []
                )
                if not cuts:
                    new_clips.append(aclip)
                    new_speed.append(audio_speed[ci_a])
                    new_track.append(audio_track[ci_a])
                    new_status.append(audio_status[ci_a])
                    continue
                bounds = [0.0, *cuts, aclip.duration]
                base = aclip.path.with_suffix("")
                # Same PCM codec as the rendered voice itself — a PCM->same-PCM
                # cut is byte-identical, so the segments concatenate back into
                # the monolith exactly. `pcm_codec_for` (not the bit-depth-only
                # helper) because the SAMPLE FORMAT decides it: a float voice
                # monolith matched on depth alone would be cut to pcm_s32le,
                # hard-clipping every sample above full scale — the same loss
                # the render path avoids, reintroduced at the very last step.
                voice_codec = pcm_codec_for(probe(aclip.path, timeout=config.probe_timeout_s))
                for si in range(len(bounds) - 1):
                    a, b = bounds[si], bounds[si + 1]
                    part = Path(f"{base}_p{si + 1:02d}.wav")
                    cut_wav_segment(aclip.path, part, a, b, codec=voice_codec)
                    seg_retakes = _retake_groups_in_segment(aclip.retake_groups, a, b)
                    new_clips.append(
                        MediaClip(
                            path=part,
                            kind="audio",
                            offset=aclip.offset + a,
                            in_point=0.0,
                            duration=b - a,
                            lane=aclip.lane,
                            display_name=f"{aclip.display_name or aclip.path.stem}_p{si + 1:02d}",
                            role=aclip.role,
                            retake_groups=seg_retakes or None,
                            # Narrow the source link to THIS segment's own
                            # stretch of the clip: a segment covering minutes
                            # 5-10 must be verified against minutes 5-10 of the
                            # video, not against its opening seconds.
                            source_ref=(
                                (
                                    aclip.source_ref[0],
                                    aclip.source_ref[1] + a,
                                    b - a,
                                )
                                if aclip.source_ref
                                else None
                            ),
                        )
                    )
                    new_speed.append(audio_speed[ci_a])
                    new_track.append(audio_track[ci_a])
                    new_status.append(audio_status[ci_a])
                with contextlib.suppress(OSError):
                    os.unlink(aclip.path)
                logger.info(
                    "Split %s into %d segment(s) of ~%d min",
                    aclip.path.name,
                    len(bounds) - 1,
                    config.voice_segment_minutes,
                )
            audio_clips = new_clips
            audio_speed, audio_track, audio_status = new_speed, new_track, new_status
            plan.clips = video_clips + audio_clips
            plan.total_duration = _timeline_end(plan.clips)
            _notify("processing", 1.0, "Voice segments ready", clips=_timeline_snapshot())

        # --- extract voice-free camera ambience onto its own lane (optional) ---
        # Strip the camera's own voice (which would double/echo the clean synced
        # voice) but keep the room tone, on a lane below the synced audio.
        if config.ambience_track:
            from whispersync.engine import separation

            repo_root = Path(__file__).resolve().parents[2]
            if not separation.is_available(repo_root):
                warnings.append(
                    "Ambience track requested but the separation environment "
                    "(.sep-venv) is missing — skipped."
                )
            else:
                ambience_dir = output_path.parent / "ambience"
                model_dir = repo_root / "models" / "separator"
                ambient_lane = min((c.lane for c in audio_clips), default=-1) - 1
                # Only clips that actually HAVE an audio stream. The earlier
                # stages skip silent b-roll, but ambience re-collected every
                # clip of the camera — so one silent timelapse in a 70-clip
                # shoot raised out of `extract_audio_to_wav` (which sits
                # OUTSIDE the batch try/except) and threw away the entire
                # export after hours of rendering, for an optional lane that is
                # on by default.
                amb_indices = [
                    ci
                    for ci in range(n)
                    if clip_camera[ci] == audio_ci and video_infos[ci].audio_codec is not None
                ]
                src_clips = [video_clips[ci] for ci in amb_indices]

                # Extract every clip's camera audio first, then run the
                # separator ONCE over the whole batch — audio-separator loads
                # its (1.5+ GB) model once per invocation, so calling it once
                # per clip (the previous behaviour) reloaded the model from
                # scratch for every camera clip in a multi-clip shoot. See
                # PROJECT_ANALYSIS.md §6.3.
                #
                # The extracted audio is named after its CLIP, not left as an
                # anonymous tempfile: the separator derives its output name
                # from the input's, so "DJI_0762.wav" lands as
                # "DJI_0762_(Instrumental)_<model>.wav". Anything left behind
                # by an interrupted or partially failed run is then still
                # identifiable — a field run that failed at this step left 70
                # files called "tmp0plj448a_(Instrumental)_....wav", which no
                # editor could match back to a clip.
                amb_work = workspace.temp_dir("ambience_src")
                job_scratch_dirs.append(amb_work)
                cam_wavs: list[Path] = []
                amb_clip_indices: list[int] = []
                amb_names: list[str] = []
                extract_failures: list[str] = []
                for k, (ci_amb, vclip) in enumerate(zip(amb_indices, src_clips, strict=True)):
                    _check_cancel()
                    _notify(
                        "processing",
                        k / max(len(src_clips), 1),
                        f"Extracting camera audio {k + 1}/{len(src_clips)}: {vclip.path.name}",
                    )
                    # Source ids are unique already — no ad-hoc de-duplication
                    # needed, and no chance of two cameras' same-named clips
                    # overwriting one another's ambience.
                    name = cam_sources[ci_amb].sid
                    try:
                        cam_wav = extract_audio_to_wav(
                            vclip.path,
                            amb_work / f"{name}.wav",
                            sample_rate=out_sr,
                            mono=False,
                            stream_index=cam_sources[ci_amb].stream_index,
                        )
                    except (RuntimeError, OSError) as e:
                        # One clip that will not decode costs its own ambience
                        # lane, not the finished project.
                        logger.warning("Ambience: could not extract %s (%s)", vclip.path.name, e)
                        extract_failures.append(vclip.path.name)
                        continue
                    cam_wavs.append(cam_wav)
                    amb_names.append(name)
                    amb_clip_indices.append(ci_amb)
                if extract_failures:
                    warnings.append(
                        f"Ambience: could not extract camera audio for "
                        f"{len(extract_failures)} clip(s) "
                        f"({', '.join(extract_failures[:5])}"
                        f"{', …' if len(extract_failures) > 5 else ''}) — no ambience "
                        "lane for those."
                    )
                src_clips = [video_clips[ci] for ci in amb_clip_indices]

                _notify("processing", 0.0, f"Separating ambience for {len(cam_wavs)} clip(s)...")
                amb_clips: list[MediaClip] = []
                try:
                    amb_by_input = separation.extract_ambience_batch(
                        cam_wavs,
                        ambience_dir,
                        repo_root,
                        config.ambience_model,
                        model_dir=model_dir if model_dir.exists() else None,
                    )
                except (RuntimeError, OSError) as e:
                    warnings.append(f"Ambience batch separation failed ({e}) — skipped.")
                    amb_by_input = {}

                skipped_clips: list[str] = []
                for vclip, cam_wav, cam_name in zip(src_clips, cam_wavs, amb_names, strict=True):
                    amb = amb_by_input.get(cam_wav)
                    if amb is None:
                        # One clip the separator couldn't handle no longer costs
                        # the whole shoot its ambience — name it and move on.
                        skipped_clips.append(vclip.path.name)
                        continue
                    # Rename the separator's "…_(Instrumental)_<model>.wav" to a clean
                    # clip-matched name ("DJI_0832_ambience.wav"). cam_name (not the
                    # raw stem) keeps two cameras' same-named clips apart — otherwise
                    # one would overwrite the other's ambience.
                    amb_final = ambience_dir / f"{cam_name}_ambience.wav"
                    with contextlib.suppress(OSError):
                        amb.replace(amb_final)
                        amb = amb_final
                    amb_clips.append(
                        MediaClip(
                            path=amb,
                            kind="audio",
                            offset=vclip.offset,
                            in_point=0.0,
                            duration=vclip.duration,
                            lane=ambient_lane,
                            display_name=f"{cam_name}_ambience",
                            role="Effects",
                        )
                    )
                if skipped_clips and amb_by_input:
                    warnings.append(
                        f"Ambience separation produced no output for "
                        f"{len(skipped_clips)} of {len(src_clips)} clip(s) "
                        f"({', '.join(skipped_clips[:5])}"
                        f"{', …' if len(skipped_clips) > 5 else ''}) — those clips have "
                        "no ambience lane; see the log for each one's reason."
                    )
                plan.clips.extend(amb_clips)
                plan.total_duration = _timeline_end(plan.clips)

        # Every clip is in the plan by now (voice, segments, ambience), so this
        # is the one place a whole-plan origin shift can be applied
        # consistently — before EITHER serializer reads an offset. Doing it
        # here rather than inside each of them is what makes a negative
        # lip-sync calibration actually take effect instead of being clamped
        # away twice, differently.
        origin_shift = normalize_plan_origin(plan.clips)
        if origin_shift > 0:
            plan.total_duration = _timeline_end(plan.clips)
            warnings.append(
                f"Timeline shifted {origin_shift * 1000:.0f} ms later so the "
                f"{-origin_shift * 1000:.0f} ms lip-sync calibration fits before "
                "time zero; relative sync is unchanged."
            )

        # --- optional master WAV (single file spanning the whole timeline, for
        # users without an NLE) ---
        master_wav_path: Path | None = None
        if config.render_master_wav:
            _notify("processing", 0.0, "Rendering master WAV...")
            audio_timeline_clips = [c for c in plan.clips if c.kind == "audio"]
            if audio_timeline_clips:
                master_wav_path = output_path.parent / f"{output_path.stem}_master.wav"
                # Use the strongest recorder's channel/codec choice (same one
                # picked for the primary synced-voice renders) as the master's
                # own format; every input clip is itself already a lossless
                # PCM render at out_sr, so this mix never re-touches bit depth.
                master_channels = out_channels_by_rec[primary]
                master_codec = out_codec_by_rec[primary]
                # Rendered to a temporary beside the destination and moved into
                # place only once complete: a crash or a full disk mid-mix used
                # to leave a truncated master standing where the previous run's
                # good one had been.
                with published(master_wav_path) as tmp_master:
                    mix_clips_on_timeline(
                        [(c.path, c.offset) for c in audio_timeline_clips],
                        plan.total_duration,
                        out_sr,
                        tmp_master,
                        channels=master_channels,
                        codec=master_codec,
                    )
            else:
                warnings.append("render_master_wav requested but no synced audio clips to mix")

        # --- export ---
        _notify("exporting", 0.0, "Generating FCPXML...")
        # The FCPXML is the run's deliverable and is published LAST, atomically,
        # after every asset it references exists: a reader either sees the
        # previous project or this one, never a document pointing at audio that
        # is still being written. Validation runs on the staged file, so a
        # broken document never replaces a working one.
        staged_fcpxml = workspace.scratch_dir("export") / output_path.name
        generate_fcpxml(
            plan,
            video_infos,
            staged_fcpxml,
            config.fcpxml_version,
            output_path.stem,
            audio_sample_rate=out_sr,  # matches the rendered synced WAVs
        )
        export_problems = check_fcpxml(staged_fcpxml, check_media=True)
        if export_problems:
            warnings.append(
                "Generated FCPXML failed validation — Final Cut Pro may refuse to "
                f"import it: {'; '.join(export_problems[:5])}"
                f"{f' (+{len(export_problems) - 5} more)' if len(export_problems) > 5 else ''}"
            )
        with published(output_path) as tmp_xml:
            shutil.copy2(staged_fcpxml, tmp_xml)

        # --- collect quality warnings from the best alignment overall ---
        # Quality warnings must describe maps that were actually USED. Reading
        # the raw matrix reported on maps the gate had already rejected — and,
        # worse, could pick a rejected map as "best" and report its residual as
        # the run's headline number.
        all_used = [a for row in accepted for a in row if a is not None]
        best = max(all_used, key=_map_evidence)
        for am in all_used:
            if am.provenance == "text" and am.residual_ms > 40:
                warnings.append(f"High residual alignment error: {am.residual_ms:.1f} ms")
                break

        # Auto-strategy advice: tell the user if the drift characteristics
        # suggest a different strategy than the one actually used, so they
        # can re-run cheaply (transcripts are cached — only the render
        # repeats). See PROJECT_ANALYSIS.md §10.1.
        recommended_id, reason = recommend_strategy(best)
        if recommended_id != strategy_id:
            warnings.append(
                f"Strategy {recommended_id} ({strategy_name(recommended_id)}) may suit this "
                f"recording better than the strategy {strategy_id} used: {reason}"
            )

        # count the best recorder per clip for a representative anchor total
        anchors_used = sum(max((_anchor_count(a) for a in row), default=0) for row in accepted)

        _notify("done", 1.0, "Pipeline complete")
        return SyncResult(
            fcpxml_path=output_path,
            alignment=best,
            plan=plan,
            anchors_used=anchors_used,
            warnings=warnings,
            master_wav_path=master_wav_path,
        )

    except InterruptedError:
        logger.info("Pipeline cancelled by user")
        raise
    except Exception:
        logger.exception("Pipeline failed")
        raise
    finally:
        if engine is not None:
            engine.unload()
        # Only files THIS run created, and only after the render pool has been
        # fully shut down above. The run's own scratch tree is removed by the
        # workspace context manager in run_pipeline.
        for tmp in cleanup_paths:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        for d in job_scratch_dirs:
            shutil.rmtree(d, ignore_errors=True)
