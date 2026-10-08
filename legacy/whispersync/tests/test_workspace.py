"""Run isolation: a second run must not damage the first one's results.

Runs used to share ``audio_synced/``, ``.master/`` and ``enhance_tmp/`` under
ffmpeg's ``-y``, so a second run pointed at the same output folder overwrote
the audio the first run's FCPXML still referenced, and either run's cleanup
deleted files the other was still reading. A crash halfway through left a
half-written WAV standing in for a previously good one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from whispersync.engine.workspace import (
    OutputLockedError,
    RunWorkspace,
    output_lock,
    published,
)

# --- published(): a result becomes visible only when it is complete ---------


def test_published_replaces_atomically(tmp_path: Path) -> None:
    final = tmp_path / "out.wav"
    final.write_text("old but good")
    with published(final) as tmp:
        tmp.write_text("new and complete")
        # Until the block exits, readers still see the previous complete file.
        assert final.read_text() == "old but good"
    assert final.read_text() == "new and complete"


def test_published_leaves_the_previous_result_intact_on_failure(tmp_path: Path) -> None:
    """The behaviour that matters most: a failed run must not degrade an
    earlier good one. A crash mid-render used to leave a truncated file where
    a working result had been."""
    final = tmp_path / "out.wav"
    final.write_text("previous good result")
    with pytest.raises(RuntimeError, match="boom"), published(final) as tmp:
        tmp.write_text("half written")
        raise RuntimeError("boom")
    assert final.read_text() == "previous good result"
    # And no temporary is left lying around.
    assert [p.name for p in tmp_path.iterdir()] == ["out.wav"]


def test_published_writes_its_temp_beside_the_destination(tmp_path: Path) -> None:
    """os.replace is only atomic within one filesystem, so the temporary must
    live in the destination's own directory."""
    final = tmp_path / "sub" / "out.wav"
    with published(final) as tmp:
        assert tmp.parent == final.parent
        tmp.write_text("x")
    assert final.exists()


def test_published_rejects_an_empty_write(tmp_path: Path) -> None:
    final = tmp_path / "out.wav"
    with pytest.raises(RuntimeError, match="nothing was written"), published(final) as tmp:
        tmp.unlink()


# --- RunWorkspace: one owner per run ---------------------------------------


def test_two_runs_get_separate_scratch(tmp_path: Path) -> None:
    """A fixed scratch directory name is how one run's cleanup deleted another
    run's working files."""
    with RunWorkspace(tmp_path) as a, RunWorkspace(tmp_path) as b:
        assert a.scratch != b.scratch
        (a.scratch_dir("seg") / "a.wav").write_text("a")
        (b.scratch_dir("seg") / "b.wav").write_text("b")
        assert (a.scratch / "seg" / "a.wav").exists()
        assert (b.scratch / "seg" / "b.wav").exists()


def test_cleanup_removes_only_its_own_scratch(tmp_path: Path) -> None:
    other = RunWorkspace(tmp_path)
    victim = other.scratch_dir("seg") / "keep.wav"
    victim.write_text("still in use")
    keeper = tmp_path / "audio_synced"
    keeper.mkdir()
    (keeper / "result.wav").write_text("a finished output")

    with RunWorkspace(tmp_path) as ws:
        (ws.scratch_dir("seg") / "mine.wav").write_text("mine")

    assert victim.exists(), "cleanup deleted another run's scratch"
    assert (keeper / "result.wav").exists(), "cleanup deleted a finished output"
    other.cleanup()


def test_scratch_lives_on_the_output_volume(tmp_path: Path) -> None:
    """Piece WAVs are the size of the final render, so /tmp — often a small
    tmpfs — is the wrong place for them."""
    with RunWorkspace(tmp_path) as ws:
        assert ws.scratch.parent == tmp_path


def test_temp_dir_is_unique_each_call(tmp_path: Path) -> None:
    with RunWorkspace(tmp_path) as ws:
        assert ws.temp_dir("seg") != ws.temp_dir("seg")


# --- output_lock: concurrent runs are refused, not interleaved --------------


def test_second_run_on_the_same_output_is_refused(tmp_path: Path) -> None:
    with (
        output_lock(tmp_path),
        pytest.raises(OutputLockedError, match="Another WhisperSync run"),
        output_lock(tmp_path),
    ):
        pass


def test_lock_is_released_after_the_run(tmp_path: Path) -> None:
    with output_lock(tmp_path):
        pass
    with output_lock(tmp_path):  # must not raise
        pass


def test_lock_is_released_even_when_the_run_fails(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), output_lock(tmp_path):
        raise RuntimeError("pipeline failed")
    with output_lock(tmp_path):
        pass


def test_a_stale_lock_from_a_dead_process_is_taken_over(tmp_path: Path) -> None:
    """A crashed run must not lock a folder forever."""
    lock = tmp_path / ".whispersync-run.lock"
    # A pid that cannot be running: 2**31-1 is above every platform's pid_max.
    lock.write_text(json.dumps({"pid": 2**31 - 1, "started": 0}))
    with output_lock(tmp_path):
        pass
    assert not lock.exists()


def test_a_live_lock_is_respected(tmp_path: Path) -> None:
    lock = tmp_path / ".whispersync-run.lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "started": 1e12}))
    with pytest.raises(OutputLockedError), output_lock(tmp_path):
        pass
    lock.unlink()
