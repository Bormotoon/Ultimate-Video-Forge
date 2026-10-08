"""Preserved thresholds for content diagnostics and local repair alignment."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SelfCheckSettings:
    self_check_mode: str = "off"
    min_anchors: int = 8
    self_check_min_run_words: int = 5
    self_check_shift_threshold_s: float = 0.35
    self_check_min_content_words: int = 5
    anchor_min_confidence: float = 0.6
    alignment_max_k_deviation: float = 0.05
    alignment_max_residual_ms: float = 250.0
    acoustic_max_lag_s: float = 1.0
    acoustic_fallback_min_sharpness: float = 50.0
    gcc_eps: float = 1e-8
    boundary_flex: bool = False
