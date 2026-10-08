"""Owned llama-server process lifecycle."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class LlamaServer:
    executable: Path
    model: Path
    work_dir: Path
    port: int = 8080
    process: subprocess.Popen[str] | None = None

    def start(self) -> int:
        if self.process and self.process.poll() is None:
            return self.process.pid
        self.work_dir.mkdir(parents=True, exist_ok=True)
        command = [str(self.executable), "-m", str(self.model), "--port", str(self.port)]
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=os.name != "nt",
        )
        marker = {
            "pid": self.process.pid,
            "started": time.time(),
            "executable": str(self.executable.resolve()),
            "model": str(self.model.resolve()),
            "port": self.port,
        }
        self.marker_path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        return self.process.pid

    @property
    def marker_path(self) -> Path:
        return self.work_dir / "llama-server.json"

    def stop(self, timeout_s: float = 5.0) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        if not self._owns_process(process.pid):
            raise RuntimeError("refusing to stop an unowned llama-server process")
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        self.marker_path.unlink(missing_ok=True)

    def _owns_process(self, pid: int) -> bool:
        try:
            marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return bool(
            marker.get("pid") == pid
            and marker.get("executable") == str(self.executable.resolve())
        )

    def __enter__(self) -> LlamaServer:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
