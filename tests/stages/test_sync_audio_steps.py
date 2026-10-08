import json
from pathlib import Path
from unittest.mock import patch

from studio.stages.sync_audio_steps import process_audio


def test_missing_backend_retains_original_and_reports_warning(tmp_path: Path) -> None:
    original = tmp_path / "voice.wav"
    original.write_bytes(b"original")
    with patch("studio.stages.sync_audio_steps.sync_enhance.run_batch",
               side_effect=RuntimeError("module missing")):
        voices, ambience, artifacts = process_audio(
            {"cam": original}, {}, tmp_path / "results", {"voice_enhance": "denoise"},
        )
    assert voices == {"cam": original}
    assert not ambience
    assert original.read_bytes() == b"original"
    assert "module missing" in json.loads(artifacts[-1].read_text())["warnings"][0]


def test_enhanced_voice_is_selected_for_downstream_consumers(tmp_path: Path) -> None:
    original = tmp_path / "voice.wav"
    improved = tmp_path / "improved.wav"
    with patch("studio.stages.sync_audio_steps.sync_enhance.run_batch",
               return_value={original: improved}):
        voices, _, artifacts = process_audio(
            {"cam": original}, {}, tmp_path / "results", {"voice_enhance": "denoise"},
        )
    assert voices["cam"] == improved
    assert improved in artifacts
    assert not json.loads(artifacts[-1].read_text())["warnings"]
