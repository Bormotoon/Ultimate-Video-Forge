"""Realized lip-sync measurement between a source clip and a rendered voice.

Cross-correlate a short window of the video's own audio against the rendered
voice track at points across the clip (GCC-PHAT, the same function Boundary
Flex uses) and report the realized lag distribution. A sharp, confident peak
means "this is genuinely how far off the two tracks are" rather than
correlation noise, so unconfident windows are rejected.

This lives in ``whispersync.engine`` — not in ``tools/`` — because it is part
of the shipped product: ``--verify`` imports it at the END of a sync run. With
the implementation in ``tools/``, which package discovery does not include, a
wheel-installed WhisperSync raised ``ModuleNotFoundError`` at that point, after
the entire (expensive) synchronisation had already completed. ``tools/
verify_sync.py`` is now a thin CLI wrapper around this module.

Two things the reporting gets right that are easy to get wrong:

* **Coverage is not the same as accuracy.** A rejected window is a window the
  measurement could not evaluate, not one that measured zero. Rejections are
  counted, so "12 of 40 windows were usable" cannot be presented as "12/12
  confident".
* **A median over a handful of clustered windows is not a verdict.** ``passed``
  additionally requires enough independent measurements, spread over enough of
  the clip, to mean anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from studio.stages.sync_acoustic import (
    _REFINE_SR,
    _window_slice,
    gcc_phat,
    load_mono16k_track,
)

__all__ = [
    "DEFAULT_GCC_EPS",
    "DEFAULT_GRID_S",
    "DEFAULT_MAX_LAG_S",
    "DEFAULT_MIN_SHARPNESS",
    "DEFAULT_WINDOW_S",
    "LagSample",
    "VerifyReport",
    "measure",
    "validate_parameters",
]

DEFAULT_GRID_S = 5.0
DEFAULT_WINDOW_S = 4.0
DEFAULT_MAX_LAG_S = 1.0
DEFAULT_MIN_SHARPNESS = 50.0
DEFAULT_GCC_EPS = 1e-8

# A pass needs more than a small median: at least this many usable windows,
# spread over at least this fraction of the measured span. Two well-correlated
# windows three seconds apart in a ten-minute clip say nothing about the
# other 597 seconds.
DEFAULT_MIN_CONFIDENT = 3
DEFAULT_MIN_SPAN_RATIO = 0.5


@dataclass
class LagSample:
    t: float
    lag_ms: float
    sharpness: float


@dataclass
class VerifyReport:
    video: str
    voice: str
    # Every window that was measured, confident or not.
    samples: list[LagSample] = field(default_factory=list)
    # Windows attempted but rejected, with the reason, so coverage is honest.
    rejected: list[tuple[float, str]] = field(default_factory=list)
    duration_s: float = 0.0
    min_sharpness: float = DEFAULT_MIN_SHARPNESS

    @property
    def confident(self) -> list[LagSample]:
        return [s for s in self.samples if s.sharpness >= self.min_sharpness]

    @property
    def attempted(self) -> int:
        """Windows the measurement TRIED, including the ones it could not use.

        The old report dropped unconfident windows before counting, then
        reported ``n_confident/n_total`` from what survived — so it always read
        N/N. Three windows with sharpness [100, 1, 1] reported "1/1 confident"
        instead of 1/3, turning a clip that could barely be measured into a
        clip that measured perfectly.
        """
        return len(self.samples) + len(self.rejected)

    @property
    def coverage_span_s(self) -> float:
        """Time between the first and last CONFIDENT window."""
        times = [s.t for s in self.confident]
        return (max(times) - min(times)) if len(times) >= 2 else 0.0

    def summary(self) -> dict[str, float | int | str]:
        confident = self.confident
        base: dict[str, float | int | str] = {
            "n_confident": len(confident),
            "n_attempted": self.attempted,
            "n_rejected": len(self.rejected),
            "duration_s": round(self.duration_s, 3),
            "coverage_span_s": round(self.coverage_span_s, 3),
            "coverage_ratio": (
                round(self.coverage_span_s / self.duration_s, 3) if self.duration_s > 0 else 0.0
            ),
        }
        if not confident:
            return base
        lags = sorted(float(abs(s.lag_ms)) for s in confident)
        n = len(lags)
        base.update(
            {
                "median_abs_lag_ms": lags[n // 2],
                "p90_abs_lag_ms": lags[min(n - 1, int(n * 0.9))],
                "max_abs_lag_ms": lags[-1],
            }
        )
        return base

    def verdict(
        self,
        median_threshold_ms: float,
        min_confident: int = DEFAULT_MIN_CONFIDENT,
        min_span_ratio: float = DEFAULT_MIN_SPAN_RATIO,
    ) -> tuple[str, str]:
        """``(status, reason)`` where status is passed/failed/inconclusive.

        ``inconclusive`` exists because "we could not measure this" and "this
        measured well" are different answers, and only one of them justifies
        shipping the render.
        """
        summary = self.summary()
        confident = int(summary["n_confident"])
        if confident < min_confident:
            return (
                "inconclusive",
                f"only {confident} usable window(s) out of {self.attempted} attempted "
                f"(need {min_confident}) — the two tracks correlate too weakly to measure",
            )
        ratio = float(summary["coverage_ratio"])
        if ratio < min_span_ratio:
            return (
                "inconclusive",
                f"usable windows span only {ratio:.0%} of the clip "
                f"(need {min_span_ratio:.0%}) — the rest was never measured",
            )
        median = float(summary["median_abs_lag_ms"])
        if median > median_threshold_ms:
            return "failed", f"median |lag| {median:.1f} ms exceeds {median_threshold_ms:.1f} ms"
        return "passed", f"median |lag| {median:.1f} ms over {confident} window(s)"


def validate_parameters(
    grid_s: float,
    window_s: float,
    max_lag_s: float = DEFAULT_MAX_LAG_S,
    min_sharpness: float = DEFAULT_MIN_SHARPNESS,
    median_threshold_ms: float = 20.0,
) -> None:
    """Reject parameters that cannot produce a measurement.

    ``--grid-s 0`` left the sampling cursor where it was and a negative grid
    walked it backwards, so the loop's exit condition was never reached: the
    tool sampled the same instant forever, growing the sample list without
    bound. Every value here is checked for finiteness and range at the entry
    point, so an impossible request is an immediate error rather than a hang.
    """
    import math

    checks = (
        ("grid_s", grid_s, True),
        ("window_s", window_s, True),
        ("max_lag_s", max_lag_s, True),
        ("min_sharpness", min_sharpness, False),
        ("median_threshold_ms", median_threshold_ms, False),
    )
    for name, value, strictly_positive in checks:
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite number, got {value!r}")
        if strictly_positive and value <= 0:
            raise ValueError(f"{name} must be greater than 0, got {value!r}")
        if not strictly_positive and value < 0:
            raise ValueError(f"{name} must not be negative, got {value!r}")


def measure(
    video_audio_path: Path,
    voice_path: Path,
    grid_s: float = DEFAULT_GRID_S,
    window_s: float = DEFAULT_WINDOW_S,
    max_lag_s: float = DEFAULT_MAX_LAG_S,
    min_sharpness: float = DEFAULT_MIN_SHARPNESS,
    source_start_s: float = 0.0,
    source_duration_s: float | None = None,
    voice_start_s: float = 0.0,
) -> VerifyReport:
    """Cross-correlate a source's audio against a rendered voice on a time grid.

    ``source_start_s``/``source_duration_s`` select the stretch of the SOURCE
    that ``voice_path`` corresponds to, and ``voice_start_s`` the offset within
    the voice file. They matter for segmented voice output: comparing the start
    of a second voice segment against the start of the video measures two
    unrelated minutes of audio and reports the disagreement as lag. Every
    measured window — confident or not — is recorded, so coverage can be
    reported honestly.
    """
    validate_parameters(grid_s, window_s, max_lag_s, min_sharpness)

    video_track = load_mono16k_track(
        video_audio_path, start_s=source_start_s or None, duration_s=source_duration_s
    )
    voice_track = load_mono16k_track(voice_path, start_s=voice_start_s or None)
    duration_s = min(len(video_track), len(voice_track)) / _REFINE_SR

    report = VerifyReport(
        video=str(video_audio_path),
        voice=str(voice_path),
        duration_s=duration_s,
        min_sharpness=min_sharpness,
    )
    half = window_s / 2.0
    t = half
    while t <= duration_s - half:
        video_win = _window_slice(video_track, t, window_s, _REFINE_SR)
        voice_win = _window_slice(voice_track, t, window_s, _REFINE_SR)
        lag_s, sharp = gcc_phat(video_win, voice_win, _REFINE_SR, max_lag_s, DEFAULT_GCC_EPS)
        if sharp >= min_sharpness:
            report.samples.append(LagSample(t=t, lag_ms=lag_s * 1000.0, sharpness=sharp))
        else:
            report.rejected.append((t, f"correlation peak too weak (sharpness {sharp:.1f})"))
        t += grid_s
    return report
