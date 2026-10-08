"""Run isolation: who owns which files, and when a result becomes visible.

Two runs pointed at the same output folder used to share ``audio_synced/``,
``.master/`` and ``enhance_tmp/`` — every ffmpeg invocation carrying ``-y``.
The second run therefore overwrote the audio the FIRST run's FCPXML still
referenced, its cleanup deleted scratch the first run was still reading, and a
crash halfway through left a half-written WAV standing in for a previously
good one. Nothing in the old layout distinguished "finished result", "result
being written right now" and "leftovers from a run that died".

This module supplies the three pieces that fix that:

* :class:`RunWorkspace` — one owner for a run's scratch. Scratch lives in a
  private, uniquely named directory (so a concurrent run can never collide
  with, or clean up, another's temporaries) and is removed as a whole.
* :func:`published` — write to a temporary file beside the destination, then
  ``os.replace`` it into place. The replace is atomic on the same filesystem,
  so a reader either sees the previous complete file or the new complete file,
  never a truncated one. A failure leaves the previous file untouched.
* :func:`output_lock` — a cooperative lock over one output namespace, so two
  runs writing the same folder is a clear error rather than silent
  interleaving.
"""

from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["OutputLockedError", "RunWorkspace", "output_lock", "published"]

# A lock whose owning process is gone and whose file is older than this is
# treated as abandoned (a crashed run) and taken over.
_STALE_LOCK_AGE_S = 6 * 3600.0


class OutputLockedError(RuntimeError):
    """Another WhisperSync run holds the lock on this output directory."""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by another user
        return True
    except OSError:
        return True
    return True


@contextmanager
def published(final_path: Path, *, suffix: str = "") -> Iterator[Path]:
    """Yield a temporary path; on clean exit, atomically move it onto ``final_path``.

    The temporary lives in ``final_path``'s own directory, which is what makes
    the final ``os.replace`` atomic (a cross-filesystem move is a copy and can
    be observed half-done). On an exception the temporary is removed and any
    pre-existing ``final_path`` is left exactly as it was — a failed run never
    degrades an earlier good result.
    """
    final_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=final_path.parent,
        prefix=f".{final_path.stem}.",
        suffix=suffix or final_path.suffix or ".tmp",
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        yield tmp_path
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
    if not tmp_path.exists():
        raise RuntimeError(f"nothing was written for {final_path}")
    os.replace(tmp_path, final_path)


@contextmanager
def output_lock(output_dir: Path, *, timeout_s: float = 0.0) -> Iterator[Path]:
    """Hold an exclusive cooperative lock over ``output_dir`` for this run.

    Implemented as an ``O_EXCL`` lock file holding the owner's pid and start
    time, so it works the same on every platform this app targets and needs no
    extra dependency. A lock whose owner process no longer exists (or which is
    older than ``_STALE_LOCK_AGE_S``) is taken over — a crashed run must not
    lock a folder forever.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / ".whispersync-run.lock"
    payload = json.dumps({"pid": os.getpid(), "started": time.time()})
    deadline = time.monotonic() + max(0.0, timeout_s)

    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, payload.encode())
            os.close(fd)
            break
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            if _take_over_stale(lock_path):
                continue
            if time.monotonic() >= deadline:
                raise OutputLockedError(
                    f"Another WhisperSync run is writing to {output_dir} "
                    f"(lock: {lock_path}). Use a different output folder, or "
                    "wait for it to finish; delete the lock file only if you "
                    "are sure no run is active."
                ) from None
            time.sleep(0.25)

    try:
        yield lock_path
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()


def _take_over_stale(lock_path: Path) -> bool:
    """Remove ``lock_path`` if its owner is gone. True when it was taken over."""
    try:
        raw = lock_path.read_text()
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return False
    pid = 0
    with contextlib.suppress(ValueError, TypeError, json.JSONDecodeError):
        pid = int(json.loads(raw).get("pid", 0))
    if _pid_alive(pid) and age < _STALE_LOCK_AGE_S:
        return False
    logger.warning("Taking over stale run lock %s (pid %s, %.0f s old)", lock_path, pid, age)
    with contextlib.suppress(OSError):
        lock_path.unlink()
    return True


class RunWorkspace:
    """Owner of one run's scratch space and output namespace.

    ``scratch`` is a private directory (unique per run) on the OUTPUT volume:
    intermediate piece WAVs are the same order of size as the final render, so
    ``/tmp`` — often a small tmpfs — is the wrong place for them, but sharing a
    fixed directory name across runs is what let one run delete another's
    working files. A unique directory gives both.
    """

    def __init__(self, output_dir: Path, *, run_id: str | None = None) -> None:
        self.output_dir = Path(output_dir)
        self.run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.scratch = self.output_dir / f".whispersync-run-{self.run_id}"
        self.scratch.mkdir(parents=True, exist_ok=True)
        self._subdirs: dict[str, Path] = {}

    # -- scratch -----------------------------------------------------------
    def scratch_dir(self, name: str) -> Path:
        """A named subdirectory of this run's private scratch (created once)."""
        existing = self._subdirs.get(name)
        if existing is not None:
            return existing
        path = self.scratch / name
        path.mkdir(parents=True, exist_ok=True)
        self._subdirs[name] = path
        return path

    def temp_dir(self, prefix: str) -> Path:
        """A fresh unique directory inside this run's scratch."""
        return Path(tempfile.mkdtemp(prefix=f"{prefix}_", dir=self.scratch))

    # -- outputs -----------------------------------------------------------
    def output_subdir(self, name: str) -> Path:
        path = self.output_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def publish(self, final_path: Path, *, suffix: str = "") -> contextlib.AbstractContextManager:
        """``published(final_path)`` — write-then-atomic-replace."""
        return published(final_path, suffix=suffix)

    # -- lifecycle ---------------------------------------------------------
    def cleanup(self) -> None:
        """Delete this run's scratch — and only this run's scratch."""
        shutil.rmtree(self.scratch, ignore_errors=True)
        self._subdirs.clear()

    def __enter__(self) -> RunWorkspace:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()
