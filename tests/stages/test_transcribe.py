from pathlib import Path

from studio.core.transcript import Segment, Transcript, Word
from studio.stages.transcribe import TranscribeSettings, WhisperEngine


class _Backend:
    device = "cpu"
    compute_type = "float32"

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio: Path, settings: TranscribeSettings) -> Transcript:
        self.calls += 1
        return Transcript(audio, "en", 1.0, [Segment(0, 1, (Word("hello", 0, 1),))])


def test_content_cache_reuses_same_source_across_path_mtime_changes(tmp_path: Path) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"same audio")
    second.write_bytes(b"same audio")
    backend = _Backend()
    engine = WhisperEngine(TranscribeSettings(), backend, tmp_path / "cache")
    assert engine.transcribe(first).words[0].text == "hello"
    assert engine.transcribe(second).words[0].text == "hello"
    assert backend.calls == 1


def test_cache_changes_with_stream_or_decoding_settings(tmp_path: Path) -> None:
    source = tmp_path / "audio.wav"
    source.write_bytes(b"audio")
    backend = _Backend()
    WhisperEngine(TranscribeSettings(), backend, tmp_path / "cache").transcribe(source, 0)
    WhisperEngine(TranscribeSettings(), backend, tmp_path / "cache").transcribe(source, 1)
    changed = TranscribeSettings(initial_prompt="context")
    WhisperEngine(changed, backend, tmp_path / "cache").transcribe(source, 1)
    assert backend.calls == 3


def test_prompt_combines_context_glossary_and_fillers() -> None:
    prompt = TranscribeSettings(
        initial_prompt="Podcast about Studio.", glossary=("Bormotoon",), keep_fillers=True
    ).prompt()
    assert "Bormotoon" in prompt
    assert "ээ" in prompt
