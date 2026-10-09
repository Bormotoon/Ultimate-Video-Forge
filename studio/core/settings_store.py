"""Validate and atomically update selected project settings."""

from pathlib import Path
from typing import Any

import yaml

from studio.core.publication import recover_publications
from studio.core.settings import _construct, _merge, validate_settings
from studio.core.workspace import project_lock, published


def save_settings(work_dir: Path, changes: dict[str, Any], *, replace: bool = False) -> None:
    path = work_dir / "settings.yaml"
    with project_lock(work_dir):
        recover_publications(work_dir)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("settings root must be a mapping")
        merged = changes if replace else _merge(data, changes)
        validate_settings(_construct(merged))
        with published(path) as temporary:
            temporary.write_text(
                yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
