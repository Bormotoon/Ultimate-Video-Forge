import wave
from pathlib import Path

from studio.core.project import Asset, AssetKind, AssetRole, Project
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.runner import run_stage_process


def test_complex_worker_publishes_real_voice_and_warp(tmp_path: Path) -> None:
    work = tmp_path / "_studio"
    work.mkdir()
    source = tmp_path / "recorder.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x10\x00" * 48000 * 4)
    words = tuple(Word(f"token{i}", 0.2 + i * 0.1, 0.25 + i * 0.1) for i in range(25))
    transcripts = {}
    for name, duration in [("rec", 4.0), ("cam", 3.0)]:
        path = work / f"{name}.json"
        Transcript(source, "en", duration, [Segment(0.0, 3.0, words)]).save(path)
        transcripts[name] = path
    project_path = work / "project.json"
    Project(tmp_path, work, assets=[
        Asset("rec", Path("recorder.wav"), AssetKind.AUDIO, AssetRole.RECORDER),
        Asset("cam", Path("camera.mov"), AssetKind.VIDEO, AssetRole.CAMERA),
    ], transcripts=transcripts).save(project_path)
    settings = work / "settings.yaml"
    settings.write_text("sync:\n  mode: complex\n")
    result = run_stage_process("sync", project_path, settings, "complex-fp")
    project = Project.load(project_path)
    voice = project.outputs["sync:cam"][0]
    assert voice == work / "stages" / "sync" / "voice" / "cam.wav"
    assert voice.is_file()
    assert project.audio_warp_maps[0].strategy == 3
    assert [placement.asset_id for placement in project.placements] == ["rec", "cam"]
    assert result.manifest.reusable(work, "complex-fp")
    assert not list(work.glob(".settings-*"))
