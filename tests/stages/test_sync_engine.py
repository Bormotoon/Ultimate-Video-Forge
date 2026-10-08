import wave
from pathlib import Path

import pytest

from studio.core.timeline import TimeDomain
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.sync_engine import matching_transcript, synchronize_transcripts
from studio.stages.sync_geometry import PieceConfig
from studio.stages.sync_match_settings import MatchSettings


def test_public_transcripts_flow_through_matching_planning_and_real_render(tmp_path: Path) -> None:
    source = tmp_path / "recorder.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x10\x00" * 48000 * 4)
    words = tuple(Word(f"token{i}", 0.2 + i * 0.1, 0.25 + i * 0.1) for i in range(25))
    recorder = Transcript(source, "en", 4.0, [Segment(0.0, 3.0, words)])
    camera = Transcript(tmp_path / "camera.mov", "en", 3.0, [Segment(0.0, 3.0, words)])
    before = recorder.to_dict()
    output = tmp_path / "voice.wav"
    plan = synchronize_transcripts(
        camera, recorder, source, output, match_settings=MatchSettings(),
        piece_settings=PieceConfig(), strategy=3, source_asset_id="rec", target_asset_id="cam",
        codec="pcm_s16le",
    )
    assert recorder.to_dict() == before
    assert plan.warp.strategy == 3
    with wave.open(str(output), "rb") as audio:
        assert audio.getnframes() == 48000 * 3


def test_matching_adapter_rejects_timeline_domain() -> None:
    transcript = Transcript(Path("audio"), "en", 1.0, [], time_domain=TimeDomain.TIMELINE)
    with pytest.raises(ValueError, match="file-domain"):
        matching_transcript(transcript)
