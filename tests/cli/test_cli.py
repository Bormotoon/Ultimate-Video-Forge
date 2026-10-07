import json
from pathlib import Path

import pytest

from studio.cli.main import main
from studio.core.project import Project, StageManifest
from studio.core.settings import load_settings
from studio.stages.prepare import PrepareStage
from tools.make_fixtures import generate


def test_scan_and_plan_fixture_through_workers(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    source = generate(tmp_path / "fixture")
    assert main(["scan", str(source), "--json"]) == 0
    scan = json.loads(capsys.readouterr().out)
    assert len(scan["assets"]) == 4
    assert main(["plan", str(source), "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert [stage["id"] for stage in plan["stages"]] == [
        "scan",
        "prepare",
        "transcribe",
        "sync",
        "timeline",
        "roughcut",
        "export",
        "program",
    ]
    assert all(stage["reuse"] for stage in plan["stages"][:2])
    assert plan["stages"][2]["decision"] == "run"


def test_overrides_reach_worker_without_rewriting_project_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    source = generate(tmp_path / "fixture")
    work = source / "_studio"
    work.mkdir()
    settings_path = work / "settings.yaml"
    original = "# Project defaults\nsync:\n  mode: auto\n"
    settings_path.write_text(original, encoding="utf-8")

    assert main(["plan", str(source), "--json", "--set", "sync.mode=camera"]) == 0
    plan = json.loads(capsys.readouterr().out)
    requirements_path = work / "stages" / "prepare" / "requirements.json"
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    assert requirements["transcripts"] == ["recorder-zoom0001-tr1"]
    project = Project.load(work / "project.json")
    effective = load_settings([settings_path], ["sync.mode=camera"]).to_dict()
    manifest = StageManifest.load(work / "manifests" / "prepare.json")
    assert manifest.fingerprint == PrepareStage().fingerprint(project, effective)
    assert manifest.inputs["effective_settings"] == effective
    assert plan["stages"][1]["reuse"]
    assert settings_path.read_text(encoding="utf-8") == original
    assert not list(work.glob(".settings-*"))

    assert main(["plan", str(source), "--json"]) == 0
    capsys.readouterr()
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    assert len(requirements["transcripts"]) == 4
    assert settings_path.read_text(encoding="utf-8") == original


def test_invalid_override_returns_usage_error_from_console_entry_point(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["plan", str(tmp_path), "--set", "sync.mode=invalid", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "sync.mode" in captured.err
    assert not (tmp_path / "_studio" / "manifests").exists()
