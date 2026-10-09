"""Public binary facade for migrated vision code."""

import shutil


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"
