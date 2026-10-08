"""Private ffmpeg decoding used by the migrated waveform algorithms."""

import subprocess
from pathlib import Path


def extract_audio_to_wav(
    source: Path, output: Path, *, sample_rate: int = 16000, mono: bool = True,
    start_s: float | None = None, duration_s: float | None = None,
) -> None:
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    if start_s is not None:
        command.extend(["-ss", str(start_s)])
    command.extend(["-i", str(source), "-map", "0:a:0", "-vn"])
    if duration_s is not None:
        command.extend(["-t", str(duration_s)])
    if mono:
        command.extend(["-ac", "1"])
    command.extend(["-ar", str(sample_rate), "-c:a", "pcm_s16le", str(output)])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"acoustic decoding failed: {result.stderr.strip()}")
