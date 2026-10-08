"""Structural inputs to the preserved piece-planning algorithms."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class Anchor(Protocol):
    @property
    def rec_time(self) -> float: ...

    @property
    def cam_time(self) -> float: ...


class AlignmentMap(Protocol):
    @property
    def anchors(self) -> Sequence[Anchor]: ...

    @property
    def offset(self) -> float: ...

    @property
    def k(self) -> float: ...


class PieceSettings(Protocol):
    @property
    def phrase_gap_threshold(self) -> float: ...

    @property
    def seam_snap_max_s(self) -> float: ...


@dataclass(frozen=True, slots=True)
class PieceConfig:
    phrase_gap_threshold: float = 0.6
    seam_snap_max_s: float = 0.4
