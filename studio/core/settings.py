"""Typed, layered Studio settings with fail-fast validation."""

from __future__ import annotations

import math
import types
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

import yaml


class SettingsError(ValueError):
    pass


@dataclass(slots=True)
class ScanSettings:
    grouping: str = "auto"
    probe_timeout_s: float = 30.0


@dataclass(slots=True)
class SyncSettings:
    mode: str = "auto"
    strategy: int = 3
    max_drift_ms: float = 20.0


@dataclass(slots=True)
class TranscribeSettings:
    model: str = "large-v3"
    language: str | None = "ru"
    device: str = "auto"
    compute_type: str = "auto"
    mode: str = "fast"
    batch_size: int = 16
    beam_size: int = 5
    quality_beam_size: int = 10
    initial_prompt: str = ""
    use_cache: bool = True
    glossary: list[str] = field(default_factory=list)
    keep_fillers: bool = True


@dataclass(slots=True)
class RoughcutSettings:
    enabled: bool = True
    mode: str = "cut"
    pause_min_s: float = 1.0
    pause_keep_s: float = 0.4
    silence_threshold_db: float = -40.0
    head_tail_pad_s: float = 0.2
    detect_retakes: bool = True
    retake_min_words: int = 4
    retake_max_gap_s: float = 6.0
    phrase_gap_threshold: float = 0.6


@dataclass(slots=True)
class ExportSettings:
    targets: list[str] = field(default_factory=lambda: ["fcpxml", "xmeml"])
    fcpxml_version: str = "1.9"


@dataclass(slots=True)
class ProgramSettings:
    enabled: bool = False
    encoder: str = "auto"


@dataclass(slots=True)
class ComputeSettings:
    allow_cpu: bool = False


