"""Versioned transcript model compatible with the Forge JSON shape."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.core.timeline import TimeDomain

TRANSCRIPT_SCHEMA_VERSION = 1
_SENTENCE_END = re.compile(r"[.!?…！？]$")


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    start: float
    end: float
    probability: float = 1.0


@dataclass(frozen=True, slots=True)
class Segment:
    start: float
    end: float
    words: tuple[Word, ...] = ()
    confidence: float | None = None
    avg_logprob: float | None = None

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words).strip()


@dataclass(slots=True)
class Transcript:
    source_audio: Path
    language: str
    duration: float
    segments: list[Segment]
    model: str = ""
    device: str = ""
    compute_type: str = ""
    mode: str = ""
    time_domain: TimeDomain = TimeDomain.FILE
    map_id: str | None = None
    schema_version: int = TRANSCRIPT_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def words(self) -> list[Word]:
        return [word for segment in self.segments for word in segment.words]

    def to_dict(self) -> dict[str, Any]:
        segments = [
            {
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text,
                "words": [
                    {
                        "start": round(word.start, 3),
                        "end": round(word.end, 3),
                        "word": word.text,
                        "probability": round(word.probability, 3),
                    }
                    for word in segment.words
                ],
                **(
                    {"confidence": round(segment.confidence, 3)}
                    if segment.confidence is not None
                    else {}
                ),
                **(
                    {"avg_logprob": round(segment.avg_logprob, 3)}
                    if segment.avg_logprob is not None
                    else {}
                ),
            }
            for segment in self.segments
        ]
        return {
            "schema_version": self.schema_version,
            "audio": str(self.source_audio),
            "source_audio": str(self.source_audio),
            "model": self.model,
            "device": self.device,
            "compute_type": self.compute_type,
            "mode": self.mode,
            "language": self.language,
            "duration": self.duration,
            "timing_version": 2,
            "time_domain": self.time_domain.value,
            "map_id": self.map_id,
            "segments": segments,
            "sentences": sentence_groups(self.segments),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        schema = int(data.get("schema_version", TRANSCRIPT_SCHEMA_VERSION))
        if schema != TRANSCRIPT_SCHEMA_VERSION:
            raise ValueError(f"unsupported transcript schema: {schema}")
        return cls(
            source_audio=Path(data.get("source_audio") or data["audio"]),
            language=str(data.get("language", "")),
            duration=float(data.get("duration", 0.0)),
            segments=[
                Segment(
                    start=float(segment["start"]),
                    end=float(segment["end"]),
                    words=tuple(
                        Word(
                            text=str(word.get("word", word.get("text", ""))),
                            start=float(word["start"]),
                            end=float(word["end"]),
                            probability=float(word.get("probability", 1.0)),
                        )
                        for word in segment.get("words", [])
                    ),
                    confidence=_optional_float(segment.get("confidence")),
                    avg_logprob=_optional_float(segment.get("avg_logprob")),
                )
                for segment in data.get("segments", [])
            ],
            model=str(data.get("model", "")),
            device=str(data.get("device", "")),
            compute_type=str(data.get("compute_type", "")),
            mode=str(data.get("mode", "")),
            time_domain=TimeDomain(data.get("time_domain", TimeDomain.FILE.value)),
            map_id=data.get("map_id"),
            schema_version=schema,
            metadata=data.get("metadata", {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
        path.write_text(serialized, encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Transcript:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def sentence_groups(segments: list[Segment], max_chars: int = 140) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current: list[Segment] = []
    for segment in segments:
        if not segment.text:
            continue
        current.append(segment)
        text = " ".join(item.text for item in current)
        if _SENTENCE_END.search(segment.text) or len(text) >= max_chars:
            groups.append(_sentence(current, text))
            current = []
    if current:
        groups.append(_sentence(current, " ".join(item.text for item in current)))
    return groups


def to_srt(transcript: Transcript) -> str:
    cues: list[str] = []
    for index, segment in enumerate((item for item in transcript.segments if item.text), 1):
        cues.extend(
            [
                str(index),
                f"{_srt_time(segment.start)} --> {_srt_time(segment.end)}",
                segment.text,
                "",
            ]
        )
    return "\n".join(cues).rstrip() + "\n"


def _sentence(segments: list[Segment], text: str) -> dict[str, Any]:
    return {
        "start": round(segments[0].start, 3),
        "end": round(segments[-1].end, 3),
        "text": text,
        "segment_count": len(segments),
    }


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def _optional_float(value: object) -> float | None:
    return float(str(value)) if value is not None else None
