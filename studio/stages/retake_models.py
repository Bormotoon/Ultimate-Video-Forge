"""Dependency-free retake contracts, preserved from WhisperSync."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RetakeSettings:
    detect_retakes: bool = False
    retake_min_words: int = 4
    retake_max_gap_s: float = 6.0
    phrase_gap_threshold: float = 0.6


@dataclass
class Take:
    start: float
    end: float
    text: str


@dataclass
class RetakeGroup:
    takes: list[Take]
    keeper_index: int = -1

    @property
    def keeper(self) -> Take:
        return self.takes[self.keeper_index]

    @property
    def span(self) -> tuple[float, float]:
        return self.takes[0].start, self.takes[-1].end
