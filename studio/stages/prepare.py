"""Finalize the complete input set before compute planning."""

from __future__ import annotations

import json

from studio.core.project import AssetRole, Project, stable_fingerprint
from studio.stages.base import Decision, GpuUse, Requirement, StageContext, StageOutput


class PrepareStage:
    id = "prepare"
    title = "Prepare inputs"
    after: tuple[str, ...] = ("scan",)
    gpu = GpuUse.NONE

    def requirements(self, settings: dict[str, object]) -> list[Requirement]:
        return []

    def decide(self, project: Project, settings: dict[str, object]) -> Decision:
        return Decision.run() if project.assets else Decision.blocked(
            "scan found no media", "choose a folder containing supported media"
        )

    def fingerprint(self, project: Project, settings: dict[str, object]) -> str:
        inputs = [(asset.id, asset.role.value, asset.manual) for asset in project.assets]
        return stable_fingerprint("prepare-v1", inputs, settings)

    def run(self, context: StageContext) -> StageOutput:
        recorders = [
            asset.id
            for asset in context.project.assets
            if asset.role is AssetRole.RECORDER
        ]
        cameras = [asset.id for asset in context.project.assets if asset.role is AssetRole.CAMERA]
        mode = str(context.settings.get("sync", {}).get("mode", "auto"))
        transcripts = list(recorders)
        reasons: dict[str, str] = {
            asset_id: "primary recorder transcript" for asset_id in recorders
        }
        if not recorders or mode in {"auto", "complex"}:
            transcripts.extend(cameras)
            reasons.update(
                {asset_id: "camera transcript required for placement" for asset_id in cameras}
            )
        data = {
            "schema_version": 1,
            "plan_revision": context.project.plan_revision,
            "assets": cameras + recorders,
            "transcripts": list(dict.fromkeys(transcripts)),
            "audio_streams": {
                asset.id: asset.manual.get("media_info", {}).get("audio_stream_index")
                for asset in context.project.assets
                if asset.role in {AssetRole.CAMERA, AssetRole.RECORDER}
            },
            "modules": [],
            "reasons": reasons,
        }
        output = context.work_dir / "stages" / "prepare" / "requirements.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return StageOutput((output,))
