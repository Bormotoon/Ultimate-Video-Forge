import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from studio.core.project import Asset, AssetRole, Project
from studio.core.timeline import SourcePlacement
from studio.stages.export import (
    Sequence,
    SequenceClip,
    SequenceMarker,
    build_sequence,
    fcpxml_intervals,
    write_fcpxml,
    write_xmeml,
    xmeml_intervals,
)


def test_fcpxml_round_trip_preserves_sequence_intervals(tmp_path: Path) -> None:
    source = tmp_path / "camera.mov"
    source.write_bytes(b"placeholder")
    sequence = Sequence(
        "episode",
        Fraction(25, 1),
        1920,
        1080,
        (
            SequenceClip("a", source, 2.0, 5.0, 1.0, 0, True),
            SequenceClip("b", source, 0.0, 3.0, 7.0, 1, True),
        ),
    )
    output = write_fcpxml(sequence, tmp_path / "episode.fcpxml", media_base=tmp_path)
    assert fcpxml_intervals(output) == [("camera", 1.0, 5.0), ("camera", 7.0, 3.0)]
    assert "camera.mov" in output.read_text(encoding="utf-8")


def test_xmeml_round_trip_preserves_sequence_intervals(tmp_path: Path) -> None:
    source = tmp_path / "camera clip.mov"
    source.write_bytes(b"placeholder")
    sequence = Sequence(
        "episode",
        Fraction(25, 1),
        1920,
        1080,
        (SequenceClip("a", source, 2.0, 5.0, 1.0, 0, True),),
    )
    output = write_xmeml(sequence, tmp_path / "episode.xml")
    assert xmeml_intervals(output) == [("camera clip", 1.0, 5.0)]
    text = output.read_text(encoding="utf-8")
    assert "<!DOCTYPE xmeml>" in text
    assert "file://localhost/" in text


def test_synced_voice_is_connected_and_original_audio_is_muted(tmp_path: Path) -> None:
    voice = tmp_path / "voice.wav"
    import wave

    with wave.open(str(voice), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "PCM"))
        audio.writeframes(b"\x00\x00" * 48000 * 5)
    project = Project(tmp_path, tmp_path / "_studio")
    project.assets = [Asset("cam", Path("camera.mov"), "video", AssetRole.CAMERA)]
    project.placements = [SourcePlacement("cam", 1.0, 0.0, 5.0)]
    project.assets[0].manual["media_info"] = {"audio_codec": "aac"}
    project.outputs["sync:cam"] = [voice]
    sequence = build_sequence(project)
    assert len(sequence.clips) == 2
    output = write_fcpxml(sequence, tmp_path / "out.fcpxml", media_base=tmp_path)
    root = ET.parse(output).getroot()
    assert root.find(".//spine/gap") is not None
    assert root.find(".//adjust-volume").get("amount") == "-96dB"
    assert any(asset.get("hasVideo") == "0" for asset in root.findall(".//asset"))
    xml = write_xmeml(sequence, tmp_path / "out.xml")
    assert ET.parse(xml).find(".//audio/track/clipitem/sourcetrack/mediatype").text == "audio"
    root = ET.parse(xml)
    camera_audio = [item for item in root.findall(".//audio/track/clipitem")
                    if item.findtext("name") == "camera"]
    assert len(camera_audio) == 2
    assert all(item.findtext("enabled") == "FALSE" for item in camera_audio)
    assert [item.findtext("sourcetrack/trackindex") for item in camera_audio] == ["1", "2"]
    assert all(item.find("link/linkclipref") is not None for item in camera_audio)


def test_edit_is_applied_identically_to_video_and_synced_voice(tmp_path: Path) -> None:
    voice = tmp_path / "voice.wav"
    import wave

    with wave.open(str(voice), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "PCM"))
        audio.writeframes(b"\x00\x00" * 48000 * 5)
    edit = tmp_path / "edit.json"
    edit.write_text(
        '{"mode":"cut","keep":[{"start":1,"end":3},{"start":5,"end":6}]}'
    )
    project = Project(tmp_path, tmp_path / "_studio")
    project.assets = [Asset("cam", Path("camera.mov"), "video", AssetRole.CAMERA)]
    project.placements = [SourcePlacement("cam", 1.0, 0.0, 5.0)]
    project.outputs = {"sync:cam": [voice], "roughcut": [edit]}
    sequence = build_sequence(project)
    videos = [clip for clip in sequence.clips if clip.has_video]
    voices = [clip for clip in sequence.clips if not clip.has_video]
    assert [(clip.source_in_s, clip.timeline_start_s, clip.duration_s) for clip in videos] == [
        (0.0, 0.0, 2.0), (4.0, 2.0, 1.0),
    ]
    assert [(clip.source_in_s, clip.timeline_start_s, clip.duration_s) for clip in voices] == [
        (0.0, 0.0, 2.0), (4.0, 2.0, 1.0),
    ]
    output = write_fcpxml(sequence, tmp_path / "out.fcpxml", media_base=tmp_path)
    assert len(ET.parse(output).findall(".//resources/asset")) == 2


def test_cut_and_filler_markers_use_edited_time_in_both_formats(tmp_path: Path) -> None:
    edit = tmp_path / "edit.json"
    edit.write_text(
        '{"mode":"cut","keep":[{"start":0,"end":2},{"start":4,"end":6}],'
        '"cuts":[{"start":2,"end":4,"reason":"pause","note":"quiet pause"}],'
        '"markers":[{"at":5,"kind":"filler","text":"um"}]}'
    )
    project = Project(tmp_path, tmp_path / "_studio")
    project.assets = [Asset("cam", Path("camera.mov"), "video", AssetRole.CAMERA)]
    project.placements = [SourcePlacement("cam", 0, 0, 6)]
    project.outputs["roughcut"] = [edit]
    sequence = build_sequence(project)
    assert sequence.markers == (
        SequenceMarker(2, "quiet pause", "pause"), SequenceMarker(3, "um", "filler"),
    )
    fcpxml = write_fcpxml(sequence, tmp_path / "out.fcpxml", media_base=tmp_path)
    assert [marker.get("start") for marker in ET.parse(fcpxml).findall(".//marker")] == [
        "50/25s", "75/25s",
    ]
    xml = write_xmeml(sequence, tmp_path / "out.xml")
    assert [marker.findtext("in") for marker in ET.parse(xml).findall(".//marker")] == [
        "50", "75",
    ]
    uncut = build_sequence(project, use_edit=False)
    assert not uncut.markers
    assert len(uncut.clips) == 1
    assert uncut.clips[0].duration_s == 6
