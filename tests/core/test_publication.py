import subprocess
import sys
from pathlib import Path

import pytest

from studio.core import publication
from studio.core.publication import publish_files, recover_publications


def test_publication_failure_rolls_back_all_replacements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = tmp_path / "project.json"
    old.write_bytes(b"old project")
    first = tmp_path / "first"
    first.write_bytes(b"new project")
    second = tmp_path / "second"
    second.write_bytes(b"new artifact")
    artifact = tmp_path / "result.wav"
    copyfile = publication.shutil.copyfile

    def fail(source: Path, destination: Path) -> None:
        if source == second:
            raise OSError("disk full")
        copyfile(source, destination)

    monkeypatch.setattr(publication.shutil, "copyfile", fail)
    with pytest.raises(OSError, match="disk full"):
        publish_files(tmp_path, [(first, old), (second, artifact)])
    assert old.read_bytes() == b"old project"
    assert not artifact.exists()
    assert list((tmp_path / ".transactions").iterdir()) == []


def test_killed_publisher_recovers_last_successful_result(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    project.write_bytes(b"old project")
    artifact = tmp_path / "result.wav"
    artifact.write_bytes(b"old audio")
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"old manifest")
    script = '''
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from studio.core import publication
root = Path(sys.argv[1])
source = root / "new"
source.write_bytes(b"new audio")
original = publication.published
@contextmanager
def interrupted(path):
    with original(path) as temporary:
        yield temporary
    os._exit(91)
publication.published = interrupted
publication.publish_files(root, [(source, root / "result.wav")])
'''
    process = subprocess.run([sys.executable, "-c", script, str(tmp_path)], timeout=10)
    assert process.returncode == 91
    assert artifact.read_bytes() == b"new audio"
    recover_publications(tmp_path)
    assert artifact.read_bytes() == b"old audio"
    assert project.read_bytes() == b"old project"
    assert manifest.read_bytes() == b"old manifest"
    assert list((tmp_path / ".transactions").iterdir()) == []
    recover_publications(tmp_path)


def test_successful_publication_has_no_pending_journal(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"complete")
    destination = tmp_path / "stages" / "scan" / "report.json"
    publish_files(tmp_path, [(source, destination)])
    recover_publications(tmp_path)
    assert destination.read_bytes() == b"complete"


def test_cancellation_during_publication_restores_previous_generation(tmp_path: Path) -> None:
    source = tmp_path / "new"
    source.write_bytes(b"complete new file")
    artifact = tmp_path / "audio.wav"
    artifact.write_bytes(b"old audio")
    project = tmp_path / "project.json"
    project.write_bytes(b"old project")
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls == 2

    with pytest.raises(InterruptedError, match="cancelled"):
        publish_files(tmp_path, [(source, artifact), (source, project)], cancelled=cancelled)
    assert artifact.read_bytes() == b"old audio"
    assert project.read_bytes() == b"old project"
    assert list((tmp_path / ".transactions").iterdir()) == []
