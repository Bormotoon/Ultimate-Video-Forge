"""Cancellation and window close, exercised on a REAL QThread.

The defects here were only visible with a live thread. Cancellation emitted a
log line and nothing else, so neither `finished` nor `error` fired — and those
are the two signals wired to `thread.quit()` and to re-enabling the buttons. A
real QThread probe confirmed `isRunning() == True` after cancelling, with the
window left showing Sync greyed out and Cancel lit: the user could neither
start again nor tell that anything had stopped.

Closing had the mirror problem: `quit()` asks a thread's EVENT LOOP to exit and
does nothing to a slot still executing, which is where `run_pipeline` spends
the whole run. The old code asked, ignored the result of `wait(3000)`, and
closed anyway.
"""

from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

from whispersync.config import WhisperSyncConfig
from whispersync.gui.worker import SyncWorker


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    return app if app is not None else QApplication([])


def _wait_for(predicate, timeout_s: float = 5.0) -> bool:
    """Spin the Qt event loop until ``predicate`` holds or the timeout expires."""
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return predicate()


def _worker_on_thread(monkeypatch, pipeline_impl) -> tuple[SyncWorker, QThread, dict]:
    monkeypatch.setattr("whispersync.gui.worker.run_pipeline", pipeline_impl)
    worker = SyncWorker(
        config=WhisperSyncConfig(),
        video_dir=os.curdir,
        audio_files=[],
        strategy_id=3,
        output_path=os.curdir,
    )
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    seen: dict[str, object] = {}
    worker.finished.connect(lambda r: seen.setdefault("finished", r))
    worker.error.connect(lambda m: seen.setdefault("error", m))
    worker.cancelled.connect(lambda: seen.setdefault("cancelled", True))
    worker.finished.connect(thread.quit)
    worker.error.connect(thread.quit)
    worker.cancelled.connect(thread.quit)
    return worker, thread, seen


def test_cancel_actually_stops_the_thread(qapp: QApplication, monkeypatch) -> None:
    """The core regression: after cancelling, the thread must not be running."""
    started = threading.Event()

    def pipeline(*, cancel_event=None, **kwargs):
        started.set()
        while not cancel_event.is_set():
            cancel_event.wait(0.01)
        raise InterruptedError("Cancelled by user")

    worker, thread, seen = _worker_on_thread(monkeypatch, pipeline)
    thread.start()
    assert _wait_for(started.is_set), "pipeline never started"

    worker.cancel()
    assert _wait_for(lambda: "cancelled" in seen), "no terminal signal after cancel"
    assert _wait_for(lambda: not thread.isRunning()), "QThread still running after cancel"

    # Cancellation is its OWN outcome, not a success and not an error.
    assert "finished" not in seen
    assert "error" not in seen
    thread.wait(2000)


def test_a_cancelled_run_can_be_started_again(qapp: QApplication, monkeypatch) -> None:
    """A cancelled run that never released its thread left the UI unable to
    start another. Cancel -> stop -> run again must work."""
    for attempt in range(2):
        started = threading.Event()

        def pipeline(*, cancel_event=None, _s=started, **kwargs):
            _s.set()
            while not cancel_event.is_set():
                cancel_event.wait(0.01)
            raise InterruptedError("Cancelled by user")

        worker, thread, seen = _worker_on_thread(monkeypatch, pipeline)
        thread.start()
        assert _wait_for(started.is_set), f"attempt {attempt}: pipeline never started"
        worker.cancel()
        stopped = _wait_for(lambda t=thread: not t.isRunning())
        assert stopped, f"attempt {attempt}: thread stuck"
        thread.wait(2000)


def test_success_and_failure_still_emit_their_own_outcomes(qapp: QApplication, monkeypatch) -> None:
    """Exactly one terminal signal per run, whichever way it ends."""
    worker, thread, seen = _worker_on_thread(monkeypatch, lambda **kw: "result")
    thread.start()
    assert _wait_for(lambda: "finished" in seen)
    assert seen["finished"] == "result"
    assert "cancelled" not in seen and "error" not in seen
    thread.wait(2000)

    def failing(**kwargs):
        raise RuntimeError("ffprobe exploded")

    worker2, thread2, seen2 = _worker_on_thread(monkeypatch, failing)
    thread2.start()
    assert _wait_for(lambda: "error" in seen2)
    assert "ffprobe exploded" in str(seen2["error"])
    assert "cancelled" not in seen2 and "finished" not in seen2
    thread2.wait(2000)


def test_close_while_running_is_deferred_until_the_thread_stops(
    qapp: QApplication, monkeypatch
) -> None:
    """The window must not close out from under a live pipeline.

    `quit()` cannot interrupt an executing slot, so the old close destroyed a
    QThread with a running worker and abandoned half-written outputs.
    """
    from whispersync.gui.main_window import MainWindow

    started = threading.Event()

    def pipeline(*, cancel_event=None, **kwargs):
        started.set()
        while not cancel_event.is_set():
            cancel_event.wait(0.01)
        raise InterruptedError("Cancelled by user")

    monkeypatch.setattr("whispersync.gui.worker.run_pipeline", pipeline)

    window = MainWindow()
    worker = SyncWorker(
        config=WhisperSyncConfig(),
        video_dir=os.curdir,
        audio_files=[],
        strategy_id=3,
        output_path=os.curdir,
    )
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.cancelled.connect(thread.quit)
    thread.finished.connect(window._on_thread_finished)
    window._worker = worker
    window._thread = thread
    thread.start()
    assert _wait_for(started.is_set)

    # First close: refused, cancellation requested.
    assert window.close() is False, "window closed while the pipeline was running"
    assert window._closing is True
    assert worker.is_cancelled

    # Once the thread really stops, the deferred close goes through.
    assert _wait_for(lambda: not thread.isRunning()), "thread never stopped"
    assert _wait_for(lambda: not window.isVisible())
    thread.wait(2000)
