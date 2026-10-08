"""Run one stage worker in an isolated process and validate its publication."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from studio.core.project import Artifact, ArtifactStatus, Project, StageManifest, describe_artifact
from studio.core.publication import publish_files, recover_publications
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
    recover_publications(project.work_dir)
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
    initial_project = project_path.read_bytes()
    private_project = snapshot.parent / "input-project.json"
    private_project.write_bytes(initial_project)
    output_dir = snapshot.parent / "output"
    output_dir.mkdir()
    result_project = snapshot.parent / "result-project.json"
    command = [
        sys.executable, "-m", "studio.stages.worker", stage_id,
        str(private_project), str(snapshot),
        "--output-dir", str(output_dir), "--result-project", str(result_project),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name != "nt",
    )
    try:
        while True:
            if cancelled():
                raise StageProcessError(f"stage {stage_id} was cancelled")
            try:
                # communicate drains BOTH pipes while the deadline keeps
                # cancellation responsive even when stdout stays silent.
                stdout, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode != 0:
            raise StageProcessError(
                f"stage {stage_id} exited {process.returncode}: {stderr.strip()}"
            )
        events = _parse_events(stdout, stage_id)
    except BaseException:
        _stop_process_tree(process, terminate_after_s)
        raise
    finally:
        if process.poll() is None:
            _stop_process_tree(process, terminate_after_s)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    artifact_paths = [
        Path(event.payload["path"])
        for event in events
        if event.type is EventType.ARTIFACT
    ]
    artifacts: list[Artifact] = []
    replacements: list[tuple[Path, Path]] = []
    path_mapping: dict[str, str] = {}
    for path in artifact_paths:
        try:
            relative = path.resolve().relative_to(output_dir.resolve())
        except ValueError as exc:
            raise StageProcessError(f"artifact outside worker output directory: {path}") from exc
        if not relative.parts or relative.parts[0] not in {"stages", "export", "program", "reels"}:
            raise StageProcessError(f"invalid artifact target: {relative}")
        destination = project.work_dir / relative
        artifact = describe_artifact(output_dir, output_dir / relative)
        artifacts.append(Artifact(str(relative), artifact.sha256, artifact.size))
        replacements.append((path, destination))
        path_mapping[str(path)] = str(destination)
    if not result_project.is_file():
        raise StageProcessError("worker did not return a project result")
    candidate = Project.load(result_project)
    if candidate.source_dir != project.source_dir or candidate.work_dir != project.work_dir:
        raise StageProcessError("worker changed project root directories")
    candidate.transcripts = {
        key: Path(path_mapping.get(str(value), str(value)))
        for key, value in candidate.transcripts.items()
    }
    candidate.outputs = {
        key: [Path(path_mapping.get(str(value), str(value))) for value in values]
        for key, values in candidate.outputs.items()
    }
    candidate.save(result_project)
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
    private_manifest = snapshot.parent / "manifest.json"
    manifest.save(private_manifest)
    replacements.extend([
        (result_project, project_path),
        (private_manifest, project.work_dir / "manifests" / f"{stage_id}.json"),
    ])
    if cancelled():
        raise StageProcessError(f"stage {stage_id} was cancelled")
    if project_path.read_bytes() != initial_project:
        raise StageProcessError("project changed while the worker was running")
    try:
        publish_files(project.work_dir, replacements, cancelled=cancelled)
    except InterruptedError as exc:
        raise StageProcessError(f"stage {stage_id} was cancelled") from exc
    return RunResult(tuple(events), manifest)


def _parse_events(stdout: str, stage_id: str) -> list[StageEvent]:
    events: list[StageEvent] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = StageEvent.from_json(line)
        except (ValueError, TypeError) as exc:
            raise StageProcessError(f"stage {stage_id}: {exc}") from exc
        if event.stage != stage_id:
            raise StageProcessError(f"worker emitted event for unexpected stage {event.stage}")
        if events and events[-1].type is EventType.DONE:
            raise StageProcessError(f"stage {stage_id} emitted an event after done")
        events.append(event)
    if not events or events[0].type is not EventType.START:
        raise StageProcessError(f"stage {stage_id} exited without a start event")
    if events[-1].type is not EventType.DONE or events[-1].payload.get("status") != "ok":
        raise StageProcessError(f"stage {stage_id} exited without a successful done event")
    return events


def _stop_process_tree(process: subprocess.Popen[str], grace_s: float) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
