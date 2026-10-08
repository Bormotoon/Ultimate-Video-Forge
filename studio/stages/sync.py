"""Synchronization planning contracts and legacy-compatible complex placement."""

from __future__ import annotations

import json
from dataclasses import dataclass

from studio.core.project import Asset, AssetRole, Project, stable_fingerprint
from studio.core.timeline import AudioWarpMap, AudioWarpPiece, SourcePlacement, TimeDomain
from studio.core.transcript import Transcript
from studio.stages.base import Decision, GpuUse, Requirement, StageContext, StageOutput
from studio.stages.sync_alignment import align_sources
from studio.stages.sync_engine import render_aligned_clip
from studio.stages.sync_geometry import PieceConfig


@dataclass(frozen=True, slots=True)
class TextAnchor:
    camera_s: float
    recorder_s: float
    token: str


class SyncStage:
    id = "sync"
    title = "Synchronization"
    after: tuple[str, ...] = ("transcribe",)
    gpu = GpuUse.NONE

    def requirements(self, settings: dict[str, object]) -> list[Requirement]:
        return [Requirement("binary", "ffmpeg", "render synchronized audio")]

    def decide(self, project: Project, settings: dict[str, object]) -> Decision:
        cameras = [asset for asset in project.assets if asset.role is AssetRole.CAMERA]
        if not cameras:
            return Decision.skip("no camera assets")
        if len(cameras) == 1 and not any(
            asset.role is AssetRole.RECORDER for asset in project.assets
        ):
            return Decision.skip("single camera needs no synchronization")
        required = _required_transcripts(project)
        missing = [asset_id for asset_id in required if asset_id not in project.transcripts]
        if missing:
            return Decision.blocked(
                f"missing transcripts: {', '.join(missing)}", "run transcription"
            )
        return Decision.run({"mode": _sync_mode(settings)})

    def fingerprint(self, project: Project, settings: dict[str, object]) -> str:
        transcripts = {
            asset_id: path.read_text(encoding="utf-8")
            for asset_id, path in project.transcripts.items()
            if asset_id in {asset.id for asset in project.assets} and path.is_file()
        }
        from studio.stages.transcribe import _sha256

        return stable_fingerprint(
            "sync-v3", transcripts, project.assets,
            {asset.id: _sha256(project.source_dir / asset.path)
             for asset in project.assets if asset.role in {AssetRole.CAMERA, AssetRole.RECORDER}},
            settings.get("sync", {}),
        )

    def run(self, context: StageContext) -> StageOutput:
        mode = _sync_mode(context.settings)
        if mode == "camera" or not any(
            asset.role is AssetRole.RECORDER for asset in context.project.assets
        ):
            return _run_camera_placement(context)
        if mode == "complex" or (mode in {"auto", "simple"} and any(
            asset.role is AssetRole.RECORDER for asset in context.project.assets
        )):
            return _run_complex(context)
        cameras = [asset for asset in context.project.assets if asset.role is AssetRole.CAMERA]
        recorders = [asset for asset in context.project.assets if asset.role is AssetRole.RECORDER]
        placements: list[SourcePlacement] = []
        warps: list[AudioWarpMap] = []
        for index, camera in enumerate(cameras):
            duration = _duration(camera)
            camera_offset = 0.0
            provenance = "metadata"
            if index and not recorders:
                primary = Transcript.load(context.project.transcripts[cameras[0].id])
                candidate = Transcript.load(context.project.transcripts[camera.id])
                anchors = text_anchors(primary, candidate)
                camera_offset, _camera_k, _residual = fit_alignment(anchors)
                provenance = "text"
            placements.append(
                SourcePlacement(
                    camera.id,
                    camera_offset,
                    0.0,
                    duration,
                    provenance=provenance,
                )
            )
        if recorders:
            reference = recorders[0]
            recorder_transcript = Transcript.load(context.project.transcripts[reference.id])
            for camera in cameras:
                camera_transcript = Transcript.load(context.project.transcripts[camera.id])
                anchors = text_anchors(camera_transcript, recorder_transcript)
                offset, k, residual = fit_alignment(anchors)
                duration = _duration(reference)
                placements.append(
                    SourcePlacement(
                        reference.id,
                        offset,
                        0.0,
                        duration,
                        k,
                        "text",
                        {"inliers": float(len(anchors)), "residual_ms": residual * 1000},
                    )
                )
                selected_mode = choose_sync_mode(mode, duration, k, residual)
                strategy = 3 if selected_mode == "complex" else 1
                warps.append(
                    AudioWarpMap(
                        f"warp-{reference.id}-{camera.id}",
                        reference.id,
                        camera.id,
                        (AudioWarpPiece(0.0, duration, 0.0, duration * k, "resample"),),
                        TimeDomain.FILE,
                        strategy,
                        {"anchors": float(len(anchors)), "residual_ms": residual * 1000},
                    )
                )
        output = context.work_dir / "stages" / "sync" / "output.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": 1,
            "mode": mode,
            "placements": [
                {
                    "asset_id": item.asset_id,
                    "offset_s": item.offset_s,
                    "in_s": item.in_s,
                    "duration_s": item.duration_s,
                    "k": item.k,
                    "provenance": item.provenance,
                    "evidence": item.evidence,
                }
                for item in placements
            ],
            "audio_warp_maps": [warp.id for warp in warps],
        }
        output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return StageOutput(
            (output,), {"placements": placements, "audio_warp_maps": warps}
        )


