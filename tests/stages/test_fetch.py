from pathlib import Path

from studio.core.project import Project
from studio.stages.fetch import FetchStage, read_fetch_metadata


def test_fetch_decision_skips_local_projects(tmp_path: Path) -> None:
    project = Project(tmp_path, tmp_path / "_studio")
    assert FetchStage().decide(project, {}).reason == "input is a local folder"
    assert FetchStage().decide(project, {"fetch": {"url": "https://youtu.be/id"}}).choices[
        "url"
    ] == "https://youtu.be/id"


def test_fetch_metadata_requires_object(tmp_path: Path) -> None:
    path = tmp_path / "info.json"
    path.write_text('{"title":"Episode"}', encoding="utf-8")
    assert read_fetch_metadata(path)["title"] == "Episode"
