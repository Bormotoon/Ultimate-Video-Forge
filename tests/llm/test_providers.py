from pathlib import Path

from studio.llm.providers import LlamaProvider, parse_json_response


def test_cache_key_includes_model_prompt_and_grammar(tmp_path: Path) -> None:
    first = LlamaProvider("http://localhost", "a", tmp_path)._cache_path("prompt", None)
    changed_model = LlamaProvider("http://localhost", "b", tmp_path)._cache_path("prompt", None)
    changed_grammar = LlamaProvider("http://localhost", "a", tmp_path)._cache_path(
        "prompt", "root ::= object"
    )
    assert len({first, changed_model, changed_grammar}) == 3


def test_json_response_accepts_plain_and_fenced_json() -> None:
    assert parse_json_response('{"ok": true}') == {"ok": True}
    assert parse_json_response('```json\n{"ok": true}\n```') == {"ok": True}