def text_anchors(camera: Transcript, recorder: Transcript) -> list[TextAnchor]:
    camera_words: dict[str, list[float]] = {}
    recorder_words: dict[str, list[float]] = {}
    for word in camera.words:
        camera_words.setdefault(_normalize(word.text), []).append(word.start)
    for word in recorder.words:
        recorder_words.setdefault(_normalize(word.text), []).append(word.start)
    return [
        TextAnchor(camera_words[token][0], recorder_words[token][0], token)
        for token in camera_words.keys() & recorder_words.keys()
        if token and len(camera_words[token]) == len(recorder_words[token]) == 1
    ]


def _run_complex(context: StageContext) -> StageOutput:
    project = context.project
    cameras = [asset for asset in project.assets if asset.role is AssetRole.CAMERA]
    recorders = [asset for asset in project.assets if asset.role is AssetRole.RECORDER]
    if not recorders:
        raise ValueError("recorder rendering requires a recorder")
    recorder = recorders[0]
    transcript = Transcript.load(project.transcripts[recorder.id])
    alignments = []
    camera_transcripts = []
    for camera in cameras:
        candidate = Transcript.load(project.transcripts[camera.id])
        sync = context.settings.get("sync", {})
        alignment = align_sources(
            candidate, transcript, project.source_dir / camera.path,
            project.source_dir / recorder.path, sync if isinstance(sync, dict) else {},
            acoustic_first=_sync_mode(context.settings) in {"auto", "simple"},
        )
        alignments.append(alignment)
        camera_transcripts.append(candidate)
    if not alignments:
        raise ValueError("complex rendering requires a camera")
    reference = alignments[0]
    offsets = [reference.offset - reference.k * item.offset / item.k for item in alignments]
    origin = min(0.0, reference.offset, *offsets)
    placements = [SourcePlacement(
        recorder.id, reference.offset - origin, 0.0, transcript.duration,
        reference.k, "text",
    )]
    artifacts = []
    warps = []
    outputs = {key: paths for key, paths in project.outputs.items() if not key.startswith("sync:")}
    source = project.source_dir / recorder.path
    sync = context.settings.get("sync", {})
    sync = sync if isinstance(sync, dict) else {}
    mode = _sync_mode(context.settings)
    for extra in recorders[1:]:
        extra_transcript = Transcript.load(project.transcripts[extra.id])
        fitted = align_sources(
            transcript, extra_transcript, source, project.source_dir / extra.path,
            sync, acoustic_first=mode in {"auto", "simple"},
        )
        placements.append(SourcePlacement(
            extra.id, reference.offset + reference.k * fitted.offset - origin, 0,
            extra_transcript.duration, reference.k * fitted.k, fitted.provenance,
        ))
    for camera, candidate, alignment, offset in zip(
        cameras, camera_transcripts, alignments, offsets, strict=True,
    ):
        placements.append(SourcePlacement(
            camera.id, offset - origin, 0.0, candidate.duration,
            reference.k / alignment.k, alignment.provenance,
            {"inliers": float(alignment.inliers), "residual_ms": alignment.residual_ms},
        ))
        voice = context.work_dir / "stages" / "sync" / "voice" / f"{camera.id}.wav"
        selected = choose_sync_mode(
            mode, candidate.duration, alignment.k, alignment.residual_ms / 1000,
            max_drift_ms=float(sync.get("max_drift_ms", 20.0)),
        )
        strategy = int(sync.get("strategy", 3)) if selected == "complex" else 1
        plan = render_aligned_clip(
            alignment, PieceConfig(), source, voice,
            clip_duration_s=candidate.duration, recorder_duration_s=transcript.duration,
            recorder_words=[(word.start, word.end) for word in transcript.words],
            strategy=strategy,
            source_asset_id=recorder.id, target_asset_id=camera.id,
            channels=int(recorder.manual.get("media_info", {}).get("audio_channels") or 1),
            camera_audio=project.source_dir / camera.path if sync.get("boundary_flex") else None,
        )
        warps.append(plan.warp)
        artifacts.append(voice)
        outputs[f"sync:{camera.id}"] = [voice]
        choices = [(recorder.id, voice, alignment.residual_ms, alignment.inliers)]
        for extra in recorders[1:]:
            extra_transcript = Transcript.load(project.transcripts[extra.id])
            fitted = align_sources(
                candidate, extra_transcript, project.source_dir / camera.path,
                project.source_dir / extra.path, sync, acoustic_first=mode in {"auto", "simple"},
            )
            extra_voice = voice.with_name(f"{camera.id}-{extra.id}.wav")
            extra_mode = choose_sync_mode(
                mode, candidate.duration, fitted.k, fitted.residual_ms / 1000,
                max_drift_ms=float(sync.get("max_drift_ms", 20.0)),
            )
            extra_plan = render_aligned_clip(
                fitted, PieceConfig(), project.source_dir / extra.path, extra_voice,
                clip_duration_s=candidate.duration, recorder_duration_s=extra_transcript.duration,
                recorder_words=[(word.start, word.end) for word in extra_transcript.words],
                strategy=int(sync.get("strategy", 3)) if extra_mode == "complex" else 1,
                source_asset_id=extra.id, target_asset_id=camera.id,
                channels=int(extra.manual.get("media_info", {}).get("audio_channels") or 1),
                camera_audio=(project.source_dir / camera.path
                              if sync.get("boundary_flex") else None),
            )
            warps.append(extra_plan.warp)
            artifacts.append(extra_voice)
            choices.append((extra.id, extra_voice, fitted.residual_ms, fitted.inliers))
        choices.sort(key=lambda item: (item[2], -item[3]))
        outputs[f"sync:{camera.id}"] = [choices[0][1]]
        if sync.get("recorder_mode") == "all":
            outputs[f"sync-tracks:{camera.id}"] = [item[1] for item in choices]
    report = context.work_dir / "stages" / "sync" / "output.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schema_version": 1, "mode": mode,
        "strategies": {warp.target_asset_id: warp.strategy for warp in warps},
        "voices": {camera.id: str(outputs[f"sync:{camera.id}"][0].relative_to(report.parent))
                   for camera in cameras},
        "padding": "silence is outside invertible source maps",
    }, indent=2) + "\n", encoding="utf-8")
    artifacts.append(report)
    from studio.stages.sync_outputs import render_voice_master, segment_voice

    if float(sync.get("voice_segment_minutes", 0)) > 0:
        for camera in cameras:
            segments = segment_voice(
                outputs[f"sync:{camera.id}"][0], report.parent / "segments" / camera.id,
                float(sync["voice_segment_minutes"]),
            )
            artifacts.extend(segments)
            outputs[f"voice-segments:{camera.id}"] = list(segments)
    if sync.get("master_wav"):
        master = render_voice_master(
            placements, {camera.id: outputs[f"sync:{camera.id}"][0] for camera in cameras},
            context.work_dir / "export" / "voice-master.wav",
        )
        artifacts.append(master)
        outputs["master_wav"] = [master]
    if sync.get("verify"):
        from studio.stages.sync_verify import measure

        measurements = []
        for camera, candidate in zip(cameras, camera_transcripts, strict=True):
            verification = measure(project.source_dir / camera.path,
                                   outputs[f"sync:{camera.id}"][0],
                                   source_duration_s=candidate.duration)
            status, reason = verification.verdict(20.0)
            measurements.append({"asset_id": camera.id, "status": status, "reason": reason,
                                 **verification.summary()})
        verify_path = report.with_name("verify.json")
        verify_path.write_text(json.dumps({"schema_version": 1, "clips": measurements},
                                          indent=2) + "\n", encoding="utf-8")
        artifacts.append(verify_path)
        outputs["verify"] = [verify_path]
    return StageOutput(tuple(artifacts), {
        "placements": placements, "audio_warp_maps": warps, "outputs": outputs,
    })


