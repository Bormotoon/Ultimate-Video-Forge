"""Render a review master and derive its edited-time transcript."""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from studio.core.project import AssetRole, Project, describe_artifact, stable_fingerprint
from studio.core.timeline import (
    EditMap,
    KeepRange,
    TimeDomain,
    timeline_to_edited,
    timeline_to_file,
)
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.base import Decision, GpuUse, Requirement, StageContext, StageOutput
from studio.stages.program_encoder import select_encoder


class ProgramStage:
    id = "program"
    title = "Review master"
    after: tuple[str, ...] = ("timeline", "roughcut")
    optional_after = ("roughcut",)
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
            "program-v4", edit, project.placements, project.outputs.get("sync"),
            [describe_artifact(project.work_dir, path)
             for key, paths in project.outputs.items() if key.startswith("sync:")
             for path in paths if path.is_file()],
            [describe_artifact(project.source_dir, project.source_dir / asset.path)
             for asset in project.assets if asset.role is AssetRole.CAMERA],
            settings.get("program", {}), settings.get("roughcut", {}),
        )

    def run(self, context: StageContext) -> StageOutput:
        timeline = Transcript.load(context.project.transcripts["timeline"])
        edit_path = _edit_path(context.project)
        roughcut = context.settings.get("roughcut", {})
        use_edit = roughcut.get("enabled", True) if isinstance(roughcut, dict) else True
        edit = (load_edit_map(edit_path) if edit_path.is_file() and use_edit else
                EditMap("uncut", (KeepRange(0.0, timeline.duration),)))
        output = context.work_dir / "program" / "program.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        conf = context.settings.get("program", {})
        conf = conf if isinstance(conf, dict) else {}
        render_report = render_project_program(
            context.project, edit, output, encoder=str(conf.get("encoder", "auto")),
        )
        edited = map_transcript_to_edited(timeline, edit)
        edited_path = context.work_dir / "program" / "program.transcript.json"
        edited.save(edited_path)
        report_path = output.with_name("render.json")
        report_path.write_text(json.dumps(render_report, indent=2) + "\n", encoding="utf-8")
        transcripts = {**context.project.transcripts, "edited": edited_path}
        outputs = {**context.project.outputs, "program": [output], "program_report": [report_path]}
        return StageOutput(
            (output, edited_path, report_path), {"transcripts": transcripts, "outputs": outputs}
        )


def load_edit_map(path: Path) -> EditMap:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("mode") == "markers":
        ranges = data.get("keep", [])
        if ranges:
            return EditMap("uncut", (KeepRange(
                min(float(item["start"]) for item in ranges),
                max(float(item["end"]) for item in ranges),
            ),))
    return EditMap(
        "roughcut",
        tuple(
            KeepRange(float(item["start"]), float(item["end"]), item.get("camera_id"))
            for item in data.get("keep", [])
        ),
    )


@dataclass(frozen=True)
class ProgramPiece:
    video: Path
    audio: Path | None
    video_in_s: float
    audio_in_s: float
    duration_s: float
    rate: float


def program_pieces(project: Project, edit: EditMap) -> list[ProgramPiece]:
    cameras = {asset.id: asset for asset in project.assets if asset.role is AssetRole.CAMERA}
    placements = [item for item in project.placements if item.asset_id in cameras]
    result = []
    for keep in edit.keep:
        boundaries = sorted({keep.start_s, keep.end_s} | {
            value for item in placements
            for value in (item.offset_s, item.offset_s + item.duration_s * item.k)
            if keep.start_s < value < keep.end_s
        })
        for start, end in zip(boundaries, boundaries[1:], strict=False):
            available = [item for item in placements
                         if item.offset_s <= start + 1e-6
                         and item.offset_s + item.duration_s * item.k >= end - 1e-6]
            if keep.camera_id:
                group_ids = {asset.id for asset in cameras.values()
                             if asset.group_id == keep.camera_id or asset.id == keep.camera_id}
                available = [item for item in available if item.asset_id in group_ids]
            if not available:
                raise ValueError(f"no camera covers retained range {start:.3f}-{end:.3f}")
            placement = available[0]
            asset = cameras[placement.asset_id]
            source = project.source_dir / asset.path
            voice = next(iter(project.outputs.get(f"sync:{asset.id}", [])), None)
            has_audio = bool(asset.manual.get("media_info", {}).get("audio_codec"))
            audio = voice or (source if has_audio else None)
            source_in = timeline_to_file(start, placement)
            result.append(ProgramPiece(source, audio, source_in, source_in,
                                       end - start, placement.k))
    if not result:
        raise ValueError("cannot render empty retained ranges")
    return result


