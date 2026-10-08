"""Conservative repair candidates: publish only after content and lag checks."""

import json
from dataclasses import asdict, replace
from pathlib import Path

from studio.core.timeline import AudioWarpMap
from studio.core.transcript import Transcript
from studio.stages.sync_alignment import align_sources
from studio.stages.sync_check_settings import SelfCheckSettings
from studio.stages.sync_engine import render_aligned_clip
from studio.stages.sync_geometry import PieceConfig
from studio.stages.sync_self_check import check_rendered_clip
from studio.stages.sync_verify import measure
from studio.stages.transcribe import TranscribeSettings, WhisperEngine


def repair_voices(
    references: dict[str, Transcript], camera_paths: dict[str, Path],
    sources: dict[str, tuple[str, Path, Transcript, int]],
    directory: Path, check_report: Path, transcribe_settings: object,
) -> tuple[dict[str, Path], dict[str, AudioWarpMap], tuple[Path, ...]]:
    initial = json.loads(check_report.read_text(encoding="utf-8"))
    failed = {item["asset_id"] for item in initial["clips"] if item["status"] == "failed"}
    replacements = {}
    maps = {}
    artifacts = []
    reports = []
    directory.mkdir(parents=True, exist_ok=True)
    engine = WhisperEngine(TranscribeSettings.from_mapping(transcribe_settings))
    try:
        for asset_id in sorted(failed):
            reference = references[asset_id]
            source_id, source_path, source_transcript, channels = sources[asset_id]
            entry = {"asset_id": asset_id, "accepted": False}
            candidate = directory / f"{asset_id}-candidate.wav"
            try:
                # Re-derive a whole-clip map; a fit valid near one flagged span
                # cannot safely be extrapolated over the remaining recording.
                alignment = align_sources(
                    reference, source_transcript, camera_paths[asset_id], source_path,
                    {}, acoustic_first=True,
                )
                plan = render_aligned_clip(
                    alignment, PieceConfig(), source_path, candidate,
                    clip_duration_s=reference.duration,
                    recorder_duration_s=source_transcript.duration,
                    recorder_words=[(word.start, word.end) for word in source_transcript.words],
                    strategy=1, source_asset_id=source_id, target_asset_id=asset_id,
                    channels=channels,
                )
                rendered = engine.transcribe(candidate)
                outcome = check_rendered_clip(
                    rendered.words, reference.words, SelfCheckSettings(), reference.duration,
                )
                verification = measure(camera_paths[asset_id], candidate,
                                       source_duration_s=reference.duration)
                status, reason = verification.verdict(20.0)
                entry.update({"content": asdict(outcome), "lag_status": status,
                              "lag_reason": reason, "lag": verification.summary()})
                if outcome.ok and status == "passed":
                    final = directory / f"{asset_id}-repaired.wav"
                    candidate.replace(final)
                    replacements[asset_id] = final
                    maps[asset_id] = replace(plan.warp, evidence={
                        **(plan.warp.evidence or {}), "repair_verified": 1.0,
                    })
                    artifacts.append(final)
                    rendered.source_audio = final
                    transcript_path = directory / f"{asset_id}-repaired.json"
                    rendered.save(transcript_path)
                    artifacts.append(transcript_path)
                    entry["accepted"] = True
                else:
                    entry["reason"] = "candidate did not pass both content and lag verification"
            except (RuntimeError, ValueError, OSError) as exc:
                entry["reason"] = str(exc)
            finally:
                candidate.unlink(missing_ok=True)
            reports.append(entry)
    finally:
        unload = getattr(engine.backend, "unload", None)
        if unload is not None:
            unload()
    report = directory / "report.json"
    report.write_text(json.dumps({"schema_version": 1, "mode": "repair",
                                 "policy": "verified whole-clip linear rerender",
                                 "clips": reports}, indent=2) + "\n", encoding="utf-8")
    return replacements, maps, (*artifacts, report)
