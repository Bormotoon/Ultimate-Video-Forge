"""Persist desktop framing choices in the ordinary project settings layer."""

from __future__ import annotations

from pathlib import Path

import yaml

from studio.core.publication import recover_publications
from studio.core.settings import load_settings
from studio.core.workspace import project_lock, published


def save_framing(
    work_dir: Path, mode: str, width: int, height: int, crop_x: float, *, tracking: bool = False
) -> None:
    changes = {"framing": mode, "width": width, "height": height, "crop_x": crop_x}
    path = work_dir / "settings.yaml"
    changes["tracking"] = tracking
    with project_lock(work_dir):
        recover_publications(work_dir)
        load_settings([path], [f"reels.{name}={value}" for name, value in changes.items()])
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data = data or {}
        data.setdefault("reels", {}).update(changes)
        with published(path) as temporary:
            temporary.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
