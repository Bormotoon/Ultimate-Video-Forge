"""Normalize local LLM candidate payloads into typed moment records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from studio.reels.analysis.contracts import MomentRecord, coerce_moment_record


def extract_candidate_payload(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    if isinstance(value.get("moment"), Mapping):
        return [value["moment"]]
    for key in ("moments", "candidates", "decisions", "reviews", "results", "items", "clips"):
        items = value.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, Mapping)]
    return []


def normalize_candidate_list(value: Any, *, stage: str) -> list[MomentRecord]:
    records: list[MomentRecord] = []
    for raw in extract_candidate_payload(value):
        record = coerce_moment_record(raw)
        if record is None:
            continue
        staged = coerce_moment_record({**record.to_dict(), "selection_stage": stage})
        if staged is not None:
            records.append(staged)
    return records


def build_candidate_json(records: Sequence[MomentRecord]) -> list[dict[str, Any]]:
    return [record.to_dict() for record in records]
