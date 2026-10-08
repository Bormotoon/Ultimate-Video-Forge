import struct
import wave
from pathlib import Path

from whispersync.engine.timestretch import assemble_continuous, render_piece

from studio.stages.sync_render import render_audio_plan


def test_migrated_render_matches_frozen_pcm_with_trim_and_padding(tmp_path: Path) -> None:
    rate = 48000
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"".join(
            struct.pack("<hh", index % 20000 - 10000, 10000 - index % 20000)
            for index in range(rate * 4)
        ))
    pieces = [(0.0, 1.0, 1.0), (1.0, 1.0, 1.003), (2.0, 1.0, 1.3)]
    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    segments = [render_piece(
        source, frozen_dir, start, duration, factor, index, 10,
        sample_rate=rate, channels=2, codec="pcm_s16le",
    ) for index, (start, duration, factor) in enumerate(pieces)]
    for duration in (2.5, 3.5):
        reference = assemble_continuous(
            segments, 0.25, duration, rate, tmp_path / f"reference-{duration}.wav",
            channels=2, codec="pcm_s16le",
        )
        output = tmp_path / f"studio-{duration}.wav"
        plan = render_audio_plan(
            source, output, pieces, lead_silence_s=0.25, duration_s=duration,
            map_id="warp", source_asset_id="rec", target_asset_id="cam", strategy=3,
            sample_rate=rate, channels=2, codec="pcm_s16le",
        )
        with wave.open(str(reference), "rb") as old, wave.open(str(output), "rb") as new:
            assert old.getparams() == new.getparams()
            assert old.readframes(old.getnframes()) == new.readframes(new.getnframes())
        assert plan.duration_s == duration
    assert not list(tmp_path.glob(".pieces-*"))
