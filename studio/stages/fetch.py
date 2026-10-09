"""Fetch one YouTube URL into a Studio project without shell execution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from studio.core.project import Project, stable_fingerprint
from studio.modules.manager import ModuleManager
from studio.stages.base import Decision, GpuUse, Requirement, StageContext, StageOutput


class FetchStage:
    id = "fetch"
    title = "YouTube fetch"
    after: tuple[str, ...] = ()
    gpu = GpuUse.NONE

    def requirements(self, settings: dict[str, object]) -> list[Requirement]:
        return [Requirement("module", "youtube", "download YouTube source media")]

    def decide(self, project: Project, settings: dict[str, object]) -> Decision:
        fetch = settings.get("fetch", {})
        url = fetch.get("url") if isinstance(fetch, dict) else None
        return Decision.run({"url": url}) if url else Decision.skip("input is a local folder")

    def fingerprint(self, project: Project, settings: dict[str, object]) -> str:
        return stable_fingerprint("fetch-v1", settings.get("fetch", {}))

    def run(self, context: StageContext) -> StageOutput:
        fetch = context.settings.get("fetch", {})
        if not isinstance(fetch, dict) or not fetch.get("url"):
            raise ValueError("fetch.url is required")
        media, info = fetch_to_directory(str(fetch["url"]), context.work_dir / "fetch")
        return StageOutput((media, info))


def fetch_to_directory(url: str, destination: Path) -> tuple[Path, Path]:
    """Download one URL without executing a shell or overwriting local media."""
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "source.%(ext)s"
    command = [
        *_yt_dlp_command(),
        "--no-playlist",
        "--no-overwrites",
        "--write-info-json",
        "--output",
        str(output),
        url,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")
    media = [
        path for path in destination.iterdir()
        if path.is_file() and not path.name.endswith(".info.json")
    ]
    info = list(destination.glob("*.info.json"))
    if len(media) != 1 or len(info) != 1:
        raise RuntimeError("yt-dlp did not publish exactly one media and info file")
    return media[0], info[0]


def _yt_dlp_command() -> list[str]:
    manager = ModuleManager()
    if manager.installed("youtube"):
        return [str(manager.interpreter("youtube")), "-m", "yt_dlp"]
    return ["yt-dlp"]


def read_fetch_metadata(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("YouTube info JSON root must be an object")
    return value
