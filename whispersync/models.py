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
    anchors: list[Anchor]
    offset: float
    k: float
    residual_ms: float


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
    # renders each group as an <audition> instead of a plain asset-clip.
    retake_groups: list[RetakeGroup] | None = None


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
