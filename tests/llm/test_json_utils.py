import pytest

from studio.llm.json_utils import extract_first_json_value


def test_extracts_objects_arrays_fences_and_surrounding_prose() -> None:
    assert extract_first_json_value('{"a": 1}') == {"a": 1}
    assert extract_first_json_value("[1, 2]") == [1, 2]
    assert extract_first_json_value('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_first_json_value('Answer: {"a": 1}\nDone.') == {"a": 1}


def test_recovers_complete_values_from_truncated_model_output() -> None:
    value = extract_first_json_value(
        '{"moments": [{"start": 1, "title": "one"}, {"start": 2, "title": "cut'
    )
    assert value == {"moments": [{"start": 1, "title": "one"}, {"start": 2}]}


def test_rejects_text_without_a_structured_value() -> None:
    with pytest.raises(ValueError, match="Could not parse JSON"):
        extract_first_json_value("no JSON here")