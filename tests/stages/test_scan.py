from pathlib import Path

from studio.core.project import Asset, AssetKind, AssetRole
from studio.stages.scan import apply_manual_overrides, scan
from tools.make_fixtures import generate


def test_scan_classifies_fixture_and_links_gopro_chapters(tmp_path: Path) -> None:
    source = generate(tmp_path / "fixture")
    assets, warnings = scan(source)
    assert warnings == []
    assert [asset.role for asset in assets].count(AssetRole.CAMERA) == 3
    assert [asset.role for asset in assets].count(AssetRole.RECORDER) == 1
    chapters = [asset for asset in assets if asset.path.parent.name == "camera-b"]
    assert chapters[0].chapter_of is None
    assert chapters[1].chapter_of == chapters[0].id
    assert all(asset.manual["media_info"]["duration_s"] > 0 for asset in assets)


def test_scan_keeps_explicit_manual_overrides() -> None:
    previous = [
        Asset(
            "camera-a",
            Path("camera-a.mp4"),
            AssetKind.VIDEO,
            AssetRole.CAMERA,
            manual={
                "media_info": {"duration_s": 1},
                "overrides": {"role": "ignore", "group_id": "wide", "device": "Manual"},
            },
        )
    ]
    scanned = [
        Asset(
            "camera-a",
            Path("camera-a.mp4"),
            AssetKind.VIDEO,
            AssetRole.CAMERA,
            device="Detected",
            group_id="detected",
            manual={"media_info": {"duration_s": 2}},
        )
    ]

    result = apply_manual_overrides(previous, scanned)

    assert result[0].role is AssetRole.IGNORE
    assert result[0].group_id == "wide"
    assert result[0].device == "Manual"
    assert result[0].manual == {
        "media_info": {"duration_s": 2},
        "overrides": {"role": "ignore", "group_id": "wide", "device": "Manual"},
    }
