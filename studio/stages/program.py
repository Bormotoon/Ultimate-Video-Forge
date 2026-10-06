"""Render a review master and derive its edited-time transcript."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from studio.core.project import AssetRole, Project, stable_fingerprint
from studio.core.timeline import EditMap, KeepRange, TimeDomain, timeline_to_edited
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.base import Decision, GpuUse, Requirement, StageContext, StageOutput


class ProgramStage:
    id = "program"
    title = "Review master"
    after: tuple[str, ...] = ("roughcut",)
    gpu = GpuUse.NVENC

    def requirements(self, settings: dict[str, object]) -> list[Requirement]:
        return [Requirement("binary", "ffmpeg", "render the review master")]

    def decide(self, project: Project, settings: dict[str, object]) -> Decision:
        program = settings.get("program", {})
        enabled = program.get("enabled", False) if isinstance(program, dict) else False
        return Decision.run() if enabled else Decision.skip("program render is disabled")

    def fingerprint(self, project: Project, settings: dict[str, object]) -> str:
        edit_path = _edit_path(project)
        edit = edit_path.read_text(encoding="utf-8") if edit_path.is_file() else ""
        return stable_fingerprint(
            "program-v1", edit, project.placements, settings.get("program", {})
        )

    def run(self, context: StageContext) -> StageOutput:
        camera = next(
            asset for asset in context.project.assets if asset.role is AssetRole.CAMERA
        )
        source = context.project.source_dir / camera.path
        edit = load_edit_map(_edit_path(context.project))
        output = context.work_dir / "program" / "program.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        render_program(source, edit, output)
        timeline = Transcript.load(context.project.transcripts["timeline"])
        edited = map_transcript_to_edited(timeline, edit)
        edited_path = context.work_dir / "program" / "program.transcript.json"
        edited.save(edited_path)
        transcripts = {**context.project.transcripts, "edited": edited_path}
        outputs = {**context.project.outputs, "program": [output]}
        return StageOutput(
            (output, edited_path), {"transcripts": transcripts, "outputs": outputs}
        )


def load_edit_map(path: Path) -> EditMap:
    data = json.loads(path.read_text(encoding="utf-8"))
    return EditMap(
        "roughcut",
        tuple(
            KeepRange(float(item["start"]), float(item["end"]), item.get("camera_id"))
            for item in data.get("keep", [])
        ),
    )


def map_transcript_to_edited(transcript: Transcript, edit: EditMap) -> Transcript:
    segments: list[Segment] = []
    for segment in transcript.segments:
        words: list[Word] = []
        for word in segment.words:
            start = timeline_to_edited(word.start, edit)
            end = timeline_to_edited(word.end, edit)
            if start is not None and end is not None:
                words.append(Word(word.text, start, end, word.probability))
        if words:
            segments.append(
                Segment(words[0].start, words[-1].end, tuple(words), segment.confidence)
            )
    duration = sum(item.end_s - item.start_s for item in edit.keep)
    return Transcript(
        transcript.source_audio,
        transcript.language,
        duration,
        segments,
        transcript.model,
        transcript.device,
        transcript.compute_type,
        transcript.mode,
        TimeDomain.EDITED,
        edit.id,
    )


def render_program(source: Path, edit: EditMap, output: Path) -> None:
    if not edit.keep:
        raise ValueError("cannot render a program with no retained ranges")
    filters: list[str] = []
    inputs: list[str] = []
    for index, item in enumerate(edit.keep):
        filters.extend(
            [
                f"[0:v]trim=start={item.start_s}:end={item.end_s},setpts=PTS-STARTPTS[v{index}]",
                f"[0:a]atrim=start={item.start_s}:end={item.end_s},asetpts=PTS-STARTPTS[a{index}]",
            ]
        )
        inputs.append(f"[v{index}][a{index}]")
    filters.append(f"{''.join(inputs)}concat=n={len(edit.keep)}:v=1:a=1[v][a]")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-c:a",
        "aac",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"program render failed: {result.stderr.strip()}")


def _edit_path(project: Project) -> Path:
    outputs = project.outputs.get("roughcut", [])
    return outputs[0] if outputs else project.work_dir / "stages" / "roughcut" / "edit.json"
