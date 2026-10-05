"""Versioned project and artifact-manifest persistence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

PROJECT_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1


class AssetKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    OTHER = "other"


class AssetRole(StrEnum):
    CAMERA = "camera"
    RECORDER = "recorder"
    IGNORE = "ignore"
    UNKNOWN = "unknown"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"


@dataclass(slots=True)
class Asset:
    id: str
    path: Path
    kind: AssetKind
    role: AssetRole
    device: str | None = None
    group_id: str | None = None
    chapter_of: str | None = None
    manual: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Project:
    source_dir: Path
    work_dir: Path
    assets: list[Asset] = field(default_factory=list)
    outputs: dict[str, list[Path]] = field(default_factory=dict)
    plan_revision: int = 1
    version: int = 1
    schema_version: int = PROJECT_SCHEMA_VERSION
    producer_version: str = "studio-dev"

    def save(self, path: Path) -> None:
        _atomic_json(path, self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "schema_version": self.schema_version,
            "producer_version": self.producer_version,
            "source_dir": str(self.source_dir),
            "work_dir": str(self.work_dir),
            "plan_revision": self.plan_revision,
            "assets": [
                {
                    **asdict(asset),
                    "path": str(asset.path),
                    "kind": asset.kind.value,
                    "role": asset.role.value,
                }
                for asset in self.assets
            ],
            "outputs": {key: [str(path) for path in paths] for key, paths in self.outputs.items()},
        }

    @classmethod
    def load(cls, path: Path) -> Project:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != PROJECT_SCHEMA_VERSION:
            raise ValueError(f"unsupported project schema: {data.get('schema_version')}")
        return cls(
            source_dir=Path(data["source_dir"]),
            work_dir=Path(data["work_dir"]),
            assets=[
                Asset(
                    id=item["id"],
                    path=Path(item["path"]),
                    kind=AssetKind(item["kind"]),
                    role=AssetRole(item["role"]),
                    device=item.get("device"),
                    group_id=item.get("group_id"),
                    chapter_of=item.get("chapter_of"),
                    manual=item.get("manual", {}),
                )
                for item in data.get("assets", [])
            ],
            outputs={
                key: [Path(value) for value in values]
                for key, values in data.get("outputs", {}).items()
            },
            plan_revision=int(data.get("plan_revision", 1)),
            version=int(data.get("version", 1)),
            schema_version=int(data["schema_version"]),
            producer_version=str(data.get("producer_version", "unknown")),
        )


@dataclass(frozen=True, slots=True)
class Artifact:
    path: str
    sha256: str
    size: int


@dataclass(slots=True)
class StageManifest:
    stage: str
    fingerprint: str
    inputs: dict[str, Any]
    artifacts: list[Artifact]
    status: ArtifactStatus
    producer_version: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def save(self, path: Path) -> None:
        data = asdict(self)
        data["status"] = self.status.value
        _atomic_json(path, data)

    @classmethod
    def load(cls, path: Path) -> StageManifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"unsupported manifest schema: {data.get('schema_version')}")
        return cls(
            stage=data["stage"], fingerprint=data["fingerprint"], inputs=data["inputs"],
            artifacts=[Artifact(**item) for item in data["artifacts"]],
            status=ArtifactStatus(data["status"]), producer_version=data["producer_version"],
            created_at=data["created_at"], schema_version=data["schema_version"],
        )

    def reusable(self, root: Path, fingerprint: str) -> bool:
        if self.status is not ArtifactStatus.OK or self.fingerprint != fingerprint:
            return False
        return all(_artifact_matches(root, artifact) for artifact in self.artifacts)


def describe_artifact(root: Path, path: Path) -> Artifact:
    resolved = path if path.is_absolute() else root / path
    relative = resolved.relative_to(root)
    return Artifact(str(relative), _sha256(resolved), resolved.stat().st_size)


def stable_fingerprint(*parts: Any) -> str:
    material = json.dumps(
        parts, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _artifact_matches(root: Path, artifact: Artifact) -> bool:
    path = root / artifact.path
    try:
        return path.stat().st_size == artifact.size and _sha256(path) == artifact.sha256
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
