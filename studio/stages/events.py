"""Strict JSON-lines protocol between stage workers and the runner."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from studio.core.enums import StrEnum


class EventType(StrEnum):
    START = "start"
    PROGRESS = "progress"
    TIMELINE = "timeline"
    WARNING = "warning"
    ARTIFACT = "artifact"
    DONE = "done"


@dataclass(frozen=True, slots=True)
class StageEvent:
    type: EventType
    stage: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"t": self.type.value, "stage": self.stage, **self.payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, line: str) -> StageEvent:
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid worker event JSON: {exc.msg}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("stage"), str):
            raise ValueError("worker event requires string fields 't' and 'stage'")
        try:
            event_type = EventType(data.pop("t"))
        except (KeyError, ValueError) as exc:
            raise ValueError("worker event has unknown or missing type") from exc
        stage = data.pop("stage")
        _validate_payload(event_type, data)
        return cls(event_type, stage, data)


def _validate_payload(event_type: EventType, payload: dict[str, Any]) -> None:
    if event_type is EventType.PROGRESS:
        value = payload.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
            raise ValueError("progress event value must be between 0 and 1")
    if event_type is EventType.ARTIFACT and not isinstance(payload.get("path"), str):
        raise ValueError("artifact event requires a path")
    if event_type is EventType.DONE and payload.get("status") not in {"ok", "failed"}:
        raise ValueError("done event status must be ok or failed")
