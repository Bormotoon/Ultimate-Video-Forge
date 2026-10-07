"""Local OpenAI-compatible llama.cpp provider with deterministic caching."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class LlamaProvider:
    base_url: str
    model: str
    cache_dir: Path | None = None

    def complete(self, prompt: str, *, grammar: str | None = None) -> str:
        cache = self._cache_path(prompt, grammar)
        if cache and cache.is_file():
            return cache.read_text(encoding="utf-8")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        if grammar:
            body["grammar"] = grammar
        request = Request(
            self.base_url.rstrip("/") + "/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=600) as response:  # noqa: S310
            data = json.load(response)
        text = str(data["choices"][0]["message"]["content"])
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text, encoding="utf-8")
        return text

    def _cache_path(self, prompt: str, grammar: str | None) -> Path | None:
        if self.cache_dir is None:
            return None
        material = json.dumps(
            {"model": self.model, "prompt": prompt, "grammar": grammar},
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(material.encode()).hexdigest()
        return self.cache_dir / f"{digest}.txt"


def parse_json_response(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1])
    return json.loads(stripped)
