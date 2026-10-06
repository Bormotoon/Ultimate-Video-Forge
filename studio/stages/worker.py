"""Subprocess entry point for one Studio stage."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from studio.core.project import Project
from studio.stages.base import Stage, StageContext
from studio.stages.events import EventType, StageEvent
from studio.stages.export import ExportStage
from studio.stages.prepare import PrepareStage
from studio.stages.scan import ScanStage
from studio.stages.sync import SyncStage
from studio.stages.timeline import TimelineStage
from studio.stages.transcribe import TranscribeStage


def stage_registry() -> dict[str, Stage]:
    stages: list[Stage] = [
        ScanStage(),
        PrepareStage(),
        TranscribeStage(),
        SyncStage(),
        TimelineStage(),
        ExportStage(),
    ]
    return {stage.id: stage for stage in stages}


def _emit(event: StageEvent) -> None:
    print(event.to_json(), flush=True)


def execute(stage_id: str, project_path: Path, settings_path: Path) -> int:
    stage = stage_registry().get(stage_id)
    if stage is None:
        raise ValueError(f"unknown stage: {stage_id}")
    project = Project.load(project_path)
    settings: dict[str, Any] = {}
    if settings_path.is_file():
        loaded = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise ValueError("settings root must be a mapping")
            settings = loaded
    _emit(StageEvent(EventType.START, stage_id))
    output = stage.run(StageContext(project, settings, project.work_dir))
    for key, value in output.project_changes.items():
        if not hasattr(project, key):
            raise ValueError(f"stage returned unknown project field: {key}")
        setattr(project, key, value)
    project.save(project_path)
    for artifact in output.artifacts:
        _emit(StageEvent(EventType.ARTIFACT, stage_id, {"path": str(artifact)}))
    _emit(StageEvent(EventType.DONE, stage_id, {"status": "ok"}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage_id")
    parser.add_argument("project", type=Path)
    parser.add_argument("settings", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return execute(args.stage_id, args.project, args.settings)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
