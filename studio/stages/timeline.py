"""Build a timeline-domain transcript without another Whisper pass."""

from __future__ import annotations

from studio.core.project import AssetRole, Project, stable_fingerprint
from studio.core.timeline import SourcePlacement, TimeDomain, file_to_timeline
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.base import Decision, GpuUse, Requirement, StageContext, StageOutput


class TimelineStage:
    id = "timeline"
    title = "Timeline transcript"
    after: tuple[str, ...] = ("transcribe", "sync")
    optional_after = ("sync",)
    gpu = GpuUse.NONE

    def requirements(self, settings: dict[str, object]) -> list[Requirement]:
        return []

    def decide(self, project: Project, settings: dict[str, object]) -> Decision:
        source_id = _primary_source_id(project)
        if source_id is None:
            return Decision.skip("no transcript source is available")
        if source_id not in project.transcripts:
            return Decision.blocked("primary transcript is missing", "run transcription")
        return Decision.run({"source_asset_id": source_id})

    def fingerprint(self, project: Project, settings: dict[str, object]) -> str:
        source_id = _primary_source_id(project)
        transcript = project.transcripts.get(source_id or "")
        content = (
            transcript.read_text(encoding="utf-8")
            if transcript and transcript.is_file()
            else ""
        )
        placement = next(
            (item for item in project.placements if item.asset_id == source_id), None
        )
        return stable_fingerprint("timeline-v1", source_id, content, placement)

    def run(self, context: StageContext) -> StageOutput:
        source_id = _primary_source_id(context.project)
        if source_id is None:
            raise ValueError("no transcript source is available")
        source = Transcript.load(context.project.transcripts[source_id])
        placement = next(
            (item for item in context.project.placements if item.asset_id == source_id),
            _identity_placement(source_id, source.duration),
        )
        segments = [
            Segment(
                file_to_timeline(segment.start, placement),
                file_to_timeline(segment.end, placement),
                tuple(
                    Word(
                        word.text,
                        file_to_timeline(word.start, placement),
                        file_to_timeline(word.end, placement),
                        word.probability,
                    )
                    for word in segment.words
                ),
                segment.confidence,
                segment.avg_logprob,
                segment.text_override,
            )
            for segment in source.segments
        ]
        transcript = Transcript(
            source.source_audio,
            source.language,
            max((segment.end for segment in segments), default=0.0),
            segments,
            source.model,
            source.device,
            source.compute_type,
            source.mode,
            TimeDomain.TIMELINE,
            f"placement:{source_id}",
            metadata={**source.metadata, "source_asset_id": source_id},
        )
        output = context.work_dir / "stages" / "timeline" / "transcript.json"
        transcript.save(output)
        transcripts = {**context.project.transcripts, "timeline": output}
        return StageOutput((output,), {"transcripts": transcripts})


def _primary_source_id(project: Project) -> str | None:
    for role in (AssetRole.RECORDER, AssetRole.CAMERA):
        source = next((asset.id for asset in project.assets if asset.role is role), None)
        if source is not None:
            return source
    return None


def _identity_placement(asset_id: str, duration: float) -> SourcePlacement:
    return SourcePlacement(asset_id, 0.0, 0.0, max(duration, 1e-6), provenance="metadata")
