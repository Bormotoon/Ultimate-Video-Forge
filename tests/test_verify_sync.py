"""Tests for the lip-sync verification report and CLI (pure logic — no ffmpeg)."""

from __future__ import annotations

import pytest

from tools import verify_sync
from whispersync.engine.verify import LagSample, VerifyReport, measure, validate_parameters


def test_summary_empty_report() -> None:
    report = VerifyReport(video="v", voice="a")
    summary = report.summary()
    assert summary["n_confident"] == 0
    assert summary["n_attempted"] == 0


def test_summary_computes_median_p90_max() -> None:
    report = VerifyReport(
        video="v",
        voice="a",
        duration_s=4.0,
        samples=[
            LagSample(t=0.0, lag_ms=10.0, sharpness=100.0),
            LagSample(t=1.0, lag_ms=-20.0, sharpness=100.0),
            LagSample(t=2.0, lag_ms=5.0, sharpness=100.0),
            LagSample(t=3.0, lag_ms=-50.0, sharpness=100.0),
        ],
    )
    summary = report.summary()
    assert summary["n_confident"] == 4
    assert summary["n_attempted"] == 4
    # abs lags sorted: [5, 10, 20, 50] -> median index 2 -> 20
    assert summary["median_abs_lag_ms"] == 20.0
    assert summary["max_abs_lag_ms"] == 50.0


def test_coverage_counts_rejected_windows() -> None:
    """A window that could not be measured is not a window that measured zero.

    The old report discarded unconfident windows before counting, then printed
    ``n_confident/n_total`` from the survivors — so it always read N/N. Three
    windows with sharpness [100, 1, 1] reported "1/1 confident" instead of 1/3,
    presenting a barely measurable clip as a perfectly measured one.
    """
    report = VerifyReport(
        video="v",
        voice="a",
        duration_s=10.0,
        samples=[LagSample(t=0.0, lag_ms=1.0, sharpness=100.0)],
        rejected=[(5.0, "too weak"), (10.0, "too weak")],
    )
    summary = report.summary()
    assert summary["n_confident"] == 1
    assert summary["n_attempted"] == 3
    assert summary["n_rejected"] == 2


def test_confident_filters_by_sharpness() -> None:
    report = VerifyReport(
        video="v",
        voice="a",
        min_sharpness=50.0,
        samples=[
            LagSample(t=0.0, lag_ms=1.0, sharpness=200.0),
            LagSample(t=1.0, lag_ms=1.0, sharpness=10.0),
        ],
    )
    assert [s.t for s in report.confident] == [0.0]


# --- verdict ---------------------------------------------------------------


def test_verdict_passes_on_well_covered_low_lag() -> None:
    report = VerifyReport(
        video="v",
        voice="a",
        duration_s=100.0,
        samples=[LagSample(t=float(t), lag_ms=5.0, sharpness=100.0) for t in (0, 40, 80, 100)],
    )
    status, _reason = report.verdict(median_threshold_ms=20.0)
    assert status == "passed"


def test_verdict_is_inconclusive_with_too_few_windows() -> None:
    """A small median over two windows is not a verdict about the clip."""
    report = VerifyReport(
        video="v",
        voice="a",
        duration_s=600.0,
        samples=[LagSample(t=1.0, lag_ms=1.0, sharpness=100.0)],
        rejected=[(t, "too weak") for t in range(2, 120)],
    )
    status, reason = report.verdict(median_threshold_ms=20.0)
    assert status == "inconclusive"
    assert "usable window" in reason


def test_verdict_is_inconclusive_when_windows_are_clustered() -> None:
    """Perfect agreement across three seconds of a ten-minute clip says
    nothing about the other 597 seconds."""
    report = VerifyReport(
        video="v",
        voice="a",
        duration_s=600.0,
        samples=[LagSample(t=float(t), lag_ms=2.0, sharpness=100.0) for t in (1, 2, 3, 4)],
    )
    status, reason = report.verdict(median_threshold_ms=20.0)
    assert status == "inconclusive"
    assert "span" in reason


def test_verdict_fails_on_large_lag() -> None:
    report = VerifyReport(
        video="v",
        voice="a",
        duration_s=100.0,
        samples=[LagSample(t=float(t), lag_ms=250.0, sharpness=100.0) for t in (0, 40, 80, 100)],
    )
    status, _reason = report.verdict(median_threshold_ms=20.0)
    assert status == "failed"


# --- parameter validation --------------------------------------------------


@pytest.mark.parametrize("grid", [0.0, -5.0, float("nan"), float("inf")])
def test_zero_or_negative_grid_is_rejected(grid: float) -> None:
    """`--grid-s 0` left the sampling cursor in place and a negative grid
    walked it backwards, so the loop's exit condition was never reached: the
    tool sampled one instant forever while the sample list grew without bound.
    """
    with pytest.raises(ValueError, match="grid_s"):
        validate_parameters(grid, 4.0)


@pytest.mark.parametrize("window", [0.0, -1.0, float("nan")])
def test_invalid_window_is_rejected(window: float) -> None:
    with pytest.raises(ValueError, match="window_s"):
        validate_parameters(5.0, window)


def test_negative_thresholds_are_rejected() -> None:
    with pytest.raises(ValueError, match="min_sharpness"):
        validate_parameters(5.0, 4.0, min_sharpness=-1.0)
    with pytest.raises(ValueError, match="median_threshold_ms"):
        validate_parameters(5.0, 4.0, median_threshold_ms=-1.0)


def test_measure_rejects_bad_grid_before_decoding(tmp_path) -> None:
    """The check must happen before any file is opened, so an impossible
    request fails immediately instead of after a multi-hour decode."""
    with pytest.raises(ValueError, match="grid_s"):
        measure(tmp_path / "nope.mov", tmp_path / "nope.wav", grid_s=0.0)


def test_cli_reports_invalid_arguments_as_exit_2() -> None:
    code = verify_sync.main(["--video", "v.mov", "--voice", "a.wav", "--grid-s", "0"])
    assert code == verify_sync.EXIT_UNUSABLE


# --- packaging -------------------------------------------------------------


def test_measurement_api_lives_in_the_shipped_package() -> None:
    """`--verify` imports this at the END of a sync run.

    While the implementation lived in ``tools/`` — which package discovery does
    not include — a wheel-installed WhisperSync raised ModuleNotFoundError at
    that point, after the whole expensive synchronisation had completed.
    """
    import whispersync.engine.verify as engine_verify

    assert engine_verify.__name__.startswith("whispersync.")
    assert verify_sync.measure is engine_verify.measure
