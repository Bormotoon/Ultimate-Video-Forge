import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

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
