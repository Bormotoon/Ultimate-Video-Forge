"""Running long external processes without holding their output in memory.

``subprocess.run(capture_output=True)`` buffers the child's ENTIRE stdout and
stderr in the parent's memory. That is fine for ``ffprobe -version`` and wrong
for the ML backends: a separator or enhancement pass over a whole shoot runs
for tens of minutes and prints a progress line per chunk, so the parent
accumulates megabytes of text it will use at most the last few hundred bytes of
— while the machine is already under memory pressure from the model itself.

``run_logged`` streams both streams to files on disk instead and returns a
bounded tail of each. Memory use is constant regardless of how chatty or
long-running the child is, and the FULL log is still on disk for as long as the
caller's directory lives, which is more than the old behaviour offered: it kept
everything in RAM and then discarded all but the slice used in an error
message.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["ProcResult", "run_logged"]

# How much of each stream to keep in memory. Errors are explained by the END of
# a log (the traceback, the last failing file), not its beginning.
DEFAULT_TAIL_BYTES = 64 * 1024


class ProcResult:
    """A finished process: exit code, bounded output tails, and log paths."""

    def __init__(
        self,
        returncode: int,
        stdout: str,
        stderr: str,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path


def _tail(path: Path, limit: int) -> str:
    """The last ``limit`` bytes of ``path``, decoded leniently."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > limit:
                fh.seek(size - limit)
            data = fh.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def run_logged(
    cmd: Sequence[str],
    *,
    timeout: float,
    log_dir: Path | None = None,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
    keep_logs: bool = False,
) -> ProcResult:
    """Run ``cmd`` with its output streamed to disk; return a bounded tail.

    ``timeout`` is honoured exactly as ``subprocess.run``'s is, and the same
    ``subprocess.TimeoutExpired`` is raised — callers normalise it into a
    ``RuntimeError`` so an optional stage can degrade rather than take the run
    down with it.

    Logs land in ``log_dir`` (a temporary directory otherwise) and are removed
    afterwards unless ``keep_logs`` is set.
    """
    workdir = log_dir or Path(tempfile.mkdtemp(prefix="ws_proclog_"))
    workdir.mkdir(parents=True, exist_ok=True)
    fd_out, out_name = tempfile.mkstemp(dir=workdir, prefix="stdout_", suffix=".log")
    fd_err, err_name = tempfile.mkstemp(dir=workdir, prefix="stderr_", suffix=".log")
    out_path, err_path = Path(out_name), Path(err_name)
    try:
        with os.fdopen(fd_out, "wb") as out_fh, os.fdopen(fd_err, "wb") as err_fh:
            completed = subprocess.run(  # noqa: S603 - argv list, never a shell
                list(cmd),
                stdout=out_fh,
                stderr=err_fh,
                timeout=timeout,
                check=False,
            )
        return ProcResult(
            completed.returncode,
            _tail(out_path, tail_bytes),
            _tail(err_path, tail_bytes),
            out_path if keep_logs else None,
            err_path if keep_logs else None,
        )
    finally:
        if not keep_logs:
            for path in (out_path, err_path):
                with contextlib.suppress(OSError):
                    path.unlink()
            if log_dir is None:
                with contextlib.suppress(OSError):
                    workdir.rmdir()
