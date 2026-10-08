import numpy as np

from studio.core.transcript import Word
from studio.stages.speakers import assign_words_to_mics


def test_mic_assignment_handles_bleed_and_overlap() -> None:
    words = [Word("one", 0, 0.1), Word("both", 0.1, 0.2), Word("two", 0.2, 0.3)]
    envelopes = {
        "S1": np.array([1.0, 1.0, 1.0, 1.0, 0.1, 0.1]),
        "S2": np.array([0.1, 0.1, 1.0, 1.0, 1.0, 1.0]),
    }
    turns = assign_words_to_mics(words, envelopes)
    assert [turn.speaker for turn in turns] == ["S1", "overlap", "S2"]
