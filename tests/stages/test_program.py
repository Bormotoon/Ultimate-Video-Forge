import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from studio.core.project import Asset, AssetRole, Project
from studio.core.timeline import EditMap, KeepRange, SourcePlacement, TimeDomain
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.program import map_transcript_to_edited, render_project_program
from studio.stages.program_encoder import EncoderChoice


def test_program_transcript_uses_edited_time_and_drops_cut_words() -> None:
    transcript = Transcript(
        Path("audio.wav"),
        "en",
        10,
        [
            Segment(
                0,
                9,
                (
                    Word("keep", 1, 2),
                    Word("cut", 4, 5),
                    Word("again", 7, 8),
                ),
            )
        ],
        time_domain=TimeDomain.TIMELINE,
    )
    edit = EditMap("edit", (KeepRange(0, 3), KeepRange(6, 10)))
    result = map_transcript_to_edited(transcript, edit)
    assert result.time_domain is TimeDomain.EDITED
    assert [(word.text, word.start, word.end) for word in result.words] == [
        ("keep", 1, 2),
        ("again", 4, 5),
    ]
    assert result.duration == 7


def test_render_uses_placement_and_synced_audio_on_real_media(tmp_path: Path) -> None:
    camera = tmp_path / "camera.mp4"
    voice = tmp_path / "voice.wav"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        "color=c=red:s=160x120:r=25:d=3", "-c:v", "libx264", str(camera),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=3", str(voice),
    ], check=True)
    project = Project(tmp_path, tmp_path / "_studio")
    project.assets = [Asset("cam", Path("camera.mp4"), "video", AssetRole.CAMERA)]
    project.assets[0].manual["media_info"] = {"width":160, "height":120, "fps":25}
    project.placements = [SourcePlacement("cam", 10, 0, 3)]
    project.outputs["sync:cam"] = [voice]
    edit = EditMap("cut", (KeepRange(10.4, 11.2), KeepRange(12, 12.6)))
    output = tmp_path / "program.mp4"
    report = render_project_program(project, edit, output, encoder="cpu")
    assert report["final_codec"] == "libx264"
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)
    ], check=True, capture_output=True, text=True)
    data = json.loads(probe.stdout)
    assert {stream["codec_type"] for stream in data["streams"]} == {"audio", "video"}
    assert float(data["format"]["duration"]) == pytest.approx(1.4, abs=0.08)
    assert next(stream for stream in data["streams"] if stream["codec_type"] == "video")[
        "width"
    ] == 160


def test_runtime_gpu_failure_rerenders_all_parts_on_cpu(tmp_path: Path) -> None:
    project = Project(tmp_path, tmp_path / "work")
    project.assets = [Asset("cam", Path("camera.mp4"), "video", AssetRole.CAMERA)]
    project.assets[0].manual["media_info"] = {"width": 160, "height": 120, "fps": 25}
    project.placements = [SourcePlacement("cam", 0, 0, 3)]
    edit = EditMap("cut", (KeepRange(0, 1), KeepRange(2, 3)))
    commands = []

    def run(command):
        commands.append(command.copy())
        if len(commands) == 2:
            raise RuntimeError("GPU disappeared")

    with patch("studio.stages.program.select_encoder", side_effect=[
        EncoderChoice("auto", "h264_nvenc", "probe passed"),
        EncoderChoice("cpu", "libx264", "CPU requested"),
    ]), patch("studio.stages.program._run_ffmpeg", side_effect=run):
        report = render_project_program(project, edit, tmp_path / "out.mp4")
    assert report["requested_encoder"] == "auto"
    assert report["initial_codec"] == "h264_nvenc"
    assert report["final_codec"] == "libx264"
    assert report["runtime_fallback_reason"] == "GPU disappeared"
    assert [cmd[cmd.index("-c:v") + 1] for cmd in commands] == [
        "h264_nvenc", "h264_nvenc", "libx264", "libx264", "copy",
    ]
