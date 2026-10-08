"""Configuration for preserved acoustic matching and boundary refinement."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AcousticSettings:
    acoustic_max_lag_s: float = 1.0
    flex_deadband_s: float = 0.008
    flex_max_shift_s: float = 0.1
    flex_min_sharpness: float = 50.0
    flex_window_s: float = 2.0
    gcc_eps: float = 1e-8
