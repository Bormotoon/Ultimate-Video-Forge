from pathlib import Path

import pytest

from tools.make_fixtures import generate


def test_generate_requires_force_for_existing_directory(tmp_path: Path) -> None:
    output = tmp_path / "fixture"
    output.mkdir()
    with pytest.raises(FileExistsError, match="--force"):
        generate(output)


def test_generate_creates_expected_media_tree(tmp_path: Path) -> None:
    output = generate(tmp_path / "fixture")
    assert (output / "fixture.json").is_file()
    assert (output / "camera-a" / "DJI_0001.MP4").stat().st_size > 0
    assert (output / "camera-b" / "GX010024.MP4").stat().st_size > 0
    assert (output / "camera-b" / "GX020024.MP4").stat().st_size > 0
    assert (output / "recorder" / "ZOOM0001_Tr1.WAV").stat().st_size > 0