def _run_camera_placement(context: StageContext) -> StageOutput:
    project = context.project
    cameras = [asset for asset in project.assets if asset.role is AssetRole.CAMERA]
    if not cameras:
        raise ValueError("no camera assets")
    reference = cameras[0]
    conf = context.settings.get("sync", {})
    conf = conf if isinstance(conf, dict) else {}
    primary = (Transcript.load(project.transcripts[reference.id])
               if reference.id in project.transcripts else
               Transcript(project.source_dir / reference.path, "", _duration(reference), []))
    placements = [SourcePlacement(reference.id, 0, 0, primary.duration, provenance="metadata")]
    for camera in cameras[1:]:
        candidate = (Transcript.load(project.transcripts[camera.id])
                     if camera.id in project.transcripts else
                     Transcript(project.source_dir / camera.path, "", _duration(camera), []))
        fitted = align_sources(
            primary, candidate, project.source_dir / reference.path,
            project.source_dir / camera.path, conf, acoustic_first=True,
        )
        placements.append(SourcePlacement(
            camera.id, fitted.offset, 0, candidate.duration, fitted.k, fitted.provenance,
            {"inliers": float(fitted.inliers), "residual_ms": fitted.residual_ms},
        ))
    origin = min(item.offset_s for item in placements)
    from dataclasses import replace

    placements = [replace(item, offset_s=item.offset_s - origin) for item in placements]
    output = context.work_dir / "stages" / "sync" / "output.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"schema_version": 1, "mode": "camera",
                                  "placement_ids": [item.asset_id for item in placements]},
                                 indent=2) + "\n", encoding="utf-8")
    return StageOutput((output,), {"placements": placements, "audio_warp_maps": [],
                                  "outputs": {key: paths for key, paths in project.outputs.items()
                                              if not key.startswith("sync:")}})


