import pytest

from studio.stages.speakers import SpeakerTurn, smooth_short_turns


def test_isolated_short_island_merges_without_mutating_evidence() -> None:
    original = [SpeakerTurn(0, 1, "Alice"), SpeakerTurn(1, 1.1, "Bob"),
                SpeakerTurn(1.1, 2, "Alice")]
    assert smooth_short_turns(original) == [SpeakerTurn(0, 2, "Alice")]
    assert original[1].speaker == "Bob"
    assert smooth_short_turns(original, min_turn_s=0) == original


@pytest.mark.parametrize("speaker", ["unknown", "overlap"])
def test_uncertain_evidence_is_not_smoothed(speaker: str) -> None:
    turns = [SpeakerTurn(0, 1, "Alice"), SpeakerTurn(1, 1.1, speaker),
             SpeakerTurn(1.1, 2, "Alice")]
    assert smooth_short_turns(turns) == turns


@pytest.mark.parametrize("turns", [
    [SpeakerTurn(0, 1, "Alice"), SpeakerTurn(1, 1.1, "Bob"), SpeakerTurn(1.1, 2, "Carol")],
    [SpeakerTurn(0, 1, "Alice"), SpeakerTurn(1.5, 1.6, "Bob"), SpeakerTurn(1.6, 2.6, "Alice")],
    [SpeakerTurn(0, 1, "Alice"), SpeakerTurn(1, 1.5, "Bob"), SpeakerTurn(1.5, 2.5, "Alice")],
    [SpeakerTurn(0, 0.1, "Alice"), SpeakerTurn(0.1, 0.2, "Bob"),
     SpeakerTurn(0.2, 0.3, "Alice")],
])
def test_real_handoffs_pauses_and_short_neighbors_are_preserved(turns) -> None:
    assert smooth_short_turns(turns) == turns


def test_overlapping_turns_are_not_used_for_smoothing() -> None:
    turns = [SpeakerTurn(0, 1, "Alice"), SpeakerTurn(0.9, 1.1, "Bob"),
             SpeakerTurn(1.1, 2, "Alice")]
    assert smooth_short_turns(turns) == turns