def render_project_program(
    project: Project, edit: EditMap, output: Path, *, encoder: str = "auto",
) -> dict[str, object]:
    pieces = program_pieces(project, edit)
    first = next(asset for asset in project.assets
                 if project.source_dir / asset.path == pieces[0].video)
    info = first.manual.get("media_info", {})
    width, height = int(info.get("width") or 1920), int(info.get("height") or 1080)
    fps = str(info.get("fps") or 25)
    choice = select_encoder(encoder)
    codec = choice.codec
    fallback_reason = None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".render-", dir=output.parent) as temporary:
        directory = Path(temporary)
        for index, piece in enumerate(pieces):
            duration = piece.duration_s / piece.rate
            command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(piece.video)]
            if piece.audio is not None:
                command.extend(["-i", str(piece.audio)])
                audio = (f"[1:a]atrim=start={piece.audio_in_s}:duration={duration},"
                         f"asetpts=PTS-STARTPTS,atempo={1 / piece.rate},"
                         "aresample=48000,aformat=channel_layouts=stereo,"
                         f"apad,atrim=duration={piece.duration_s}[a]")
            else:
                audio = ("anullsrc=r=48000:cl=stereo,"
                         f"atrim=duration={piece.duration_s}[a]")
            video = (f"[0:v]trim=start={piece.video_in_s}:duration={duration},"
                     f"setpts={piece.rate}*(PTS-STARTPTS),"
                     f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                     f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v]")
            command.extend(["-filter_complex", video + ";" + audio,
                            "-map", "[v]", "-map", "[a]", "-c:v", codec,
                            "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le",
                            "-t", str(piece.duration_s), str(directory / f"part-{index}.mkv")])
            try:
                _run_ffmpeg(command)
            except RuntimeError as exc:
                if encoder != "auto" or codec != "h264_nvenc":
                    raise
                # Regenerate every part to avoid joining different H.264
                # parameter sets when hardware fails halfway through a job.
                retry = render_project_program(project, edit, output, encoder="cpu")
                retry.update({"requested_encoder": encoder, "initial_codec": choice.codec,
                              "selection_reason": choice.reason,
                              "runtime_fallback_reason": str(exc)[-2000:]})
                return retry
        listing = directory / "parts.txt"
        listing.write_text("".join(f"file 'part-{index}.mkv'\n"
                                   for index in range(len(pieces))), encoding="utf-8")
        _run_ffmpeg(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat",
                     "-safe", "0", "-i", str(listing), "-c:v", "copy", "-c:a", "aac",
                     "-movflags", "+faststart", str(output)])
    return {"schema_version": 1, "requested_encoder": encoder,
            "initial_codec": choice.codec, "final_codec": codec,
            "selection_reason": choice.reason, "runtime_fallback_reason": fallback_reason,
            "width": width, "height": height, "fps": fps, "piece_count": len(pieces),
            "duration_s": sum(piece.duration_s for piece in pieces)}


def _run_ffmpeg(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"program render failed: {result.stderr.strip()}")


def map_transcript_to_edited(transcript: Transcript, edit: EditMap) -> Transcript:
    segments: list[Segment] = []
    for segment in transcript.segments:
        words: list[Word] = []
        for word in segment.words:
            if not any(item.start_s <= word.start and word.end <= item.end_s
                       for item in edit.keep):
                continue
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
