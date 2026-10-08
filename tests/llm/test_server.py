import json
from pathlib import Path

import pytest

from studio.llm.server import LlamaServer


class _Process:
    pid = 123

    def poll(self) -> None:
        return None


def test_server_refuses_to_stop_process_without_matching_owner_marker(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server"
    model = tmp_path / "model.gguf"
    server = LlamaServer(executable, model, tmp_path)
    server.process = _Process()  # type: ignore[assignment]
    server.marker_path.write_text(
        json.dumps({"pid": 999, "executable": str(executable.resolve())}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="unowned"):
        server.stop()


def test_owner_check_requires_pid_and_executable(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server"
    server = LlamaServer(executable, tmp_path / "model", tmp_path)
    server.marker_path.write_text(
        json.dumps({"pid": 123, "executable": str(executable.resolve())}), encoding="utf-8"
    )
    assert server._owns_process(123)
    assert not server._owns_process(456)
