"""Bounded channel discovery and resumable project acquisition."""

import json
import re
import subprocess
from pathlib import Path

from studio.core.workspace import project_lock, published
from studio.modules.cancellation import check_cancelled
from studio.stages.fetch import _yt_dlp_command


def discover_channel(url: str, limit: int) -> list[str]:
    if not 1 <= limit <= 1000:
        raise ValueError("channel limit must be between 1 and 1000")
    result = subprocess.run(
        [
            *_yt_dlp_command(),
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-end",
            str(limit),
            "--",
            url,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    payload = json.loads(result.stdout)
    ids = []
    for entry in payload.get("entries", []):
        if isinstance(entry, dict) and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(entry.get("id", ""))):
            if entry["id"] not in ids:
                ids.append(entry["id"])
    return ids[:limit]


def acquire_channel(url, destination, limit, fetch, cancelled=lambda: False):
    destination.mkdir(parents=True, exist_ok=True)
    results, sources = [], []
    with project_lock(destination):
        for video_id in discover_channel(url, limit):
            check_cancelled(cancelled)
            folder = destination / video_id
            try:
                if (folder / "_studio" / "project.json").is_file():
                    from studio.core.project import Project

                    existing = Project.load(folder / "_studio" / "project.json")
                    if existing.source_dir.resolve() != folder.resolve():
                        raise ValueError("channel project belongs to another source directory")
                    status = "reused"
                else:
                    # Incomplete downloads remain intact for inspection; never overwrite.
                    fetch(f"https://www.youtube.com/watch?v={video_id}", folder)
                    status = "fetched"
                sources.append(folder)
                results.append({"id": video_id, "status": status, "source": str(folder)})
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                results.append({"id": video_id, "status": "failed", "error": str(exc)})
            write_report(
                destination / "channel-acquisition.json", {"schema_version": 1, "videos": results}
            )
    return sources, results


def write_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with published(path) as temporary:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
