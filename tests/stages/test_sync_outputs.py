import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

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


def decode(path: Path) -> np.ndarray:
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1",
                             "-f", "s16le", "-"], capture_output=True, check=True)
    return np.frombuffer(result.stdout, dtype="<i2").astype(float)


def test_master_crossfade_smooths_switch_without_shifting_timeline(tmp_path: Path) -> None:
    first, second = tmp_path / "first.wav", tmp_path / "second.wav"
    write_audio(first, np.full(96000, 4000))
    write_audio(second, np.full(96000, -4000))
    placements = [SourcePlacement("a", 0, 0, 2), SourcePlacement("b", 1, 0, 2)]
    smooth = decode(render_voice_master(placements, {"a": first, "b": second},
                                       tmp_path / "smooth.wav", crossfade_ms=20))
    hard = decode(render_voice_master(placements, {"a": first, "b": second},
                                     tmp_path / "hard.wav", crossfade_ms=0))
    assert len(smooth) == len(hard) == 144000
    assert np.max(np.abs(np.diff(smooth[95000:96100]))) < 50
    assert np.max(np.abs(np.diff(hard[95000:96100]))) > 4000
    assert np.median(smooth[96000:100000]) == pytest.approx(
        np.median(hard[96000:100000]), abs=2,
    )


def test_master_crossfade_preserves_gap_silence(tmp_path: Path) -> None:
    source = tmp_path / "voice.wav"
    write_audio(source, np.full(48000, 4000))
    samples = decode(render_voice_master(
        [SourcePlacement("a", 0, 0, 1), SourcePlacement("b", 2, 0, 1)],
        {"a": source, "b": source}, tmp_path / "gap.wav", crossfade_ms=100,
    ))
    assert len(samples) == 144000
    assert np.max(np.abs(samples[49000:95000])) == 0


def test_correlated_crossfade_does_not_double_dialogue_level(tmp_path: Path) -> None:
    source = tmp_path / "voice.wav"
    write_audio(source, np.full(96000, 4000))
    samples = decode(render_voice_master(
        [SourcePlacement("a", 0, 0, 2), SourcePlacement("b", 1, 0, 2)],
        {"a": source, "b": source}, tmp_path / "same.wav", crossfade_ms=20,
    ))
    assert np.max(samples[95000:96100]) - np.min(samples[95000:96100]) < 5


def test_master_uses_sample_offsets_and_compensates_limiter_latency(tmp_path: Path) -> None:
    source = tmp_path / "impulse.wav"
    impulse = np.zeros(48000)
    impulse[0] = 4000
    write_audio(source, impulse)
    samples = decode(render_voice_master(
        [SourcePlacement("a", 1 / 48000, 0, 1)], {"a": source},
        tmp_path / "impulse-master.wav",
    ))
    assert len(samples) == 48001
    assert np.argmax(np.abs(samples)) == 1
