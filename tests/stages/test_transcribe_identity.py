import os
from pathlib import Path

from studio.core.project import Asset, AssetKind, AssetRole, Project
from studio.stages.transcribe import TranscribeStage


def test_source_content_invalidates_stage_with_preserved_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"first")
    requirements = tmp_path / "stages" / "prepare" / "requirements.json"
    requirements.parent.mkdir(parents=True)
    requirements.write_text('{"transcripts": ["rec"]}')
    project = Project(tmp_path, tmp_path, assets=[
        Asset("rec", Path("source.wav"), AssetKind.AUDIO, AssetRole.RECORDER),
    ])
    stage = TranscribeStage()
    before = stage.fingerprint(project, {})
    stat = source.stat()
    source.write_bytes(b"other")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert stage.fingerprint(project, {}) != before
