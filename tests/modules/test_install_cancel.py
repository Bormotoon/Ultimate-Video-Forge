import io
import sys
import time
from pathlib import Path

import pytest

import studio.modules.models as models
from studio.cli.main import main
from studio.modules.cancellation import InstallationCancelled, run_install
from studio.modules.manager import ModuleManager


def test_cancel_running_child() -> None:
    start = time.monotonic()
    with pytest.raises(InstallationCancelled):
        run_install(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            lambda: time.monotonic() - start > 0.2,
        )
    assert time.monotonic() - start < 5


def test_cancelled_model_preserves_previous_file(tmp_path: Path, monkeypatch) -> None:
    recipe = models.ModelRecipe("model.bin", "https://example.com/model", "0" * 64)
    monkeypatch.setattr(models, "recipes", lambda: {"test": recipe})
    monkeypatch.setattr(models.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"data"))
    path = tmp_path / "model.bin"
    path.write_bytes(b"previous")
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 1

    with pytest.raises(InstallationCancelled):
        models.ModelManager(tmp_path).install("test", cancelled=cancelled)
    assert path.read_bytes() == b"previous"
    assert not (tmp_path / ".studio-run.lock").exists()


def test_cancelled_module_does_not_publish_marker(tmp_path: Path, monkeypatch) -> None:
    manager = ModuleManager(tmp_path)

    def interrupted(*args, **kwargs):
        manager.interpreter("vision").parent.mkdir(parents=True)
        raise InstallationCancelled("cancelled")

    monkeypatch.setattr("studio.modules.manager.run_install", interrupted)
    with pytest.raises(InstallationCancelled):
        manager.install("vision", cancelled=lambda: False)
    assert not manager.installed("vision")


def test_cli_cancel_before_install(tmp_path: Path, capsys) -> None:
    cancel = tmp_path / "cancel"
    cancel.touch()
    assert main(["modules", "install", "vision", "--cancel-file", str(cancel)]) == 130
    assert "installation_cancelled" in capsys.readouterr().out
