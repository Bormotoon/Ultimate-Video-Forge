"""Run one stage worker in an isolated process and validate its publication."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from studio.core.project import ArtifactStatus, Project, StageManifest, describe_artifact
from studio.core.settings import load_settings
from studio.stages.events import EventType, StageEvent


class StageProcessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunResult:
    events: tuple[StageEvent, ...]
    manifest: StageManifest


def run_stage_process(
    stage_id: str,
    project_path: Path,
    settings_path: Path,
    fingerprint: str,
    *,
    effective_settings: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    terminate_after_s: float = 5.0,
) -> RunResult:
    settings = (
        effective_settings
        if effective_settings is not None
        else load_settings([settings_path]).to_dict()
    )
    project = Project.load(project_path)
    # A private snapshot keeps the worker's inputs identical to the planner's,
    # even if the project YAML changes while the worker is running.
    with tempfile.TemporaryDirectory(prefix=".settings-", dir=project.work_dir) as temporary:
        snapshot = Path(temporary) / "settings.yaml"
        snapshot.write_text(yaml.safe_dump(settings), encoding="utf-8")
        return _run_stage_process(
            stage_id, project_path, snapshot, settings_path, fingerprint,
            settings, cancelled, terminate_after_s,
        )


def _run_stage_process(
    stage_id: str,
    project_path: Path,
    snapshot: Path,
    settings_path: Path,
    fingerprint: str,
    settings: dict[str, Any],
    cancelled: Callable[[], bool],
    terminate_after_s: float,
) -> RunResult:
    project = Project.load(project_path)
    command = [
        sys.executable, "-m", "studio.stages.worker", stage_id,
        str(project_path), str(snapshot),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
    )
    events: list[StageEvent] = []
    assert process.stdout is not None
    while process.poll() is None:
        if cancelled():
            _stop_process_tree(process, terminate_after_s)
            raise StageProcessError(f"stage {stage_id} was cancelled")
        line = process.stdout.readline()
        if line:
            event = StageEvent.from_json(line)
            if event.stage != stage_id:
                _stop_process_tree(process, terminate_after_s)
                raise StageProcessError(f"worker emitted event for unexpected stage {event.stage}")
            events.append(event)
        else:
            time.sleep(0.02)
    remainder = process.stdout.read()
    for line in remainder.splitlines():
        if line:
            events.append(StageEvent.from_json(line))
    assert process.stderr is not None
    stderr = process.stderr.read().strip()
    if process.returncode != 0:
        raise StageProcessError(f"stage {stage_id} exited {process.returncode}: {stderr}")
    done = next((event for event in reversed(events) if event.type is EventType.DONE), None)
    if done is None or done.payload.get("status") != "ok":
        raise StageProcessError(f"stage {stage_id} exited without a successful done event")
    artifact_paths = [
        Path(event.payload["path"])
        for event in events
        if event.type is EventType.ARTIFACT
    ]
    artifacts = [describe_artifact(project.work_dir, path) for path in artifact_paths]
    manifest = StageManifest(
        stage=stage_id,
        fingerprint=fingerprint,
        inputs={
            "project": str(project_path),
            "settings": str(settings_path),
            "effective_settings": settings,
        },
        artifacts=artifacts,
        status=ArtifactStatus.OK,
        producer_version=project.producer_version,
    )
    manifest.save(project.work_dir / "manifests" / f"{stage_id}.json")
    return RunResult(tuple(events), manifest)


def _stop_process_tree(process: subprocess.Popen[str], grace_s: float) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.terminate()
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
