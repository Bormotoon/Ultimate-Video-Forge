import json
from pathlib import Path
from unittest.mock import patch

from studio.core.transcript import Segment, Transcript, Word
from studio.stages.sync_check_step import check_voices


def test_content_check_reports_missing_voice_and_unloads_backend(tmp_path: Path) -> None:
    voice = tmp_path / "voice.wav"
    reference = Transcript(voice, "en", 3, [Segment(0, 3, tuple(
        Word(f"word{i}", i * 0.3, i * 0.3 + 0.2) for i in range(6)
    ))])
    with patch("studio.stages.sync_check_step.WhisperEngine") as factory:
        engine = factory.return_value
        engine.transcribe.return_value = Transcript(voice, "en", 3, [])
        artifacts = check_voices({"cam": reference}, {"cam": voice}, tmp_path / "check", {})
        engine.backend.unload.assert_called_once()
    report = json.loads(artifacts[-1].read_text())
    assert report["clips"][0]["status"] == "failed"
    assert report["clips"][0]["spans"][0]["kind"] == "content"
    assert report["clips"][0]["time_domain"] == "rendered_audio"
    assert all(path.is_file() for path in artifacts)
