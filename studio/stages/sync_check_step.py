"""Re-recognize rendered voices and publish honest content-check outcomes."""

import json
from dataclasses import asdict
from pathlib import Path

from studio.core.transcript import Transcript
from studio.stages.sync_check_settings import SelfCheckSettings
from studio.stages.sync_self_check import check_rendered_clip
from studio.stages.transcribe import TranscribeSettings, WhisperEngine


def check_voices(
    references: dict[str, Transcript], voices: dict[str, Path], directory: Path,
    transcribe_settings: object,
) -> tuple[Path, ...]:
    engine = WhisperEngine(TranscribeSettings.from_mapping(transcribe_settings))
    reports = []
    artifacts = []
    directory.mkdir(parents=True, exist_ok=True)
    try:
        for asset_id, reference in references.items():
            rendered = engine.transcribe(voices[asset_id])
            transcript_path = directory / f"{asset_id}.json"
            rendered.save(transcript_path)
            artifacts.append(transcript_path)
            outcome = check_rendered_clip(
                rendered.words, reference.words, SelfCheckSettings(), reference.duration,
            )
            reports.append({"asset_id": asset_id, "time_domain": "rendered_audio",
                            **asdict(outcome)})
    finally:
        unload = getattr(engine.backend, "unload", None)
        if unload is not None:
            unload()
    report = directory / "report.json"
    report.write_text(json.dumps({"schema_version": 1, "mode": "warn", "clips": reports},
                                 indent=2) + "\n", encoding="utf-8")
    return (*artifacts, report)
