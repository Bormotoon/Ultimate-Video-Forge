from pathlib import Path

from studio.core.project import Project
from studio.stages import fetch
from studio.stages.fetch import FetchStage, fetch_to_directory, read_fetch_metadata


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


def test_fetch_to_directory_uses_no_overwrite_and_returns_one_source(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    def run(command: list[str], **_: object) -> object:
        assert "--no-overwrites" in command
        (tmp_path / "source.mp4").write_bytes(b"media")
        (tmp_path / "source.info.json").write_text("{}", encoding="utf-8")
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(fetch.subprocess, "run", run)

    media, info = fetch_to_directory("https://example.invalid/video", tmp_path)

    assert media.name == "source.mp4"
    assert info.name == "source.info.json"
