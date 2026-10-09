from studio.reels.analysis.candidate_extraction import normalize_candidate_list
from studio.reels.analysis.scoring import combined_priority_score, resolve_scoring_weights


def _moment(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "start": 0,
        "end": 45,
        "title": "Why schools lose students",
        "quote": "We lost half the class in one year",
        "why": "Unexpected number and conflict",
        "hook": "Why schools lose students every year?",
        "score": 8,
    }
    value.update(changes)
    return value


def test_candidate_normalization_marks_stage_and_drops_invalid_records() -> None:
    records = normalize_candidate_list(
        {"moments": [_moment(), {"start": 3, "end": 2, "title": "bad"}]}, stage="scout"
    )
    assert len(records) == 1
    assert records[0].selection_stage == "scout"


def test_priority_penalizes_mid_thought_and_never_becomes_negative() -> None:
    complete = combined_priority_score(_moment(), target_min=30, target_max=60)
    truncated = combined_priority_score(
        _moment(title="Why schools..."), target_min=30, target_max=60
    )
    assert truncated < complete
    assert (
        combined_priority_score(
            _moment(title="...", quote="a", why="no", score=0),
            target_min=30,
            target_max=60,
            weights={"mid_thought": 100},
        )
        == 0
    )


def test_scoring_weights_ignore_unknown_and_invalid_values() -> None:
    weights = resolve_scoring_weights({"hook": 3, "unknown": 1, "duration": "bad"})
    assert weights["hook"] == 3
    assert "unknown" not in weights
