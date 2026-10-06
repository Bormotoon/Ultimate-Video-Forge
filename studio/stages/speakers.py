"""Assign transcript words to isolated microphone tracks by relative energy."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from studio.core.project import Project, stable_fingerprint
from studio.core.transcript import Word
from studio.stages.base import Decision, GpuUse, Requirement, StageContext, StageOutput


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str
    source: str = "mics"


class SpeakersStage:
    id = "speakers"
    title = "Speakers"
    after: tuple[str, ...] = ("timeline",)
    gpu = GpuUse.NONE

    def requirements(self, settings: dict[str, object]) -> list[Requirement]:
        return []

    def decide(self, project: Project, settings: dict[str, object]) -> Decision:
        speakers = settings.get("speakers", {})
        method = str(speakers.get("method", "off")) if isinstance(speakers, dict) else "off"
        return Decision.skip("speaker detection is off") if method == "off" else Decision.run(
            {"method": method}
        )

    def fingerprint(self, project: Project, settings: dict[str, object]) -> str:
        transcript = project.transcripts.get("timeline")
        content = (
            transcript.read_text(encoding="utf-8")
            if transcript and transcript.is_file()
            else ""
        )
        return stable_fingerprint("speakers-v1", content, settings.get("speakers", {}))

    def run(self, context: StageContext) -> StageOutput:
        raise RuntimeError("speaker stage requires decoded microphone envelopes")


def assign_words_to_mics(
    words: list[Word],
    envelopes: dict[str, np.ndarray],
    *,
    step_s: float = 0.05,
    margin_db: float = 6.0,
) -> list[SpeakerTurn]:
    assignments: list[SpeakerTurn] = []
    for word in words:
        start = max(0, int(word.start / step_s))
        end = max(start + 1, math.ceil(word.end / step_s))
        energies = sorted(
            (
                (speaker, float(np.mean(np.square(values[start:end]))))
                for speaker, values in envelopes.items()
                if len(values[start:end])
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        if not energies:
            speaker = "unknown"
        elif len(energies) == 1:
            speaker = energies[0][0]
        else:
            first_db = 10 * math.log10(max(energies[0][1], 1e-12))
            second_db = 10 * math.log10(max(energies[1][1], 1e-12))
            speaker = energies[0][0] if first_db - second_db >= margin_db else "overlap"
        assignments.append(SpeakerTurn(word.start, word.end, speaker))
    return merge_adjacent_turns(assignments)


def merge_adjacent_turns(turns: list[SpeakerTurn], max_gap_s: float = 0.3) -> list[SpeakerTurn]:
    merged: list[SpeakerTurn] = []
    for turn in turns:
        same_speaker = merged and merged[-1].speaker == turn.speaker
        close = merged and turn.start - merged[-1].end <= max_gap_s
        if same_speaker and close:
            previous = merged[-1]
            merged[-1] = SpeakerTurn(previous.start, turn.end, turn.speaker, turn.source)
        else:
            merged.append(turn)
    return merged


def save_turns(path: Path, turns: list[SpeakerTurn]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(turn) for turn in turns], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
