"""Core data models for WhisperSync."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class Word:
    text: str
    start: float
    end: float
    probability: float
    norm: str = ""


@dataclass
class Segment:
    start: float
    end: float
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    source_path: Path
    language: str
    duration: float
    segments: list[Segment]

    @property
    def words(self) -> list[Word]:
        return [w for seg in self.segments for w in seg.words]


@dataclass
class Anchor:
    cam_time: float
    rec_time: float
    token: str
    confidence: float


@dataclass
class AlignmentMap:
    """``t_camera = offset + k * t_recorder`` — one clip's clock map.

    ``provenance`` records HOW the map was derived, because "how many anchors
    does it have" is not the same question as "is it any good": the acoustic
    fallback produces a perfectly usable map with an EMPTY anchor list, and
    code that measured confidence by ``len(anchors)`` therefore threw away
    every successful acoustic match while happily accepting a two-anchor text
    fit (through two points any line fits with ~zero residual, including a
    completely wrong one). ``evidence`` carries the numbers a gate needs —
    see ``matcher.evaluate_alignment``.
    """

    anchors: list[Anchor]
    offset: float
    k: float
    residual_ms: float
    # "text" (transcript word anchors), "acoustic" (GCC-PHAT waveform scan),
    # or "repair" (a local re-alignment of one flagged span).
    provenance: str = "text"
    # Anchors that survived the robust line fit (<= len(anchors)). For an
    # acoustic map this is the number of confident grid points instead.
    inliers: int = 0
    # Recorder-time extent the supporting evidence actually spans, in seconds.
    # A map fitted from evidence covering 3 s of a 10-minute clip is an
    # extrapolation, however small its residual.
    evidence_span_s: float = 0.0


@dataclass
class Take:
    """One recorded attempt at a line: a span (in some clip's LOCAL time —
    seconds from that clip's own start) and its transcript text."""

    start: float
    end: float
    text: str


@dataclass
class RetakeGroup:
    """Two or more consecutive attempts at the same line, chronological.

    ``keeper_index`` is the take to leave active (the last attempt by default —
    the one the speaker settled on before moving past it). Produced by
    ``engine.retakes.detect_retakes`` and, once converted to a clip's local
    time, attached to that ``MediaClip`` for the FCPXML exporter to render as
    a Final Cut *audition* (the alternatives stacked under one active pick).
    """

    takes: list[Take]
    keeper_index: int = -1

    @property
    def keeper(self) -> Take:
        return self.takes[self.keeper_index]

    @property
    def span(self) -> tuple[float, float]:
        return self.takes[0].start, self.takes[-1].end


@dataclass
class MediaClip:
    path: Path
    kind: Literal["video", "audio"]
    offset: float
    in_point: float
    duration: float
    lane: int
    # Optional FCPXML display name (falls back to the file stem) and FCPX role
    # (e.g. "Dialogue", "Effects", "Video") so the editor colours/groups clips.
    display_name: str | None = None
    role: str | None = None
    # Retake groups within THIS clip's own local time ([0, duration)), for
    # audio clips where detect_retakes found re-recorded lines. The exporter
    # renders each group as MARKERS on the clip (see engine/export.py) — a
    # review aid that leaves the synchronised A/V untouched.
    retake_groups: list[RetakeGroup] | None = None
    # Whether this clip's OWN audio should play. A camera clip whose dialogue
    # has been replaced by a synced recorder track must be video-only: leaving
    # its built-in mic enabled puts two copies of the same voice, tens of
    # milliseconds apart, on the timeline — the comb-filtered "doubled voice"
    # the whole ambience feature exists to avoid. Left enabled for clips with
    # no replacement, so unresolved footage keeps some usable sound.
    # None = decide from context (the exporter treats it as enabled).
    source_audio_enabled: bool | None = None
    # For a RENDERED audio clip: which source it was made from and which
    # stretch of that source it covers, as
    # ``(source_path, source_start_s, source_duration_s)``.
    #
    # This is what post-run verification needs and could not previously get.
    # Pairing was done by ``display_name.startswith(video_stem)``, which
    # matched "A1" against "A10", collapsed two cameras' identically named
    # clips into one dictionary entry, and — for a voice SEGMENT covering
    # minutes 5-10 — compared the start of that segment against the start of
    # the video. The measurement then reported the resulting five-minute
    # disagreement as lip-sync lag.
    source_ref: tuple[Path, float, float] | None = None


@dataclass
class SyncPlan:
    strategy_id: int
    clips: list[MediaClip]
    total_duration: float


@dataclass
class SyncResult:
    fcpxml_path: Path
    alignment: AlignmentMap
    plan: SyncPlan
    anchors_used: int
    warnings: list[str]
    master_wav_path: Path | None = None
