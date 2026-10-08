"""Neutral edit sequence and Final Cut FCPXML export."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

from studio.core.project import AssetRole, Project, describe_artifact, stable_fingerprint
from studio.core.timeline import EditMap, KeepRange, timeline_to_edited
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
    has_video: bool = True
    audio_enabled: bool = True
    audio_channels: int = 2


@dataclass(frozen=True, slots=True)
class SequenceMarker:
    at_s: float
    text: str
    kind: str


@dataclass(frozen=True, slots=True)
class Sequence:
    name: str
    fps: Fraction
    width: int
    height: int
    clips: tuple[SequenceClip, ...]
    markers: tuple[SequenceMarker, ...] = ()


class ExportStage:
    id = "export"
    title = "NLE export"
    after: tuple[str, ...] = ("timeline", "roughcut")
    optional_after = ("roughcut",)
    gpu = GpuUse.NONE

    def requirements(self, settings: dict[str, object]) -> list[Requirement]:
        return []

    def decide(self, project: Project, settings: dict[str, object]) -> Decision:
        return Decision.run() if any(
            asset.role is AssetRole.CAMERA for asset in project.assets
        ) else Decision.skip("no camera assets")

    def fingerprint(self, project: Project, settings: dict[str, object]) -> str:
        inputs = [
            describe_artifact(project.work_dir, path)
            for key, paths in project.outputs.items()
            if key.startswith("sync:") or key == "roughcut"
            for path in paths if path.is_file()
        ]
        return stable_fingerprint(
            "export-v3", project.assets, project.placements, project.audio_warp_maps,
            inputs, settings.get("export", {}), settings.get("roughcut", {}),
        )

    def run(self, context: StageContext) -> StageOutput:
        roughcut = context.settings.get("roughcut", {})
        use_edit = roughcut.get("enabled", True) if isinstance(roughcut, dict) else True
        sequence = build_sequence(context.project, use_edit=bool(use_edit))
        fcpxml = context.work_dir / "export" / f"{sequence.name}.fcpxml"
        xmeml = context.work_dir / "export" / f"{sequence.name}.xml"
        conf = context.settings.get("export", {})
        conf = conf if isinstance(conf, dict) else {}
        artifacts = []
        targets = conf.get("targets", ["fcpxml", "xmeml"])
        if "fcpxml" in targets:
            write_fcpxml(
                sequence, fcpxml, media_base=context.project.source_dir,
                version=str(conf.get("fcpxml_version", "1.9")),
            )
            artifacts.append(fcpxml)
        if "xmeml" in targets:
            write_xmeml(sequence, xmeml)
            artifacts.append(xmeml)
        outputs = {**context.project.outputs, "export": artifacts}
        return StageOutput(tuple(artifacts), {"outputs": outputs})


def build_sequence(project: Project, name: str = "Studio", *, use_edit: bool = True) -> Sequence:
    assets = {asset.id: asset for asset in project.assets}
    clips: list[SequenceClip] = []
    camera_index = 0
    for placement in project.placements:
        asset = assets.get(placement.asset_id)
        if asset is None or asset.role is not AssetRole.CAMERA:
            continue
        media = asset.manual.get("media_info", {})
        voices = project.outputs.get(f"sync:{asset.id}", [])
        voice = next((path for path in voices if path.is_file()), None)
        clips.append(
            SequenceClip(
                asset.id,
                project.source_dir / asset.path,
                placement.in_s,
                placement.duration_s,
                placement.offset_s,
                camera_index,
                bool(media.get("audio_codec")),
                audio_enabled=voice is None,
                audio_channels=int(media.get("audio_channels") or 2),
            )
        )
        if voice is not None:
            clips.append(SequenceClip(
                f"voice-{asset.id}", voice, placement.in_s, placement.duration_s,
                placement.offset_s, -camera_index - 1, True, has_video=False,
            ))
        camera_index += 1
    if not clips:
        raise ValueError("cannot build a sequence without placed camera clips")
    first_asset = assets[clips[0].asset_id]
    media = first_asset.manual.get("media_info", {})
    fps = Fraction(str(media.get("fps") or "25"))
    edit_paths = project.outputs.get("roughcut", [])
    markers: list[SequenceMarker] = []
    if use_edit and edit_paths and edit_paths[0].is_file():
        data = json.loads(edit_paths[0].read_text(encoding="utf-8"))
        edit = None
        if data.get("mode") == "cut":
            edit = EditMap("roughcut", tuple(
                KeepRange(float(item["start"]), float(item["end"]), item.get("camera_id"))
                for item in data.get("keep", [])
            ))
            edited_clips = []
            for clip in clips:
                for keep in edit.keep:
                    start = max(clip.timeline_start_s, keep.start_s)
                    end = min(clip.timeline_start_s + clip.duration_s, keep.end_s)
                    if end <= start:
                        continue
                    edited_start = timeline_to_edited(start, edit)
                    assert edited_start is not None
                    edited_clips.append(replace(
                        clip, source_in_s=clip.source_in_s + start - clip.timeline_start_s,
                        timeline_start_s=edited_start, duration_s=end - start,
                    ))
            clips = edited_clips
            if not clips:
                raise ValueError("edit removes all placed camera clips")
        for marker in data.get("markers", []):
            original = float(marker["at"])
            at = timeline_to_edited(original, edit) if edit is not None else original
            if at is not None:
                markers.append(SequenceMarker(at, marker["text"], marker["kind"]))
        for cut in data.get("cuts", []):
            if not cut.get("accepted", True):
                continue
            start, end = float(cut["start"]), float(cut["end"])
            # A removed span has no inverse time. Its marker belongs to the
            # splice, after all retained material preceding the removed span.
            at = (sum(max(0.0, min(start, keep.end_s) - keep.start_s)
                      for keep in edit.keep) if edit is not None else start)
            markers.append(SequenceMarker(at, cut["note"], cut["reason"]))
            if end < start:
                raise ValueError("cut end precedes start")
    return Sequence(
        name,
        fps,
        int(media.get("width") or 1920),
        int(media.get("height") or 1080),
        tuple(clips),
        tuple(sorted(markers, key=lambda marker: marker.at_s)),
    )


def write_fcpxml(
    sequence: Sequence, output: Path, *, media_base: Path, version: str = "1.9",
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("fcpxml", version=version)
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
        if clip.asset_id in resource_ids:
            continue
        resource_id = f"r{index}"
        resource_ids[clip.asset_id] = resource_id
        asset_element = ET.SubElement(
            resources,
            "asset",
            id=resource_id,
            name=clip.path.stem,
            start="0s",
            duration=_frame_time(max(
                item.source_in_s + item.duration_s for item in sequence.clips
                if item.asset_id == clip.asset_id
            ), sequence.fps),
            hasVideo="1" if clip.has_video else "0",
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
    bed = ET.SubElement(spine, "gap", name="Timeline", offset="0s", start="0s",
                        duration=_frame_time(duration, sequence.fps))
    for clip in sequence.clips:
        element = ET.SubElement(
            bed,
            "asset-clip",
            ref=resource_ids[clip.asset_id],
            name=clip.path.stem,
            offset=_frame_time(clip.timeline_start_s, sequence.fps),
            start=_frame_time(clip.source_in_s, sequence.fps),
            duration=_frame_time(clip.duration_s, sequence.fps),
            lane=str(clip.lane + 1) if clip.has_video else str(clip.lane),
        )
        if clip.has_audio and not clip.audio_enabled:
            ET.SubElement(element, "adjust-volume", amount="-96dB")
    for marker in sequence.markers:
        ET.SubElement(
            bed, "marker", start=_frame_time(marker.at_s, sequence.fps),
            duration=_frame_time(1 / float(sequence.fps), sequence.fps),
            value=marker.text, note=marker.kind,
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
        for element in root.findall(".//spine//asset-clip")
    ]


def write_xmeml(sequence: Sequence, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("xmeml", version="4")
    sequence_el = ET.SubElement(root, "sequence", id="sequence-1")
    ET.SubElement(sequence_el, "name").text = sequence.name
    _xmeml_rate(sequence_el, sequence.fps)
    duration = max(
        _frames(clip.timeline_start_s + clip.duration_s, sequence.fps)
        for clip in sequence.clips
    )
    ET.SubElement(sequence_el, "duration").text = str(duration)
    for marker in sequence.markers:
        element = ET.SubElement(sequence_el, "marker")
        ET.SubElement(element, "name").text = marker.text
        ET.SubElement(element, "comment").text = marker.kind
        ET.SubElement(element, "in").text = str(_frames(marker.at_s, sequence.fps))
        ET.SubElement(element, "out").text = "-1"
    media = ET.SubElement(sequence_el, "media")
    video = ET.SubElement(media, "video")
    audio = ET.SubElement(media, "audio")
    characteristics = ET.SubElement(ET.SubElement(video, "format"), "samplecharacteristics")
    _xmeml_rate(characteristics, sequence.fps)
    ET.SubElement(characteristics, "width").text = str(sequence.width)
    ET.SubElement(characteristics, "height").text = str(sequence.height)
    ET.SubElement(characteristics, "pixelaspectratio").text = "square"
    ET.SubElement(characteristics, "fielddominance").text = "none"
    for lane in sorted({clip.lane for clip in sequence.clips}):
        lane_clips = [clip for clip in sequence.clips if clip.lane == lane]
        track = ET.SubElement(video if lane_clips[0].has_video else audio, "track")
        for index, clip in enumerate(
            (item for item in sequence.clips if item.lane == lane), 1
        ):
            item = ET.SubElement(track, "clipitem", id=f"v-{lane}-{index}")
            ET.SubElement(item, "name").text = clip.path.stem
            ET.SubElement(item, "enabled").text = "TRUE"
            ET.SubElement(item, "start").text = str(
                _frames(clip.timeline_start_s, sequence.fps)
            )
            ET.SubElement(item, "end").text = str(
                _frames(clip.timeline_start_s + clip.duration_s, sequence.fps)
            )
            ET.SubElement(item, "in").text = str(
                _frames(clip.source_in_s, sequence.fps)
            )
            ET.SubElement(item, "out").text = str(
                _frames(clip.source_in_s + clip.duration_s, sequence.fps)
            )
            file_el = ET.SubElement(item, "file", id=f"file-{clip.asset_id}")
            ET.SubElement(file_el, "name").text = clip.path.name
            ET.SubElement(file_el, "pathurl").text = _xmeml_url(clip.path)
            _xmeml_rate(file_el, sequence.fps)
            file_media = ET.SubElement(file_el, "media")
            if clip.has_video:
                sample = ET.SubElement(ET.SubElement(file_media, "video"), "samplecharacteristics")
                _xmeml_rate(sample, sequence.fps)
                ET.SubElement(sample, "width").text = str(sequence.width)
                ET.SubElement(sample, "height").text = str(sequence.height)
            if clip.has_audio:
                file_audio = ET.SubElement(file_media, "audio")
                ET.SubElement(file_audio, "channelcount").text = str(clip.audio_channels)
                sample = ET.SubElement(file_audio, "samplecharacteristics")
                ET.SubElement(sample, "depth").text = "16"
                ET.SubElement(sample, "samplerate").text = "48000"
            if not clip.has_video:
                source_track = ET.SubElement(item, "sourcetrack")
                ET.SubElement(source_track, "mediatype").text = "audio"
                ET.SubElement(source_track, "trackindex").text = "1"
            elif clip.has_audio:
                # Preserve camera sound as separate, linked channel clipitems;
                # original channels remain available even when voice replaces them.
                for channel in range(1, clip.audio_channels + 1):
                    audio_track = ET.SubElement(audio, "track")
                    audio_item = ET.SubElement(
                        audio_track, "clipitem", id=f"a-{lane}-{index}-{channel}",
                    )
                    for tag in ("name", "start", "end", "in", "out"):
                        ET.SubElement(audio_item, tag).text = item.findtext(tag)
                    ET.SubElement(audio_item, "enabled").text = (
                        "TRUE" if clip.audio_enabled else "FALSE"
                    )
                    ET.SubElement(audio_item, "file", id=f"file-{clip.asset_id}")
                    source_track = ET.SubElement(audio_item, "sourcetrack")
                    ET.SubElement(source_track, "mediatype").text = "audio"
                    ET.SubElement(source_track, "trackindex").text = str(channel)
                    for owner in (item, audio_item):
                        for linked in (item, audio_item):
                            link = ET.SubElement(owner, "link")
                            ET.SubElement(link, "linkclipref").text = linked.get("id")
    ET.indent(root)
    tree = ET.ElementTree(root)
    with output.open("wb") as handle:
        handle.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n')
        tree.write(handle, encoding="utf-8", xml_declaration=False)
    return output


def xmeml_intervals(path: Path) -> list[tuple[str, float, float]]:
    root = ET.parse(path).getroot()
    sequence = root.find("sequence")
    if sequence is None:
        raise ValueError("xmeml has no sequence")
    fps = _xmeml_fps(sequence)
    intervals: list[tuple[str, float, float]] = []
    for item in root.findall(".//video/track/clipitem"):
        start = int(item.findtext("start", "0")) / float(fps)
        end = int(item.findtext("end", "0")) / float(fps)
        intervals.append((item.findtext("name", ""), start, end - start))
    return intervals


def _media_src(path: Path, base: Path) -> str:
    resolved = path.resolve()
    try:
        return quote(str(resolved.relative_to(base.resolve())))
    except ValueError:
        return resolved.as_uri()


def _frame_time(seconds: float, fps: Fraction) -> str:
    frames = _frames(seconds, fps)
    return f"{frames * fps.denominator}/{fps.numerator}s"


def _rational(seconds: float, denominator: int) -> str:
    value = Fraction(seconds).limit_denominator(denominator)
    return f"{value.numerator}/{value.denominator}s"


def _parse_time(value: str) -> float:
    raw = value.removesuffix("s")
    return float(Fraction(raw))


def _frames(seconds: float, fps: Fraction) -> int:
    return round(seconds * float(fps))


def _xmeml_rate(parent: ET.Element, fps: Fraction) -> None:
    rate = ET.SubElement(parent, "rate")
    ntsc = fps.denominator != 1
    timebase = round(float(fps)) if ntsc else fps.numerator
    ET.SubElement(rate, "timebase").text = str(timebase)
    ET.SubElement(rate, "ntsc").text = "TRUE" if ntsc else "FALSE"


def _xmeml_fps(sequence: ET.Element) -> Fraction:
    timebase = int(sequence.findtext("rate/timebase", "25"))
    if sequence.findtext("rate/ntsc") == "TRUE":
        return Fraction(timebase * 1000, 1001)
    return Fraction(timebase, 1)


def _xmeml_url(path: Path) -> str:
    uri = path.resolve().as_uri()
    return "file://localhost/" + uri.removeprefix("file:///")
