"""Legacy audio-backend media facade over the public probe implementation."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from studio.core.media import probe as public_probe


@dataclass(frozen=True)
class AudioInfo:
    duration: float
    audio_sample_rate: int | None
    audio_channels: int | None
    audio_codec: str | None
    audio_bits_per_sample: int | None


def probe(path: Path) -> AudioInfo:
    info = public_probe(path)
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                             "-show_streams", "-of", "json", str(path)],
                            capture_output=True, text=True, check=True)
    stream = json.loads(result.stdout)["streams"][0]
    bits = int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 0)
    return AudioInfo(info.duration_s, info.audio_sample_rate, info.audio_channels,
                     info.audio_codec, bits or None)


def pcm_codec_for(info: AudioInfo) -> str:
    bits = info.audio_bits_per_sample or 24
    return "pcm_s16le" if bits <= 16 else "pcm_s24le" if bits <= 24 else "pcm_s32le"
