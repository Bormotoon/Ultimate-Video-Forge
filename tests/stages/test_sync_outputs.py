import wave
from pathlib import Path

import numpy as np

from studio.core.timeline import SourcePlacement
from studio.stages.sync_outputs import render_voice_master, segment_voice


def write_audio(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "PCM"))
        audio.writeframes(samples.astype("<i2").tobytes())


def test_segments_reassemble_original_pcm(tmp_path: Path) -> None:
    original = np.random.default_rng(42).integers(-1000, 1000, 48000 * 2 + 137, dtype=np.int16)
    source = tmp_path / "voice.wav"
    write_audio(source, original)
    artifacts = segment_voice(source, tmp_path / "segments", 0.01)
    parts = []
    for path in artifacts[:-1]:
        with wave.open(str(path), "rb") as audio:
            parts.append(audio.readframes(audio.getnframes()))
    assert b"".join(parts) == original.astype("<i2").tobytes()


def test_master_does_not_sum_overlapping_camera_voices(tmp_path: Path) -> None:
    source = tmp_path / "voice.wav"
    write_audio(source, np.full(48000 * 2, 4000, dtype=np.int16))
    output = render_voice_master(
        [SourcePlacement("a", 0, 0, 2), SourcePlacement("b", 0, 0, 2)],
        {"a": source, "b": source}, tmp_path / "master.wav",
    )
    import subprocess

    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-ac", "1",
                              "-f", "s16le", "-"], capture_output=True, check=True)
    samples = np.frombuffer(decoded.stdout, dtype="<i2")
    assert len(samples) == 96000
    # Mono/stereo conversion may change gain; duplicate summation would double it.
    assert 2500 < np.median(samples[4800:-4800]) < 5000
