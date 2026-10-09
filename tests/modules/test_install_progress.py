from pathlib import Path

import pytest

from studio.core.workspace import ProjectLockedError, project_lock
from studio.modules.manager import ModuleManager


def test_installation_events_and_lock(tmp_path: Path, monkeypatch) -> None:
    manager = ModuleManager(tmp_path)

    def create(builder, folder):
        interpreter = manager.interpreter("vision")
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()

    monkeypatch.setattr("venv.EnvBuilder.create", create)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)
    events = []
    manager.install("vision", progress=events.append)
    assert [event["phase"] for event in events] == ["environment", "dependencies", "complete"]
    with project_lock(tmp_path / ".locks" / "vision"), pytest.raises(ProjectLockedError):
        manager.install("vision")
