"""Neutral edit sequence and Final Cut FCPXML export."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

from studio.core.project import AssetRole, Project, stable_fingerprint
from studio.stages.base import Decision, GpuUse, Requirement, StageContext, StageOutput


@dataclass(frozen=True, slots=True)
class SequenceClip:
    asset_id: str
    path: Path
    source_in_s: float
    duration_s: float
    timeline_start_s: float
    lane: int
    has_audio: bool


@dataclass(frozen=True, slots=True)
class Sequence:
    name: str
    fps: Fraction
    width: int
    height: int
    clips: tuple[SequenceClip, ...]


class ExportStage:
    id = "export"
    title = "NLE export"
    after: tuple[str, ...] = ("timeline",)
    gpu = GpuUse.NONE

    def requirements(self, settings: dict[str, object]) -> list[Requirement]:
        return []

    def decide(self, project: Project, settings: dict[str, object]) -> Decision:
        return Decision.run() if any(
            asset.role is AssetRole.CAMERA for asset in project.assets
        ) else Decision.skip("no camera assets")

    def fingerprint(self, project: Project, settings: dict[str, object]) -> str:
        return stable_fingerprint(
            "export-v1", project.placements, project.audio_warp_maps, settings.get("export", {})
        )

    def run(self, context: StageContext) -> StageOutput:
        sequence = build_sequence(context.project)
        output = context.work_dir / "export" / f"{sequence.name}.fcpxml"
        write_fcpxml(sequence, output, media_base=context.project.source_dir)
        outputs = {**context.project.outputs, "export": [output]}
        return StageOutput((output,), {"outputs": outputs})


def build_sequence(project: Project, name: str = "Studio") -> Sequence:
    assets = {asset.id: asset for asset in project.assets}
    clips: list[SequenceClip] = []
    camera_index = 0
    for placement in project.placements:
        asset = assets.get(placement.asset_id)
        if asset is None or asset.role is not AssetRole.CAMERA:
            continue
        media = asset.manual.get("media_info", {})
        clips.append(
            SequenceClip(
                asset.id,
                project.source_dir / asset.path,
                placement.in_s,
                placement.duration_s,
                placement.offset_s,
                camera_index,
                bool(media.get("audio_codec")),
            )
        )
        camera_index += 1
    if not clips:
        raise ValueError("cannot build a sequence without placed camera clips")
    first_asset = assets[clips[0].asset_id]
    media = first_asset.manual.get("media_info", {})
    fps = Fraction(str(media.get("fps") or "25"))
    return Sequence(
        name,
        fps,
        int(media.get("width") or 1920),
        int(media.get("height") or 1080),
        tuple(clips),
    )


def write_fcpxml(sequence: Sequence, output: Path, *, media_base: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("fcpxml", version="1.9")
    resources = ET.SubElement(root, "resources")
    frame = _rational(1 / float(sequence.fps), 1_000_000)
    ET.SubElement(
        resources,
        "format",
        id="r1",
        name="StudioFormat",
        frameDuration=frame,
        width=str(sequence.width),
        height=str(sequence.height),
    )
    resource_ids: dict[str, str] = {}
    for index, clip in enumerate(sequence.clips, 2):
        resource_id = f"r{index}"
        resource_ids[clip.asset_id] = resource_id
        asset_element = ET.SubElement(
            resources,
            "asset",
            id=resource_id,
            name=clip.path.stem,
            start="0s",
            duration=_frame_time(clip.source_in_s + clip.duration_s, sequence.fps),
            hasVideo="1",
            hasAudio="1" if clip.has_audio else "0",
        )
        asset_element.append(
            ET.Element(
                "media-rep", kind="original-media", src=_media_src(clip.path, media_base)
            )
        )
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="Studio")
    project = ET.SubElement(event, "project", name=sequence.name)
    duration = max(
        clip.timeline_start_s + clip.duration_s for clip in sequence.clips
    )
    sequence_el = ET.SubElement(
        project,
        "sequence",
        format="r1",
        duration=_frame_time(duration, sequence.fps),
        tcStart="0s",
        tcFormat="NDF",
    )
    spine = ET.SubElement(sequence_el, "spine")
    for clip in sequence.clips:
        ET.SubElement(
            spine,
            "asset-clip",
            ref=resource_ids[clip.asset_id],
            name=clip.path.stem,
            offset=_frame_time(clip.timeline_start_s, sequence.fps),
            start=_frame_time(clip.source_in_s, sequence.fps),
            duration=_frame_time(clip.duration_s, sequence.fps),
            lane=str(clip.lane) if clip.lane else "0",
        )
    ET.indent(root)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return output


def fcpxml_intervals(path: Path) -> list[tuple[str, float, float]]:
    root = ET.parse(path).getroot()
    return [
        (
            element.get("name", ""),
            _parse_time(element.get("offset", "0s")),
            _parse_time(element.get("duration", "0s")),
        )
        for element in root.findall(".//spine/asset-clip")
    ]


def _media_src(path: Path, base: Path) -> str:
    resolved = path.resolve()
    try:
        return quote(str(resolved.relative_to(base.resolve())))
    except ValueError:
        return resolved.as_uri()


def _frame_time(seconds: float, fps: Fraction) -> str:
    frames = round(seconds * float(fps))
    return f"{frames * fps.denominator}/{fps.numerator}s"


def _rational(seconds: float, denominator: int) -> str:
    value = Fraction(seconds).limit_denominator(denominator)
    return f"{value.numerator}/{value.denominator}s"


def _parse_time(value: str) -> float:
    raw = value.removesuffix("s")
    return float(Fraction(raw))
