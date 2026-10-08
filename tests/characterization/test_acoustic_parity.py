import wave
from pathlib import Path

import numpy as np
import pytest
from whispersync.engine.acoustic import gcc_phat as frozen_gcc

from studio.cli.main import main
from studio.core.project import Asset, AssetKind, AssetRole, Project
from studio.core.transcript import Transcript
from studio.stages.base import StageContext
from studio.stages.sync import SyncStage
from studio.stages.sync_acoustic import gcc_phat
from studio.stages.sync_alignment import align_sources
from studio.stages.sync_verify import measure


def test_gcc_matches_frozen_algorithm() -> None:
    random = np.random.default_rng(42)
    signal = random.normal(size=16000).astype(np.float32)
    delayed = np.concatenate((np.zeros(320), signal[:-320]))
    assert gcc_phat(signal, delayed, 16000, 0.1, 1e-8) == frozen_gcc(
        signal, delayed, 16000, 0.1, 1e-8,
    )


def test_acoustic_alignment_finds_offset_without_transcript(tmp_path: Path) -> None:
    random = np.random.default_rng(42)
    recording = random.normal(0, 0.1, 16000 * 12)

    def save(path: Path, samples: np.ndarray) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes((samples * 32767).astype("<i2").tobytes())

    recorder, camera = tmp_path / "rec.wav", tmp_path / "cam.wav"
    save(recorder, recording)
    save(camera, recording[16000 * 2:16000 * 10])
    fitted = align_sources(
        Transcript(camera, "", 8, []), Transcript(recorder, "", 12, []), camera, recorder,
        {"acoustic_grid_s": 2, "acoustic_window_s": 1}, acoustic_first=True,
    )
    assert fitted.provenance == "acoustic"
    assert fitted.offset == pytest.approx(-2, abs=0.01)
    assert fitted.k == pytest.approx(1)
    assert fitted.inliers >= 3
    verification = measure(camera, camera, grid_s=2, window_s=1)
    assert verification.verdict(20)[0] == "passed"
    assert main(["verify", str(camera), str(camera), "--grid-s", "2", "--window-s", "1"]) == 0
    project = Project(tmp_path, tmp_path / "_studio", assets=[
        Asset("primary", recorder.name, AssetKind.VIDEO, AssetRole.CAMERA),
        Asset("second", camera.name, AssetKind.VIDEO, AssetRole.CAMERA),
    ])
    project.assets[0].manual["media_info"] = {"duration_s": 12}
    project.assets[1].manual["media_info"] = {"duration_s": 8}
    output = SyncStage().run(StageContext(project, {
        "sync": {"mode": "camera", "acoustic_grid_s": 2, "acoustic_window_s": 1},
    }, tmp_path / "output"))
    placements = output.project_changes["placements"]
    assert placements[0].offset_s == 0
    assert placements[1].offset_s == pytest.approx(2, abs=0.01)
    assert not output.project_changes["audio_warp_maps"]
