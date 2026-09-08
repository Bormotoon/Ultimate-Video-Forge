"""Bounded subprocess logging."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from whispersync.engine.proc import run_logged


def test_returns_exit_code_and_output() -> None:
    result = run_logged(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        timeout=30,
    )
    assert result.returncode == 0
    assert "out" in result.stdout
    assert "err" in result.stderr


def test_output_is_bounded_regardless_of_how_chatty_the_child_is() -> None:
    """A separator run over a whole shoot prints a line per chunk for tens of
    minutes; the parent must not accumulate all of it in memory."""
    result = run_logged(
        [sys.executable, "-c", "print('x' * 80 * 20000)"],
        timeout=60,
        tail_bytes=4096,
    )
    assert result.returncode == 0
    assert len(result.stdout.encode()) <= 4096 + 16


def test_keeps_the_end_of_the_log() -> None:
    """Failures are explained by the last lines, not the first."""
    script = "for i in range(20000): print('line %d' % i)"
    result = run_logged([sys.executable, "-c", script], timeout=60, tail_bytes=2048)
    assert "line 19999" in result.stdout
    assert "line 0\n" not in result.stdout


def test_nonzero_exit_is_reported_not_raised() -> None:
    result = run_logged([sys.executable, "-c", "raise SystemExit(3)"], timeout=30)
    assert result.returncode == 3


def test_timeout_still_raises_timeout_expired() -> None:
    """Callers normalise this into RuntimeError; it must reach them unchanged."""
    with pytest.raises(subprocess.TimeoutExpired):
        run_logged([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.5)


def test_logs_are_cleaned_up(tmp_path: Path) -> None:
    run_logged([sys.executable, "-c", "print('hi')"], timeout=30, log_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_logs_can_be_kept_for_diagnosis(tmp_path: Path) -> None:
    result = run_logged(
        [sys.executable, "-c", "print('hi')"], timeout=30, log_dir=tmp_path, keep_logs=True
    )
    assert result.stdout_path is not None and result.stdout_path.exists()
    assert "hi" in result.stdout_path.read_text()
