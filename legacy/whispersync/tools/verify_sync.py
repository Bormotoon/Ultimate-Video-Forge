#!/usr/bin/env python3
"""Command-line front end for :mod:`whispersync.engine.verify`.

Measures realized audio/video lip-sync lag between a rendered voice track and
its source video, independent of the pipeline that produced it — the harness
this project's flex-sync debugging was done with.

Usage:
    python -m tools.verify_sync --video DJI_0830.MOV --voice DJI_0830_voice.wav
    python -m tools.verify_sync --video DJI_0830.MOV --voice DJI_0830_voice.wav --json

Exit code: 0 when the measurement passes (median |lag| within
--median-threshold-ms AND enough independent, well-spread windows to mean
anything), 1 when it fails, 2 when it is inconclusive or the arguments are
invalid. "Inconclusive" is deliberately not 0: a clip that could not be
measured has not been shown to be in sync.

The MEASUREMENT itself lives in ``whispersync/engine/verify.py``, not here.
This file is a thin wrapper: ``--verify`` inside the CLI imports the engine
module, and while the implementation lived in ``tools/`` — a directory package
discovery does not ship — a wheel-installed WhisperSync raised
ModuleNotFoundError at the end of a completed sync run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Importable both as `python -m tools.verify_sync` from a checkout and as a
# standalone script; the installed package provides the engine either way.
if __package__ in (None, ""):  # pragma: no cover - script-mode convenience
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from whispersync.engine.verify import (  # noqa: E402
    DEFAULT_GRID_S,
    DEFAULT_MIN_SHARPNESS,
    DEFAULT_WINDOW_S,
    VerifyReport,
    measure,
    validate_parameters,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNUSABLE = 2


def _print_table(report: VerifyReport) -> None:
    print(f"video: {report.video}")
    print(f"voice: {report.voice}")
    print()
    if not report.confident:
        print(
            f"No confident correlation points found "
            f"({report.attempted} window(s) attempted, all rejected)."
        )
        return
    print(f"{'t (s)':>8}  {'lag (ms)':>10}  {'sharpness':>10}")
    for s in report.confident:
        print(f"{s.t:8.1f}  {s.lag_ms:10.1f}  {s.sharpness:10.1f}")
    print()
    summary = report.summary()
    print(
        f"usable windows: {summary['n_confident']}/{summary['n_attempted']}  "
        f"(rejected {summary['n_rejected']}; covering "
        f"{float(summary['coverage_ratio']):.0%} of the clip)  "
        f"median |lag|: {float(summary.get('median_abs_lag_ms', 0)):.1f} ms  "
        f"p90: {float(summary.get('p90_abs_lag_ms', 0)):.1f} ms  "
        f"max: {float(summary.get('max_abs_lag_ms', 0)):.1f} ms"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_sync",
        description=(
            "Measure realized lip-sync lag between a video's own audio and a "
            "rendered synced voice track, via GCC-PHAT cross-correlation."
        ),
    )
    parser.add_argument("--video", required=True, type=Path, help="Source video/audio file")
    parser.add_argument("--voice", required=True, type=Path, help="Rendered synced voice WAV")
    parser.add_argument("--grid-s", type=float, default=DEFAULT_GRID_S, help="Grid spacing (s)")
    parser.add_argument(
        "--window-s", type=float, default=DEFAULT_WINDOW_S, help="Correlation window (s)"
    )
    parser.add_argument(
        "--min-sharpness",
        type=float,
        default=DEFAULT_MIN_SHARPNESS,
        help="Reject points below this confidence",
    )
    parser.add_argument(
        "--median-threshold-ms",
        type=float,
        default=20.0,
        help="Fail if median |lag| exceeds this",
    )
    parser.add_argument(
        "--source-start-s",
        type=float,
        default=0.0,
        help="Where in the source the voice track begins (for voice segments)",
    )
    parser.add_argument(
        "--source-duration-s",
        type=float,
        default=None,
        help="How much of the source the voice track covers",
    )
    parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        # Validate BEFORE decoding anything: `--grid-s 0` used to leave the
        # sampling cursor in place and loop forever, and a negative grid walked
        # it backwards past the exit condition. argparse cannot catch that on
        # its own, so the check belongs here and in the engine's entry point.
        validate_parameters(
            args.grid_s,
            args.window_s,
            min_sharpness=args.min_sharpness,
            median_threshold_ms=args.median_threshold_ms,
        )
        report = measure(
            args.video,
            args.voice,
            grid_s=args.grid_s,
            window_s=args.window_s,
            min_sharpness=args.min_sharpness,
            source_start_s=args.source_start_s,
            source_duration_s=args.source_duration_s,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE

    status, reason = report.verdict(args.median_threshold_ms)

    if args.json_output:
        print(
            json.dumps(
                {
                    "video": report.video,
                    "voice": report.voice,
                    "status": status,
                    "reason": reason,
                    "samples": [
                        {"t": s.t, "lag_ms": s.lag_ms, "sharpness": s.sharpness}
                        for s in report.confident
                    ],
                    "rejected": [{"t": t, "reason": why} for t, why in report.rejected],
                    "summary": report.summary(),
                },
                indent=2,
            )
        )
    else:
        _print_table(report)
        print(f"\n{status.upper()}: {reason}")

    if status == "passed":
        return EXIT_OK
    if status == "failed":
        return EXIT_FAILED
    return EXIT_UNUSABLE


if __name__ == "__main__":
    sys.exit(main())
