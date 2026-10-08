import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from studio.core.timeline import EditMap, KeepRange
from studio.stages.export import Sequence, SequenceClip, _parse_time
from studio.stages.multicam import write_multicam


def test_multicam_resources_angles_selection_and_gap(tmp_path: Path) -> None:
    sequence = Sequence("Demo", Fraction(25), 1920, 1080, (
        SequenceClip("a", tmp_path / "a.mov", 3, 2, 0, 0, True, audio_enabled=False),
        SequenceClip("voice-a", tmp_path / "a.wav", 3, 2, 0, -1, True, has_video=False),
        SequenceClip("b", tmp_path / "b.mov", 0, 2, 1, 1, True),
        SequenceClip("b", tmp_path / "b.mov", 4, 1, 4, 1, True),
    ))
    path = write_multicam(sequence, tmp_path / "out.fcpxml", media_base=tmp_path)
    root = ET.parse(path).getroot()
    angles = root.findall("./resources/media/multicam/mc-angle")
    assert len(angles) == 3
    assert len(angles[-1].findall("asset-clip")) == 2
    assert all("lane" not in clip.attrib for angle in angles for clip in angle)
    ids = {angle.get("angleID") for angle in angles}
    clips = root.findall(".//project/sequence/spine/mc-clip")
    assert [_parse_time(clip.get("offset")) for clip in clips] == [0, 1, 2, 4]
    assert [source.get("srcEnable") for source in clips[0]] == ["video", "audio"]
    assert all(source.get("angleID") in ids for clip in clips for source in clip)
    gap = root.find(".//project/sequence/spine/gap")
    assert _parse_time(gap.get("offset")) == 3
    assert _parse_time(gap.get("duration")) == 1
    refs = {asset.get("id") for asset in root.findall("./resources/asset")}
    assert all(clip.get("ref") in refs for angle in angles for clip in angle)


def test_multicam_selection_preserves_alternative_angles(tmp_path: Path) -> None:
    sequence = Sequence("Demo", Fraction(25), 1920, 1080, (
        SequenceClip("a", tmp_path / "a.mov", 0, 4, 0, 0, True),
        SequenceClip("b", tmp_path / "b.mov", 0, 4, 0, 1, True),
    ))
    path = write_multicam(sequence, tmp_path / "selected.fcpxml", media_base=tmp_path,
                          camera_plan=EditMap("plan", (KeepRange(0, 2, "a"),
                                                       KeepRange(2, 4, "b"))))
    root = ET.parse(path).getroot()
    assert len(root.findall("./resources/media/multicam/mc-angle")) == 2
    clips = root.findall(".//project/sequence/spine/mc-clip")
    assert [clip.find("mc-source").get("angleID") for clip in clips] == ["angle-1", "angle-2"]


def test_speaker_plan_is_mapped_across_removed_ranges(tmp_path: Path) -> None:
    import json

    from studio.core.project import Asset, AssetRole, Project
    from studio.core.timeline import SourcePlacement
    from studio.stages.export import multicam_camera_plan

    project = Project(tmp_path, tmp_path / "work")
    project.assets = [Asset("a", Path("a.mov"), "video", AssetRole.CAMERA),
                      Asset("b", Path("b.mov"), "video", AssetRole.CAMERA)]
    project.placements = [SourcePlacement("a", 0, 0, 10), SourcePlacement("b", 0, 0, 10)]
    speakers = tmp_path / "speakers.json"
    speakers.write_text(json.dumps([{"start": 0, "end": 5, "speaker": "Alice"},
                                    {"start": 5, "end": 10, "speaker": "Bob"}]))
    edit = tmp_path / "edit.json"
    edit.write_text(json.dumps({"mode": "cut", "keep": [{"start": 0, "end": 2},
                                                         {"start": 6, "end": 8}]}))
    project.outputs = {"roughcut": [edit], "speakers": [speakers]}
    sequence = Sequence("Demo", Fraction(25), 1920, 1080, ())
    plan = multicam_camera_plan(project, {"program": {"speaker_cameras": {
        "Alice": "a", "Bob": "b"}, "min_shot_s": 0}}, True, sequence)
    assert plan.keep == (KeepRange(0, 2, "a"), KeepRange(2, 4, "b"))


def test_ambience_is_connected_inside_selected_audio_angle(tmp_path: Path) -> None:
    sequence = Sequence("Room", Fraction(25), 1920, 1080, (
        SequenceClip("a", tmp_path / "a.mov", 6, 2, 0, 0, True, audio_enabled=False),
        SequenceClip("voice-a", tmp_path / "voice.wav", 6, 2, 0, -1, True, has_video=False),
        SequenceClip("ambience-a", tmp_path / "room.wav", 6, 2, 0, -1000, True,
                     has_video=False),
    ))
    path = write_multicam(sequence, tmp_path / "room.fcpxml", media_base=tmp_path)
    root = ET.parse(path).getroot()
    angles = root.findall("./resources/media/multicam/mc-angle")
    assert len(angles) == 2
    composite = angles[1].find("clip")
    assert composite.get("start") == "0s"
    children = composite.findall("asset-clip")
    assert [_parse_time(child.get("start")) for child in children] == [6, 6]
    assert [_parse_time(child.get("offset")) for child in children] == [0, 0]
    assert children[1].get("lane") == "-1"
    sources = root.findall(".//project/sequence/spine/mc-clip/mc-source")
    assert [(source.get("angleID"), source.get("srcEnable")) for source in sources] == [
        ("angle-1", "video"), ("angle-2", "audio"),
    ]
