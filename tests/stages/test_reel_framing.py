import json
import subprocess
from pathlib import Path

import pytest

import studio.stages.reel_render as rendering
from studio.core.settings import SettingsError, load_settings
from studio.modules.manager import ModuleManager
from studio.stages.reel_render import reframe_video


@pytest.mark.parametrize("mode", ["crop", "fit"])
def test_real_portrait_framing_preserves_duration(tmp_path: Path, mode: str) -> None:
    source, output = tmp_path / "source.mp4", tmp_path / "portrait.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=25:d=1",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
    )
    reframe_video(source, output, {"framing": mode, "width": 180, "height": 320, "crop_x": 1.0})
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    assert (stream["width"], stream["height"]) == (180, 320)
    assert stream["sample_aspect_ratio"] == "1:1"
    assert abs(float(stream["duration"]) - 1) < 0.05


@pytest.mark.parametrize(
    "override", ["reels.width=181", "reels.crop_x=1.1", "reels.framing=tracking"]
)
def test_invalid_framing_settings(override: str) -> None:
    with pytest.raises(SettingsError):
        load_settings([], [override])


def test_tracking_dispatch_and_static_fallback(tmp_path: Path, monkeypatch) -> None:
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=25:d=1",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
    )
    settings = {"framing": "crop", "width": 90, "height": 160, "tracking": True}
    monkeypatch.setattr(rendering, "tracking_filter", lambda *args: None)
    assert reframe_video(source, output, settings)["tracking"] == "unavailable-static-fallback"
    monkeypatch.setattr(
        rendering, "tracking_filter", lambda *args: "crop=100:180:100:0,scale=90:160,setsar=1"
    )
    assert reframe_video(source, output, settings)["tracking"] == "applied"


def test_managed_tracking_subprocess_contract(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ModuleManager, "installed", lambda self, name: True)
    monkeypatch.setattr(ModuleManager, "interpreter", lambda self, name: Path("/managed/python"))
    monkeypatch.setattr(
        "studio.core.media.probe", lambda path: type("Info", (), {"duration_s": 2})()
    )

    def process(command, **kwargs):
        assert command[0] == "/managed/python"
        assert command[2] == "studio.vision.worker"
        request = json.loads(Path(command[3]).read_text())
        assert request["width"] == 90
        assert kwargs["env"]["PYTHONPATH"]
        Path(command[4]).write_text(json.dumps({"filter": "crop=90:160"}))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rendering, "run_logged", process)
    assert rendering.tracking_filter(tmp_path / "video.mp4", 90, 160, {}) == "crop=90:160,setsar=1"
