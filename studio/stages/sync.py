"""Synchronization planning contracts and legacy-compatible complex placement."""

from __future__ import annotations

import json
from dataclasses import dataclass

from studio.core.project import Asset, AssetRole, Project, stable_fingerprint
from studio.core.timeline import AudioWarpMap, AudioWarpPiece, SourcePlacement, TimeDomain
from studio.core.transcript import Transcript
from studio.stages.base import Decision, GpuUse, Requirement, StageContext, StageOutput


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
            if path.is_file()
        }
        return stable_fingerprint("sync-v1", transcripts, settings.get("sync", {}))

    def run(self, context: StageContext) -> StageOutput:
        mode = _sync_mode(context.settings)
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
