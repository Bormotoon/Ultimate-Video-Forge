import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from studio.core.project import Project
from studio.stages import runner
from studio.stages.runner import StageProcessError, run_stage_process


def _project(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "_studio"
    project_path = work / "project.json"
    Project(tmp_path, work).save(project_path)
    settings_path = work / "settings.yaml"
    settings_path.write_text("{}", encoding="utf-8")
    return project_path, settings_path


def _worker_script(
    monkeypatch: pytest.MonkeyPatch, script: str,
) -> list[subprocess.Popen[str]]:
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[str]] = []

    def launch(command: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        process = real_popen([sys.executable, "-c", script], **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    return children


def test_cancel_silent_worker_reaps_process_and_preserves_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, settings = _project(tmp_path)
    manifest = project.parent / "manifests" / "scan.json"
    manifest.parent.mkdir()
    manifest.write_bytes(b"previous result")
    children = _worker_script(monkeypatch, "import time; time.sleep(5)")
    start = time.monotonic()
    with pytest.raises(StageProcessError, match="cancelled"):
        run_stage_process(
            "scan", project, settings, "fp",
            cancelled=lambda: time.monotonic() - start > 0.3,
            terminate_after_s=0.2,
        )
    assert time.monotonic() - start < 2
    assert children[0].poll() is not None
    assert manifest.read_bytes() == b"previous result"
    assert not list(project.parent.glob(".settings-*"))


def test_large_stderr_does_not_deadlock_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, settings = _project(tmp_path)
    script = (
        "import sys; "
        "sys.stderr.write('diagnostic' * 200000); sys.stderr.flush(); "
        "print('{\"t\":\"start\",\"stage\":\"scan\"}'); "
        "print('{\"t\":\"done\",\"stage\":\"scan\",\"status\":\"ok\"}')"
    )
    children = _worker_script(monkeypatch, script)
    start = time.monotonic()
    result = run_stage_process(
        "scan", project, settings, "fp",
        cancelled=lambda: time.monotonic() - start > 5,
    )
    assert result.manifest.reusable(project.parent, "fp")
    assert children[0].returncode == 0


def test_buffered_events_are_validated_after_worker_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, settings = _project(tmp_path)
    cases = [
        ('{"t":"done","stage":"other","status":"ok"}', "unexpected stage"),
        ('{"t":"unknown","stage":"scan"}', "unknown"),
        ('{"t":"warning","stage":"scan","text":"late"}', "after done"),
    ]
    for last_event, message in cases:
        output = (
            '{"t":"start","stage":"scan"}\n'
            '{"t":"done","stage":"scan","status":"ok"}\n' + last_event
        )
        with monkeypatch.context() as patch:
            children = _worker_script(patch, f"print({output!r})")
            with pytest.raises(StageProcessError, match=message):
                run_stage_process("scan", project, settings, "fp")
            assert children[0].poll() is not None
        assert not (project.parent / "manifests" / "scan.json").exists()


def test_real_worker_failure_has_stderr_diagnostic_and_failed_event(tmp_path: Path) -> None:
    project, settings = _project(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "studio.stages.worker", "unknown", str(project), str(settings)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "unknown stage" in result.stderr
    assert json.loads(result.stdout) == {"t": "done", "stage": "unknown", "status": "failed"}
    with pytest.raises(StageProcessError, match="unknown stage"):
        run_stage_process("unknown", project, settings, "fp")
