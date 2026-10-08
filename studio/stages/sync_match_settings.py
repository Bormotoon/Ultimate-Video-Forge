"""Matching defaults preserved from the frozen WhisperSync configuration."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MatchSettings:
    min_anchors: int = 8
    anchor_min_confidence: float = 0.6
    match_window_margin: float = 90.0
    seed_max_occurrences: int = 50
    seed_bin_width: float = 2.0
    align_mode: str = "auto"
    alignment_max_k_deviation: float = 0.05
    alignment_max_residual_ms: float = 250.0
    alignment_min_coverage: float = 0.25
    alignment_min_acoustic_points: int = 3
