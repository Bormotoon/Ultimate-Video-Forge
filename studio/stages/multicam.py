"""FCPXML multicam resources from an aligned, unretimed sequence."""

import xml.etree.ElementTree as ET
from pathlib import Path

from studio.stages.export import Sequence, _frame_time, write_fcpxml


def write_multicam(sequence: Sequence, output: Path, *, media_base: Path,
                  version: str = "1.9") -> Path:
    videos = [clip for clip in sequence.clips if clip.has_video]
    if not videos:
        raise ValueError("multicam requires video angles")
    write_fcpxml(sequence, output, media_base=media_base, version=version)
    tree = ET.parse(output)
    root = tree.getroot()
    resources = root.find("resources")
    sequence_el = root.find(".//project/sequence")
    spine = sequence_el.find("spine")
    bed = spine.find("gap")
    media = ET.SubElement(resources, "media", id="mc-resource", name=sequence.name + " Multicam")
    multicam = ET.SubElement(media, "multicam", format="r1", tcStart="0s", tcFormat="NDF")
    angles = {}
    for clip, element in zip(sequence.clips, list(bed.findall("asset-clip")), strict=True):
        key = (clip.has_video, clip.lane)
        if key not in angles:
            angle_id = f"angle-{len(angles) + 1}"
            angles[key] = ET.SubElement(multicam, "mc-angle", name=clip.path.stem,
                                       angleID=angle_id)
        element.attrib.pop("lane", None)
        angles[key].append(element)
    # Partition coverage so the project never selects an unavailable angle.
    boundaries = sorted({0.0} | {value for clip in sequence.clips
                                 for value in (clip.timeline_start_s,
                                               clip.timeline_start_s + clip.duration_s)})
    spine.remove(bed)
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        if end <= start:
            continue
        available = [clip for clip in videos if clip.timeline_start_s <= start + 1e-9
                     and clip.timeline_start_s + clip.duration_s >= end - 1e-9]
        if not available:
            ET.SubElement(spine, "gap", offset=_frame_time(start, sequence.fps), start="0s",
                          duration=_frame_time(end - start, sequence.fps))
            continue
        selected = available[0]
        element = ET.SubElement(spine, "mc-clip", ref="mc-resource", name=sequence.name,
                                offset=_frame_time(start, sequence.fps),
                                start=_frame_time(start, sequence.fps),
                                duration=_frame_time(end - start, sequence.fps))
        voices = [clip for clip in sequence.clips if not clip.has_video
                  and clip.asset_id == f"voice-{selected.asset_id}"
                  and clip.timeline_start_s <= start + 1e-9
                  and clip.timeline_start_s + clip.duration_s >= end - 1e-9]
        video_angle = angles[(True, selected.lane)].get("angleID")
        ET.SubElement(element, "mc-source", angleID=video_angle,
                      srcEnable="video" if voices else "all")
        if voices:
            ET.SubElement(element, "mc-source",
                          angleID=angles[(False, voices[0].lane)].get("angleID"), srcEnable="audio")
    # Markers use multicam source time within the corresponding project clip.
    for marker in sequence.markers:
        from studio.stages.export import _parse_time

        for element in spine.findall("mc-clip"):
            start = _parse_time(element.get("start"))
            end = start + _parse_time(element.get("duration"))
            if start <= marker.at_s < end:
                ET.SubElement(element, "marker", start=_frame_time(marker.at_s, sequence.fps),
                              duration=_frame_time(1 / float(sequence.fps), sequence.fps),
                              value=marker.text, note=marker.kind)
                break
    ET.indent(root)
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return output
