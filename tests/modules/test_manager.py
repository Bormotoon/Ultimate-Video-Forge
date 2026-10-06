from pathlib import Path

import pytest

from studio.modules.manager import ModuleManager, module_names


def test_module_status_requires_interpreter_and_marker(tmp_path: Path) -> None:
    manager = ModuleManager(tmp_path)
    assert "vision" in module_names()
    assert not manager.installed("vision")
    interpreter = manager.interpreter("vision")
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    assert not manager.installed("vision")
    (tmp_path / "vision" / "module.json").write_text("{}", encoding="utf-8")
    assert manager.installed("vision")


def test_unknown_module_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown"):
        ModuleManager(tmp_path).install("missing")
