"""Install and inspect isolated optional feature environments."""

from __future__ import annotations

import json
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path


@dataclass(frozen=True, slots=True)
class ModuleRecipe:
    name: str
    requirements: tuple[str, ...]
    python: str = sys.executable


RECIPES = {
    "youtube": ModuleRecipe("youtube", ("yt-dlp>=2025.1.0",)),
    "diarization": ModuleRecipe("diarization", ("pyannote.audio>=3.1,<4",)),
    "vision": ModuleRecipe("vision", ("torch>=2.7,<3", "opencv-python-headless>=4.8,<5")),
}


class ModuleManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or user_data_path("studio") / "modules"

    def interpreter(self, name: str) -> Path:
        folder = self.root / name
        return folder / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    def installed(self, name: str) -> bool:
        marker = self.root / name / "module.json"
        return self.interpreter(name).is_file() and marker.is_file()

    def install(self, name: str) -> Path:
        recipe = RECIPES.get(name)
        if recipe is None:
            raise ValueError(f"unknown Studio module: {name}")
        folder = self.root / name
        folder.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=True).create(folder)
        interpreter = self.interpreter(name)
        subprocess.run(
            [str(interpreter), "-m", "pip", "install", *recipe.requirements],
            check=True,
        )
        (folder / "module.json").write_text(
            json.dumps(
                {"schema_version": 1, "name": name, "requirements": recipe.requirements},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return interpreter


def module_names() -> tuple[str, ...]:
    return tuple(RECIPES)
