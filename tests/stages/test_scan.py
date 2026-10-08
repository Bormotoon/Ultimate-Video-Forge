from pathlib import Path

from studio.core.project import AssetRole
from studio.stages.scan import scan
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
