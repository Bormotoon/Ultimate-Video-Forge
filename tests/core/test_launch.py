import sys

from studio.core.launch import python_command
from studio.frozen_entry import main


def test_source_and_frozen_commands(monkeypatch) -> None:
    assert python_command("studio.stages.worker", ["scan"])[1:] == [
        "-m",
        "studio.stages.worker",
        "scan",
    ]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert python_command("studio.stages.worker", ["scan"])[1:] == ["--worker", "scan"]
    assert python_command("studio.cli.main", ["models"])[1:] == ["--cli", "models"]


def test_packaged_entry_dispatch(monkeypatch) -> None:
    monkeypatch.setattr("studio.cli.main.main", lambda args: 12 if args == ["models"] else 1)
    monkeypatch.setattr("studio.stages.worker.main", lambda args: 13 if args == ["scan"] else 1)
    assert main(["--cli", "models"]) == 12
    assert main(["models"]) == 12
    assert main(["--worker", "scan"]) == 13
