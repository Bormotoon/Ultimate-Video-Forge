"""Cooperative installation cancellation and owned child cleanup."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable


class InstallationCancelled(RuntimeError):
    pass


def check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise InstallationCancelled("Installation cancelled.")


def run_install(command: list[str], cancelled: Callable[[], bool] | None = None) -> None:
    if cancelled is None:
        subprocess.run(command, check=True)
        return
    check_cancelled(cancelled)
    process = subprocess.Popen(command, start_new_session=os.name != "nt")
    try:
        while process.poll() is None:
            check_cancelled(cancelled)
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        check_cancelled(cancelled)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command)
    finally:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
