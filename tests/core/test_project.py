from pathlib import Path

from studio.core.project import (
    ArtifactStatus,
    Asset,
    AssetKind,
    AssetRole,
    Project,
    StageManifest,
    describe_artifact,
    stable_fingerprint,
)
from studio.core.timeline import AudioWarpMap, AudioWarpPiece, SourcePlacement, TimeDomain


def test_project_json_round_trip(tmp_path: Path) -> None:
    project = Project(
        source_dir=Path("."), work_dir=Path("_studio"),
        assets=[Asset("cam-a", Path("camera/a.mov"), AssetKind.VIDEO, AssetRole.CAMERA)],
        placements=[SourcePlacement("cam-a", 0, 0, 10)],
        audio_warp_maps=[
            AudioWarpMap(
                "warp", "rec", "cam-a", (AudioWarpPiece(0, 10, 0, 10, "copy"),),
                TimeDomain.FILE, 1,
            )
        ],
    )
    path = tmp_path / "project.json"
    project.save(path)
    assert Project.load(path) == project


def test_manifest_reuse_requires_ok_fingerprint_and_intact_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "result.json"
    artifact.write_text("complete", encoding="utf-8")
    fingerprint = stable_fingerprint({"setting": 1}, "input")
    manifest = StageManifest(
        stage="scan", fingerprint=fingerprint, inputs={"source": "."},
        artifacts=[describe_artifact(tmp_path, artifact)], status=ArtifactStatus.OK,
        producer_version="test",
    )
    assert manifest.reusable(tmp_path, fingerprint)
    assert not manifest.reusable(tmp_path, "changed")
    artifact.write_text("damaged", encoding="utf-8")
    assert not manifest.reusable(tmp_path, fingerprint)


def test_pending_manifest_is_never_reusable(tmp_path: Path) -> None:
    artifact = tmp_path / "result"
    artifact.write_text("value", encoding="utf-8")
    manifest = StageManifest(
        stage="sync", fingerprint="same", inputs={},
        artifacts=[describe_artifact(tmp_path, artifact)], status=ArtifactStatus.PENDING,
        producer_version="test",
    )
    assert not manifest.reusable(tmp_path, "same")


def test_manifest_round_trip(tmp_path: Path) -> None:
    manifest = StageManifest("scan", "fp", {}, [], ArtifactStatus.FAILED, "test")
    path = tmp_path / "manifest.json"
    manifest.save(path)
    assert StageManifest.load(path) == manifest
