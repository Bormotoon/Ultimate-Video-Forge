"""Deterministic rough-cut decisions from the timeline transcript."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from studio.core.audio_spans import word_gaps
from studio.core.project import Project, stable_fingerprint
from studio.core.transcript import Transcript
from studio.stages.base import Decision, GpuUse, Requirement, StageContext, StageOutput

FILLERS = {"ну", "вот", "типа", "короче", "ээ", "эээ", "мм", "um", "uh"}


@dataclass(frozen=True, slots=True)
class Cut:
    start: float
    end: float
    reason: str
    confidence: float
    note: str
    accepted: bool = True


@dataclass(frozen=True, slots=True)
class Marker:
    at: float
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class EditList:
    mode: str
    keep: tuple[tuple[float, float], ...]
    cuts: tuple[Cut, ...]
    markers: tuple[Marker, ...]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": 1,
            "mode": self.mode,
            "keep": [
                {"start": start, "end": end, "camera_id": None}
                for start, end in self.keep
            ],
            "cuts": [asdict(cut) for cut in self.cuts],
            "markers": [asdict(marker) for marker in self.markers],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RoughcutStage:
    id = "roughcut"
    title = "Rough cut"
    after: tuple[str, ...] = ("timeline",)
    gpu = GpuUse.NONE

    def requirements(self, settings: dict[str, object]) -> list[Requirement]:
        return []

    def decide(self, project: Project, settings: dict[str, object]) -> Decision:
        roughcut = settings.get("roughcut", {})
        enabled = roughcut.get("enabled", True) if isinstance(roughcut, dict) else True
        if not enabled:
            return Decision.skip("disabled by user")
        return Decision.run() if "timeline" in project.transcripts else Decision.blocked(
            "timeline transcript is missing", "run timeline"
        )

    def fingerprint(self, project: Project, settings: dict[str, object]) -> str:
        path = project.transcripts.get("timeline")
        content = path.read_text(encoding="utf-8") if path and path.is_file() else ""
        return stable_fingerprint("roughcut-v1", content, settings.get("roughcut", {}))

    def run(self, context: StageContext) -> StageOutput:
        transcript = Transcript.load(context.project.transcripts["timeline"])
        roughcut = context.settings.get("roughcut", {})
        conf = roughcut if isinstance(roughcut, dict) else {}
        edit = build_edit_list(
            transcript,
            mode=str(conf.get("mode", "cut")),
            minimum_pause_s=float(conf.get("pause_min_s", 1.0)),
            keep_pause_s=float(conf.get("pause_keep_s", 0.4)),
            edge_pad_s=float(conf.get("head_tail_pad_s", 0.2)),
        )
        output = context.work_dir / "stages" / "roughcut" / "edit.json"
        edit.save(output)
        outputs = {**context.project.outputs, "roughcut": [output]}
        return StageOutput((output,), {"outputs": outputs})


def build_edit_list(
    transcript: Transcript,
    *,
    mode: str,
    minimum_pause_s: float,
    keep_pause_s: float,
    edge_pad_s: float,
) -> EditList:
    words = transcript.words
    if not words:
        return EditList(mode, (), (), ())
    cuts: list[Cut] = []
    first = min(word.start for word in words)
    last = max(word.end for word in words)
    if first > edge_pad_s:
        cuts.append(Cut(0.0, first - edge_pad_s, "head", 1.0, "before first word"))
    if transcript.duration > last + edge_pad_s:
        cuts.append(
            Cut(last + edge_pad_s, transcript.duration, "tail", 1.0, "after last word")
        )
    for gap in word_gaps(words, minimum_s=minimum_pause_s):
        trim = max(0.0, gap.duration_s - keep_pause_s)
        start = gap.start_s + keep_pause_s / 2
        cuts.append(
            Cut(start, start + trim, "pause", 0.95, f"pause {gap.duration_s:.2f}s")
        )
    cuts.sort(key=lambda cut: cut.start)
    markers = tuple(
        Marker(word.start, "filler", word.text)
        for word in words
        if word.text.casefold().strip(".,!?…") in FILLERS
    )
    keep = (
        _complement(transcript.duration, cuts)
        if mode == "cut"
        else ((0.0, transcript.duration),)
    )
    return EditList(mode, keep, tuple(cuts), markers)


def _complement(duration: float, cuts: list[Cut]) -> tuple[tuple[float, float], ...]:
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for cut in cuts:
        if cut.accepted and cut.start > cursor:
            keep.append((cursor, cut.start))
        if cut.accepted:
            cursor = max(cursor, cut.end)
    if cursor < duration:
        keep.append((cursor, duration))
    return tuple((start, end) for start, end in keep if end > start)
