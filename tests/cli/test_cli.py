import json
from pathlib import Path

from studio.cli.main import main
from tools.make_fixtures import generate


def test_scan_and_plan_fixture_through_workers(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    source = generate(tmp_path / "fixture")
    assert main(["scan", str(source), "--json"]) == 0
    scan = json.loads(capsys.readouterr().out)
    assert len(scan["assets"]) == 4
    assert main(["plan", str(source), "--json"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert [stage["id"] for stage in plan["stages"]] == ["scan", "prepare", "transcribe"]
    assert all(stage["reuse"] for stage in plan["stages"][:2])
    assert plan["stages"][2]["decision"] == "run"
