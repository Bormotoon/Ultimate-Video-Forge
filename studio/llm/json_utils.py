"""Extract structured values from imperfect local LLM responses."""

from __future__ import annotations

import json
from typing import Any

_CLOSERS = {"{": "}", "[": "]"}


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def _repair_truncated_json(text: str) -> dict[str, Any] | list[Any] | None:
    """Recover completed JSON elements when a model stopped mid-response."""
    stack: list[str] = []
    in_string = False
    escape = False
    boundaries: list[tuple[int, tuple[str, ...]]] = []
    for index, character in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            stack.append(character)
        elif character in "}]":
            if stack:
                stack.pop()
            boundaries.append((index + 1, tuple(stack)))
        elif character == "," and stack:
            boundaries.append((index, tuple(stack)))
    for cut, snapshot in reversed(boundaries):
        candidate = text[:cut].rstrip() + "".join(_CLOSERS[item] for item in reversed(snapshot))
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value
    return None


def extract_first_json_value(text: str) -> dict[str, Any] | list[Any]:
    """Extract the first JSON object or array, tolerating prose and truncation."""
    cleaned = _strip_fences(text)
    start = next((index for index, character in enumerate(cleaned) if character in "[{"), -1)
    if start < 0:
        raise ValueError("Could not parse JSON: no object or array found")
    body = cleaned[start:]
    try:
        value, _ = json.JSONDecoder().raw_decode(body)
    except json.JSONDecodeError as error:
        repaired = _repair_truncated_json(body)
        if repaired is not None:
            return repaired
        raise ValueError(f"Could not parse JSON: {error}") from error
    if isinstance(value, (dict, list)):
        return value
    raise ValueError("Could not parse JSON: top-level value is not object or array")