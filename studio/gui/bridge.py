"""QProcess bridge from GUI screens to the shared Studio CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QProcess, pyqtSignal


class RunnerBridge(QObject):
    stage_event = pyqtSignal(dict)
    output = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished = pyqtSignal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._stdout)
        self.process.readyReadStandardError.connect(self._stderr)
        self.process.finished.connect(self._finished)

    def start(self, command: str, source: Path) -> None:
        if self.process.state() is not QProcess.ProcessState.NotRunning:
            raise RuntimeError("a Studio command is already running")
        self.process.start(
            sys.executable,
            ["-m", "studio.cli.main", command, str(source), "--json"],
        )

    def cancel(self) -> None:
        if self.process.state() is not QProcess.ProcessState.NotRunning:
            self.process.terminate()

    def _stdout(self) -> None:
        text = self.process.readAllStandardOutput().data().decode(errors="replace")
        self.output.emit(text)
        for line in text.splitlines():
            try:
                value: Any = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                self.stage_event.emit(value)

    def _stderr(self) -> None:
        text = self.process.readAllStandardError().data().decode(errors="replace")
        if text:
            self.failed.emit(text)

    def _finished(self, code: int, status: QProcess.ExitStatus) -> None:
        if status is QProcess.ExitStatus.CrashExit:
            self.failed.emit("Studio worker crashed")
        self.finished.emit(code)
