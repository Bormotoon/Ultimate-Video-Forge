"""Headless Studio commands."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from studio import __version__
from studio.core.project import Project
from studio.core.publication import recover_publications
from studio.core.settings import SettingsError, load_settings
from studio.core.workspace import project_lock
from studio.stages.planner import build_plan
from studio.stages.runner import run_stage_process
from studio.stages.worker import stage_registry


def _project(source: Path) -> tuple[Project, Path, Path]:
    source = source.resolve()
    work = source / "_studio"
    project_path = work / "project.json"
    settings_path = work / "settings.yaml"
    if project_path.is_file():
        project = Project.load(project_path)
    else:
        project = Project(source, work, producer_version=__version__)
        project.save(project_path)
    if not settings_path.exists():
        settings_path.write_text("sync:\n  mode: auto\n", encoding="utf-8")
    return project, project_path, settings_path


def _settings(path: Path, overrides: list[str] | None = None) -> dict[str, Any]:
    return load_settings([path], overrides).to_dict()


def _run_discovery(
    source: Path, *, include_prepare: bool, overrides: list[str] | None = None
) -> Project:
    project, project_path, settings_path = _project(source)
    settings = _settings(settings_path, overrides)
    selected = [stage_registry()["scan"]]
    if include_prepare:
        selected.append(stage_registry()["prepare"])
    with project_lock(project.work_dir):
        recover_publications(project.work_dir)
        for stage in selected:
            project = Project.load(project_path)
            fingerprint = stage.fingerprint(project, settings)
            result = run_stage_process(
                stage.id, project_path, settings_path, fingerprint,
                effective_settings=settings,
            )
            if not result.manifest.reusable(project.work_dir, fingerprint):
                raise RuntimeError(f"stage {stage.id} did not publish a reusable result")
    return Project.load(project_path)


def _run_pipeline(source: Path, overrides: list[str] | None = None) -> Project:
    project, project_path, settings_path = _project(source)
    settings = _settings(settings_path, overrides)
    registry = stage_registry()
    with project_lock(project.work_dir):
        recover_publications(project.work_dir)
        while True:
            project = Project.load(project_path)
            plan = build_plan(registry.values(), project, settings)
            candidate = next(
                (
                    item
                    for item in plan.stages
                    if item.decision.kind.value == "run" and not item.will_reuse
                ),
                None,
            )
            if candidate is None:
                break
            run_stage_process(
                candidate.stage_id,
                project_path,
                settings_path,
                candidate.fingerprint,
                effective_settings=settings,
            )
    return Project.load(project_path)


def _doctor() -> int:
    checks = {
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "python": shutil.which("python3.12"),
    }
    for name, path in checks.items():
        print(f"{name}: {path or 'MISSING'}")
    if checks["ffmpeg"]:
        result = subprocess.run(
            [checks["ffmpeg"], "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
        )
        print(f"nvenc: {'available' if 'h264_nvenc' in result.stdout else 'unavailable'}")
    return 0 if all(checks.values()) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("source", type=Path)
        command.add_argument("--json", action="store_true")
        command.add_argument("--set", action="append", default=[])
    commands.add_parser("doctor")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _main(argv)
    except SettingsError as exc:
        print(f"settings error: {exc}", file=sys.stderr)
        return 2


def _main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        return _doctor()
    project = (
        _run_pipeline(args.source, args.set)
        if args.command == "run"
        else _run_discovery(
            args.source, include_prepare=args.command == "plan", overrides=args.set
        )
    )
    if args.command == "scan":
        data = project.to_dict()
    else:
        settings = _settings(project.work_dir / "settings.yaml", args.set)
        plan = build_plan(stage_registry().values(), project, settings)
        data = {
            "revision": plan.revision,
            "stages": [
                {
                    "id": item.stage_id,
                    "decision": item.decision.kind.value,
                    "reason": item.decision.reason,
                    "reuse": item.will_reuse,
                }
                for item in plan.stages
            ],
        }
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