@dataclass(slots=True)
class SpeakerSettings:
    method: str = "auto"
    margin_db: float = 6.0
    silence_floor_db: float = -60.0
    step_s: float = 0.05
    tracks: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Settings:
    scan: ScanSettings = field(default_factory=ScanSettings)
    sync: SyncSettings = field(default_factory=SyncSettings)
    transcribe: TranscribeSettings = field(default_factory=TranscribeSettings)
    roughcut: RoughcutSettings = field(default_factory=RoughcutSettings)
    export: ExportSettings = field(default_factory=ExportSettings)
    program: ProgramSettings = field(default_factory=ProgramSettings)
    compute: ComputeSettings = field(default_factory=ComputeSettings)
    speakers: SpeakerSettings = field(default_factory=SpeakerSettings)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_settings(paths: list[Path], overrides: list[str] | None = None) -> Settings:
    merged: dict[str, Any] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise SettingsError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(value, dict):
            raise SettingsError(f"{path}: settings root must be a mapping")
        merged = _merge(merged, value)
    for override in overrides or []:
        _set_override(merged, override)
    settings = _construct(merged)
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    for section_name in Settings.__dataclass_fields__:
        section = getattr(settings, section_name)
        for name, expected in get_type_hints(type(section)).items():
            if not _matches_type(getattr(section, name), expected):
                raise SettingsError(f"{section_name}.{name} has an invalid value type")
    _one_of("scan.grouping", settings.scan.grouping, {"auto", "folders", "names"})
    _positive("scan.probe_timeout_s", settings.scan.probe_timeout_s)
    _one_of("sync.mode", settings.sync.mode, {"auto", "camera", "simple", "complex"})
    if settings.sync.strategy not in {1, 2, 3}:
        raise SettingsError("sync.strategy must be 1, 2, or 3")
    _non_negative("sync.max_drift_ms", settings.sync.max_drift_ms)
    _one_of("transcribe.device", settings.transcribe.device, {"auto", "cuda", "cpu"})
    _one_of("transcribe.mode", settings.transcribe.mode, {"fast", "quality"})
    if settings.transcribe.batch_size < 1:
        raise SettingsError("transcribe.batch_size must be at least 1")
    for name in ("batch_size", "beam_size", "quality_beam_size"):
        value = getattr(settings.transcribe, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise SettingsError(f"transcribe.{name} must be a positive integer")
    if not isinstance(settings.transcribe.glossary, list) or not all(
        isinstance(item, str) for item in settings.transcribe.glossary
    ):
        raise SettingsError("transcribe.glossary must be a list of strings")
    _one_of("roughcut.mode", settings.roughcut.mode, {"cut", "markers"})
    _one_of("speakers.method", settings.speakers.method, {"auto", "mics", "off"})
    _non_negative("speakers.margin_db", settings.speakers.margin_db)
    _positive("speakers.step_s", settings.speakers.step_s)
    if settings.speakers.step_s > 1:
        raise SettingsError("speakers.step_s must not exceed 1 second")
    if not math.isfinite(settings.speakers.silence_floor_db) or not (
        -100 <= settings.speakers.silence_floor_db <= 0
    ):
        raise SettingsError("speakers.silence_floor_db must be between -100 and 0")
    _one_of("program.encoder", settings.program.encoder, {"auto", "nvenc", "libx264"})
    _positive("roughcut.pause_min_s", settings.roughcut.pause_min_s)
    _non_negative("roughcut.pause_keep_s", settings.roughcut.pause_keep_s)
    _non_negative("roughcut.head_tail_pad_s", settings.roughcut.head_tail_pad_s)
    _non_negative("roughcut.retake_max_gap_s", settings.roughcut.retake_max_gap_s)
    _positive("roughcut.phrase_gap_threshold", settings.roughcut.phrase_gap_threshold)
    if settings.roughcut.retake_min_words < 1:
        raise SettingsError("roughcut.retake_min_words must be a positive integer")
    threshold = settings.roughcut.silence_threshold_db
    if (not isinstance(threshold, (float, int)) or isinstance(threshold, bool)
            or not math.isfinite(threshold) or not -100 <= threshold <= 0):
        raise SettingsError("roughcut.silence_threshold_db must be between -100 and 0")
    if settings.roughcut.pause_keep_s >= settings.roughcut.pause_min_s:
        raise SettingsError("roughcut.pause_keep_s must be less than pause_min_s")
    unknown = set(settings.export.targets) - {"fcpxml", "xmeml"}
    if unknown:
        raise SettingsError(f"export.targets contains unsupported values: {sorted(unknown)}")


def _construct(data: dict[str, Any]) -> Settings:
    allowed = {field.name for field in Settings.__dataclass_fields__.values()}
    unknown = set(data) - allowed
    if unknown:
        raise SettingsError(f"unknown settings sections: {sorted(unknown)}")
    try:
        return Settings(
            scan=ScanSettings(**_section(data, "scan")),
            sync=SyncSettings(**_section(data, "sync")),
            transcribe=TranscribeSettings(**_section(data, "transcribe")),
            roughcut=RoughcutSettings(**_section(data, "roughcut")),
            export=ExportSettings(**_section(data, "export")),
            program=ProgramSettings(**_section(data, "program")),
            compute=ComputeSettings(**_section(data, "compute")),
            speakers=SpeakerSettings(**_section(data, "speakers")),
        )
    except TypeError as exc:
        raise SettingsError(str(exc)) from exc


def _matches_type(value: Any, expected: Any) -> bool:
    origin = get_origin(expected)
    if origin in (Union, types.UnionType):
        return any(_matches_type(value, item) for item in get_args(expected))
    if origin is list:
        return isinstance(value, list) and all(
            _matches_type(item, get_args(expected)[0]) for item in value
        )
    if origin is dict:
        key_type, value_type = get_args(expected)
        return isinstance(value, dict) and all(
            _matches_type(key, key_type) and _matches_type(item, value_type)
            for key, item in value.items()
        )
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, expected)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise SettingsError(f"{name} must be a mapping")
    return value


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _set_override(settings: dict[str, Any], expression: str) -> None:
    if "=" not in expression or "." not in expression.split("=", 1)[0]:
        raise SettingsError(f"invalid --set expression: {expression}")
    dotted, raw = expression.split("=", 1)
    keys = dotted.split(".")
    target = settings
    for key in keys[:-1]:
        value = target.setdefault(key, {})
        if not isinstance(value, dict):
            raise SettingsError(f"cannot set child of scalar setting: {key}")
        target = value
    try:
        target[keys[-1]] = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SettingsError(f"invalid override value: {raw}") from exc


def _one_of(name: str, value: str, choices: set[str]) -> None:
    if not isinstance(value, str) or value not in choices:
        raise SettingsError(f"{name} must be one of {sorted(choices)}")


def _positive(name: str, value: float) -> None:
    valid_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not valid_number or not math.isfinite(value) or value <= 0:
        raise SettingsError(f"{name} must be a finite positive number")


def _non_negative(name: str, value: float) -> None:
    valid_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not valid_number or not math.isfinite(value) or value < 0:
        raise SettingsError(f"{name} must be a finite non-negative number")
