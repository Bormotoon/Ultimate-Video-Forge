import wave
from pathlib import Path

import pytest

from studio.core.project import Asset, AssetKind, AssetRole, Project
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.runner import run_stage_process


@pytest.mark.parametrize("recorder_count", [1, 2])
def test_complex_worker_publishes_real_voice_and_warp(tmp_path: Path, recorder_count: int) -> None:
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
    assets = [
        Asset("rec", Path("recorder.wav"), AssetKind.AUDIO, AssetRole.RECORDER),
        Asset("cam", Path("camera.mov"), AssetKind.VIDEO, AssetRole.CAMERA),
    ]
    if recorder_count == 2:
        assets.append(Asset("rec2", Path("recorder.wav"), AssetKind.AUDIO, AssetRole.RECORDER))
        transcripts["rec2"] = transcripts["rec"]
    Project(tmp_path, work, assets=assets, transcripts=transcripts).save(project_path)
    settings = work / "settings.yaml"
    settings.write_text("sync:\n  mode: complex\n  recorder_mode: all\n"
                        "  master_wav: true\n  voice_segment_minutes: 0.02\n")
    result = run_stage_process("sync", project_path, settings, "complex-fp")
    project = Project.load(project_path)
    voice = project.outputs["sync:cam"][0]
    assert voice == work / "stages" / "sync" / "voice" / "cam.wav"
    assert voice.is_file()
    assert project.audio_warp_maps[0].strategy == 3
    assert [placement.asset_id for placement in project.placements] == (
        ["rec", "cam"] if recorder_count == 1 else ["rec", "rec2", "cam"]
    )
    assert len(project.outputs["sync-tracks:cam"]) == recorder_count
    assert len(project.audio_warp_maps) == recorder_count
    assert project.outputs["master_wav"][0].is_file()
    assert len(project.outputs["voice-segments:cam"]) == 4
    assert result.manifest.reusable(work, "complex-fp")
    assert not list(work.glob(".settings-*"))
