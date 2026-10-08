"""Recoverable publication of a stage's files, project, and final manifest.

Callers hold the project lock throughout recovery and publication. A journal
preserves the old files before any replacement; interrupted transactions roll
back on the next run. This is crash recovery, not a multi-file filesystem rename.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from studio.core.project import _atomic_json
from studio.core.workspace import published


def publish_files(
    root: Path, replacements: list[tuple[Path, Path]],
    *, cancelled: Callable[[], bool] = lambda: False,
) -> None:
    root = root.resolve()
    transactions = root / ".transactions"
    transactions.mkdir(exist_ok=True)
    transaction = Path(tempfile.mkdtemp(prefix="publish-", dir=transactions))
    entries: list[dict[str, str | None]] = []
    try:
        for index, (source, destination) in enumerate(replacements):
            target = _target(root, str(destination.relative_to(root)))
            if not source.is_file():
                raise ValueError(f"publication source is missing: {source}")
            if any(entry["path"] == str(target.relative_to(root)) for entry in entries):
                raise ValueError(f"duplicate publication target: {target}")
            backup = f"{index}.backup" if target.exists() else None
            if backup:
                shutil.copyfile(target, transaction / backup)
            entries.append({"path": str(target.relative_to(root)), "backup": backup})
        _atomic_json(transaction / "journal.json", {"schema_version": 1, "entries": entries})
        for source, destination in replacements:
            if cancelled():
                raise InterruptedError("publication was cancelled")
            with published(destination) as temporary:
                shutil.copyfile(source, temporary)
        if cancelled():
            raise InterruptedError("publication was cancelled")
        _atomic_json(transaction / "committed.json", {"status": "ok"})
    except BaseException:
        if (transaction / "journal.json").exists():
            _rollback(root, transaction)
        shutil.rmtree(transaction)
        raise
    shutil.rmtree(transaction)


def recover_publications(root: Path) -> None:
    root = root.resolve()
    transactions = root / ".transactions"
    if not transactions.exists():
        return
    for transaction in sorted(transactions.glob("publish-*")):
        if transaction.is_symlink() or not transaction.is_dir():
            raise ValueError(f"invalid publication journal directory: {transaction}")
        if not (transaction / "committed.json").is_file():
            if (transaction / "journal.json").exists():
                _rollback(root, transaction)
        shutil.rmtree(transaction)


def _rollback(root: Path, transaction: Path) -> None:
    data = json.loads((transaction / "journal.json").read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("unknown publication journal schema")
    for entry in reversed(data["entries"]):
        target = _target(root, entry["path"])
        backup = entry["backup"]
        if backup is None:
            target.unlink(missing_ok=True)
        else:
            if Path(backup).name != backup:
                raise ValueError("invalid publication backup path")
            with published(target) as temporary:
                shutil.copyfile(transaction / backup, temporary)


def _target(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"publication path must stay inside project: {relative}")
    target = root / path
    if target.resolve() != target.absolute():
        raise ValueError(f"publication path contains a symbolic link: {relative}")
    return target