def fit_alignment(anchors: list[TextAnchor]) -> tuple[float, float, float]:
    if len(anchors) < 2:
        raise ValueError("at least two unique text anchors are required")
    xs = [anchor.recorder_s for anchor in anchors]
    ys = [anchor.camera_s for anchor in anchors]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((value - mean_x) ** 2 for value in xs)
    if denominator == 0:
        raise ValueError("text anchors do not span recorder time")
    k = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator
    offset = mean_y - k * mean_x
    residual = (
        sum((y - (offset + k * x)) ** 2 for x, y in zip(xs, ys, strict=True)) / len(xs)
    ) ** 0.5
    return offset, k, residual


def choose_sync_mode(
    requested: str,
    duration_s: float,
    k: float,
    residual_s: float,
    *,
    max_drift_ms: float = 20.0,
) -> str:
    if requested != "auto":
        return requested
    accumulated_drift_ms = abs(k - 1.0) * duration_s * 1000
    if accumulated_drift_ms < max_drift_ms and residual_s < 0.02:
        return "simple"
    return "complex"


def _required_transcripts(project: Project) -> list[str]:
    path = project.work_dir / "stages" / "prepare" / "requirements.json"
    if not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("transcripts", []))


def _sync_mode(settings: dict[str, object]) -> str:
    sync = settings.get("sync", {})
    return str(sync.get("mode", "auto")) if isinstance(sync, dict) else "auto"


def _duration(asset: Asset) -> float:
    return float(asset.manual.get("media_info", {}).get("duration_s", 0.0))


def _normalize(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())
