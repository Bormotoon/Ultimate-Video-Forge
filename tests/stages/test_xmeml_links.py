import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from studio.stages.export import Sequence, SequenceClip, write_xmeml


def test_channel_tracks_reused_and_all_group_links_resolve(tmp_path: Path) -> None:
    clips = tuple(SequenceClip("cam", tmp_path / "cam.mov", start, 1, index, 0, True,
                               audio_channels=3, audio_enabled=False, rate=1.25)
                  for index, start in enumerate([2, 5, 8]))
    voice = SequenceClip("voice", tmp_path / "voice.wav", 0, 3, 0, -1, True, has_video=False)
    path = write_xmeml(Sequence("Links", Fraction(25), 1920, 1080, (*clips, voice)),
                       tmp_path / "out.xml")
    root = ET.parse(path).getroot()
    video = root.find("sequence/media/video")
    audio = root.find("sequence/media/audio")
    assert len(video.findall("track")) == 1
    assert len(audio.findall("track")) == 4  # three channels plus external voice
    locations = {}
    for kind, container in [("video", video), ("audio", audio)]:
        for track_index, track in enumerate(container.findall("track"), 1):
            for clip_index, item in enumerate(track.findall("clipitem"), 1):
                locations[(kind, track_index, clip_index)] = item.get("id")
    camera_items = [item for item in root.findall(".//clipitem")
                    if item.findtext("name") == "cam"]
    assert len(camera_items) == 12
    for item in camera_items:
        links = item.findall("link")
        assert len(links) == 4
        refs = {link.findtext("linkclipref") for link in links}
        assert item.get("id") in refs
        for link in links:
            key = (link.findtext("mediatype"), int(link.findtext("trackindex")),
                   int(link.findtext("clipindex")))
            assert locations[key] == link.findtext("linkclipref")
        assert len({link.findtext("groupindex") for link in links}) == 1
    for track in audio.findall("track"):
        items = track.findall("clipitem")
        if items[0].findtext("name") == "cam":
            assert len(items) == 3
            assert all(item.findtext("enabled") == "FALSE" for item in items)
