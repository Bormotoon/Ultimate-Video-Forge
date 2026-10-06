"""Shared speech-gap and ffmpeg silence interval analysis."""

from __future__ import annotations

import re
from dataclasses import dataclass

from studio.core.transcript import Word

_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")


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


def parse_silencedetect(stderr: str) -> list[Span]:
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
    return spans


def intersect(left: list[Span], right: list[Span]) -> list[Span]:
    result: list[Span] = []
    for first in left:
        for second in right:
            start = max(first.start_s, second.start_s)
            end = min(first.end_s, second.end_s)
            if end > start:
                result.append(Span(start, end))
    return result
