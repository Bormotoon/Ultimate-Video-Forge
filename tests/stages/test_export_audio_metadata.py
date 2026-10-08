import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from studio.core.project import Asset, AssetRole, Project
from studio.core.timeline import SourcePlacement
from studio.stages.export import build_sequence, write_fcpxml, write_xmeml


def test_rendered_wav_format_and_roles_survive_export(tmp_path: Path) -> None:
    voice, room = tmp_path / "voice.wav", tmp_path / "room.wav"
    for path, channels, rate, codec in [(voice, 1, 44100, "pcm_s24le"),
                                       (room, 2, 48000, "pcm_s16le")]:
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=1", "-ac", str(channels), "-ar", str(rate),
                        "-c:a", codec, str(path)], check=True)
    project = Project(tmp_path, tmp_path / "work")
    camera = Asset("cam", Path("cam.mov"), "video", AssetRole.CAMERA)
    camera.manual["media_info"] = {"fps": "25", "audio_channels": 4, "audio_codec": "aac"}
    project.assets = [camera]
    project.placements = [SourcePlacement("cam", 0, 0, 1)]
    project.outputs = {"sync:cam": [voice], "ambience:cam": [room]}
    sequence = build_sequence(project, retimed=True)
    voice_clip = next(clip for clip in sequence.clips if clip.asset_id == "voice-cam")
    assert (voice_clip.audio_channels, voice_clip.audio_sample_rate, voice_clip.audio_depth) == (
        1, 44100, 24,
    )
    xml = write_fcpxml(sequence, tmp_path / "out.fcpxml", media_base=tmp_path)
    root = ET.parse(xml).getroot()
    asset = next(asset for asset in root.findall("resources/asset") if asset.get("name") == "voice")
    assert (asset.get("audioChannels"), asset.get("audioRate")) == ("1", "44100")
    roles = {clip.get("name"): clip.get("audioRole")
             for clip in root.findall(".//spine/gap/asset-clip")}
    assert roles["voice"] == "dialogue"
    assert roles["room"] == "effects"
    xmeml = write_xmeml(sequence, tmp_path / "out.xml")
    root = ET.parse(xmeml).getroot()
    item = next(item for item in root.findall(".//clipitem") if item.findtext("name") == "voice")
    assert item.findtext("file/media/audio/channelcount") == "1"
    assert item.findtext("file/media/audio/samplecharacteristics/depth") == "24"
    assert item.findtext("file/media/audio/samplecharacteristics/samplerate") == "44100"
