"""Typed conversions between source, timeline, edited, and rendered time."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, TypeVar

from studio.core.enums import StrEnum


class TimeDomain(StrEnum):
    FILE = "file"
    TIMELINE = "timeline"
    EDITED = "edited"
    RENDERED_AUDIO = "rendered_audio"


@dataclass(frozen=True, slots=True)
class SourcePlacement:
    asset_id: str
    offset_s: float
    in_s: float
    duration_s: float
    k: float = 1.0
    provenance: str = "manual"
    evidence: dict[str, float] | None = None

    def __post_init__(self) -> None:
        _finite(self.offset_s, self.in_s, self.duration_s, self.k)
        if self.in_s < 0 or self.duration_s <= 0 or self.k <= 0:
            raise ValueError("placement in/duration/k must be non-negative, positive, positive")


@dataclass(frozen=True, slots=True)
class AudioWarpPiece:
    source_start_s: float
    source_duration_s: float
    rendered_start_s: float
    rendered_duration_s: float
    method: str

    def __post_init__(self) -> None:
        _finite(
            self.source_start_s,
            self.source_duration_s,
            self.rendered_start_s,
            self.rendered_duration_s,
        )
        if min(self.source_start_s, self.rendered_start_s) < 0:
            raise ValueError("warp starts must be non-negative")
        if self.source_duration_s <= 0 or self.rendered_duration_s <= 0:
            raise ValueError("warp durations must be positive")

    @property
    def factor(self) -> float:
        return self.source_duration_s / self.rendered_duration_s


@dataclass(frozen=True, slots=True)
class AudioWarpMap:
    id: str
    source_asset_id: str
    target_asset_id: str | None
    pieces: tuple[AudioWarpPiece, ...]
    source_domain: TimeDomain
    strategy: int
    evidence: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if self.source_domain not in (TimeDomain.FILE, TimeDomain.TIMELINE):
            raise ValueError("audio warp source domain must be file or timeline")
        if not self.pieces:
            raise ValueError("audio warp map must contain at least one piece")
        _validate_warp_pieces(self.pieces)

    @property
    def rendered_duration_s(self) -> float:
        last = self.pieces[-1]
        return last.rendered_start_s + last.rendered_duration_s


@dataclass(frozen=True, slots=True)
class KeepRange:
    start_s: float
    end_s: float
    camera_id: str | None = None

    def __post_init__(self) -> None:
        _finite(self.start_s, self.end_s)
        if self.start_s < 0 or self.end_s <= self.start_s:
            raise ValueError("keep range must have 0 <= start < end")


@dataclass(frozen=True, slots=True)
class EditMap:
    id: str
    keep: tuple[KeepRange, ...]

    def __post_init__(self) -> None:
        previous_end = -math.inf
        for item in self.keep:
            if item.start_s < previous_end:
                raise ValueError("keep ranges must be ordered and non-overlapping")
            previous_end = item.end_s


class Timed(Protocol):
    start: float
    end: float


T = TypeVar("T", bound=Timed)


def file_to_timeline(value_s: float, placement: SourcePlacement) -> float:
    _finite(value_s)
    return placement.offset_s + (value_s - placement.in_s) * placement.k


def timeline_to_file(value_s: float, placement: SourcePlacement) -> float:
    _finite(value_s)
    return placement.in_s + (value_s - placement.offset_s) / placement.k


def audio_source_to_rendered(value_s: float, warp: AudioWarpMap) -> float | None:
    piece = _piece_for_source(value_s, warp.pieces)
    if piece is None:
        return None
    ratio = piece.rendered_duration_s / piece.source_duration_s
    return piece.rendered_start_s + (value_s - piece.source_start_s) * ratio


def rendered_to_audio_source(value_s: float, warp: AudioWarpMap) -> float | None:
    piece = _piece_for_rendered(value_s, warp.pieces)
    if piece is None:
        return None
    ratio = piece.source_duration_s / piece.rendered_duration_s
    return piece.source_start_s + (value_s - piece.rendered_start_s) * ratio


def timeline_to_edited(value_s: float, edit: EditMap) -> float | None:
    _finite(value_s)
    elapsed = 0.0
    for item in edit.keep:
        if item.start_s <= value_s <= item.end_s:
            return elapsed + value_s - item.start_s
        elapsed += item.end_s - item.start_s
    return None


def edited_to_timeline(value_s: float, edit: EditMap) -> float | None:
    _finite(value_s)
    if value_s < 0:
        return None
    elapsed = 0.0
    for item in edit.keep:
        duration = item.end_s - item.start_s
        if elapsed <= value_s <= elapsed + duration:
            return item.start_s + value_s - elapsed
        elapsed += duration
    return None


def map_words(words: list[T], mapper: object) -> list[tuple[T, float, float]]:
    if not callable(mapper):
        raise TypeError("mapper must be callable")
    result: list[tuple[T, float, float]] = []
    for word in words:
        start = mapper(word.start)
        end = mapper(word.end)
        if start is not None and end is not None and end >= start:
            result.append((word, start, end))
    return result


def _finite(*values: float) -> None:
    if not all(math.isfinite(value) for value in values):
        raise ValueError("time values must be finite")


def _validate_warp_pieces(pieces: tuple[AudioWarpPiece, ...]) -> None:
    tolerance = 1e-6
    for index, piece in enumerate(pieces):
        if index == 0:
            continue
        previous = pieces[index - 1]
        source_end = previous.source_start_s + previous.source_duration_s
        rendered_end = previous.rendered_start_s + previous.rendered_duration_s
        if abs(piece.source_start_s - source_end) > tolerance:
            raise ValueError("audio warp source pieces must be continuous")
        if abs(piece.rendered_start_s - rendered_end) > tolerance:
            raise ValueError("audio warp rendered pieces must be continuous")


def _piece_for_source(
    value_s: float, pieces: tuple[AudioWarpPiece, ...]
) -> AudioWarpPiece | None:
    _finite(value_s)
    for piece in pieces:
        if piece.source_start_s <= value_s <= piece.source_start_s + piece.source_duration_s:
            return piece
    return None


def _piece_for_rendered(
    value_s: float, pieces: tuple[AudioWarpPiece, ...]
) -> AudioWarpPiece | None:
    _finite(value_s)
    for piece in pieces:
        if piece.rendered_start_s <= value_s <= piece.rendered_start_s + piece.rendered_duration_s:
            return piece
    return None
