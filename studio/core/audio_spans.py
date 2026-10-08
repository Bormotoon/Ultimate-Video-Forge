"""Shared speech-gap and ffmpeg silence interval analysis."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from studio.core.transcript import Word

_SILENCE_START = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?[0-9.]+)")


@dataclass(frozen=True, slots=True)
class Span:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def word_gaps(words: list[Word], *, minimum_s: float) -> list[Span]:
    ordered = sorted(words, key=lambda word: word.start)
    return [
        Span(left.end, right.start)
        for left, right in zip(ordered, ordered[1:], strict=False)
        if right.start - left.end >= minimum_s
    ]


def parse_silencedetect(stderr: str, duration_s: float | None = None) -> list[Span]:
    pending: float | None = None
    spans: list[Span] = []
    for line in stderr.splitlines():
        start = _SILENCE_START.search(line)
        if start:
            pending = float(start.group(1))
        end = _SILENCE_END.search(line)
        if end and pending is not None:
            value = float(end.group(1))
            if value > pending:
                spans.append(Span(pending, value))
            pending = None
    if pending is not None and duration_s is not None and duration_s > pending:
        spans.append(Span(max(0.0, pending), duration_s))
    return spans


def detect_silence(
    source: Path, *, threshold_db: float = -40.0, minimum_s: float = 0.1,
    duration_s: float | None = None,
) -> list[Span]:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(source),
         "-map", "0:a:0", "-vn", "-af",
         f"silencedetect=noise={threshold_db}dB:d={minimum_s}:mono=0",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(f"silence analysis failed: {result.stderr.strip()}")
    return parse_silencedetect(result.stderr, duration_s)


def intersect(left: list[Span], right: list[Span]) -> list[Span]:
    result: list[Span] = []
    for first in left:
        for second in right:
            start = max(first.start_s, second.start_s)
            end = min(first.end_s, second.end_s)
            if end > start:
                result.append(Span(start, end))
    return result
