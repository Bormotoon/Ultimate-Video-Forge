"""Subprocess argv shared by source and frozen application launches."""

import sys


def python_command(module: str, arguments: list[str]) -> list[str]:
    if getattr(sys, "frozen", False):
        switches = {"studio.cli.main": "--cli", "studio.stages.worker": "--worker"}
        if module not in switches:
            raise ValueError(f"unsupported frozen subprocess module: {module}")
        return [sys.executable, switches[module], *arguments]
    return [sys.executable, "-m", module, *arguments]
