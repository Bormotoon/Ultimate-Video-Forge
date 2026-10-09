import hashlib
import io
from pathlib import Path

import pytest

import studio.modules.models as models


def test_verified_download_and_no_replacement_on_bad_checksum(tmp_path: Path, monkeypatch) -> None:
    payload = b"controlled model weights"
    recipe = models.ModelRecipe(
        "weights.bin", "https://example.com/model", hashlib.sha256(payload).hexdigest()
    )
    monkeypatch.setattr(models, "recipes", lambda: {"test": recipe})
    monkeypatch.setattr(
        models.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(payload)
    )
    manager = models.ModelManager(tmp_path)
    events = []
    path = manager.install("test", progress=events.append)
    assert events[-1]["percent"] == 100
    assert any(event["phase"] == "verify" for event in events)
    assert manager.installed("test")
    path.write_bytes(b"previous bytes")
    monkeypatch.setattr(
        models.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"bad")
    )
    with pytest.raises(ValueError, match="checksum"):
        manager.install("test")
    assert path.read_bytes() == b"previous bytes"


def test_model_recipes_do_not_import_ml() -> None:
    import re

    for recipe in models.recipes().values():
        assert re.fullmatch("[a-f0-9]{64}", recipe.sha256)
        assert recipe.url.startswith("https://")
