"""Probe usable hardware encoding rather than trusting compiled-in support."""

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class EncoderChoice:
    requested: str
    codec: str
    reason: str


def select_encoder(requested: str) -> EncoderChoice:
    if requested not in {"auto", "cpu", "nvenc"}:
        raise ValueError(f"unknown program encoder: {requested}")
    if requested == "cpu":
        return EncoderChoice(requested, "libx264", "CPU explicitly requested")
    try:
        result = subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "color=c=black:s=128x128:r=25", "-frames:v", "1",
            "-c:v", "h264_nvenc", "-pix_fmt", "yuv420p", "-f", "null", "-",
        ], capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            return EncoderChoice(requested, "h264_nvenc", "NVENC encode probe passed")
        reason = result.stderr.strip()[-2000:] or f"ffmpeg exited {result.returncode}"
    except subprocess.TimeoutExpired:
        reason = "NVENC encode probe timed out after 20 seconds"
    if requested == "nvenc":
        raise RuntimeError(f"requested NVENC is unavailable: {reason}")
    return EncoderChoice(requested, "libx264", f"NVENC unavailable: {reason}")
