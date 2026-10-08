from dataclasses import asdict

import pytest
from whispersync.engine.self_check import diagnose as frozen_diagnose
from whispersync.models import Word as FrozenWord

from studio.stages.sync_models import Word
from studio.stages.sync_self_check import diagnose


@pytest.mark.parametrize("shift,empty", [(0, False), (0.8, False), (0, True)])
def test_diagnostic_outcomes_match_frozen_source(shift: float, empty: bool) -> None:
    reference = [Word(f"word{i}", i * 0.5, i * 0.5 + 0.2, 0.99) for i in range(10)]
    rendered = [] if empty else [Word(word.text, word.start + shift, word.end + shift, 0.99)
                                for word in reference]
    def old(words):
        return [FrozenWord(word.text, word.start, word.end, word.probability) for word in words]
    assert asdict(diagnose(rendered, reference, clip_duration=6)) == asdict(
        frozen_diagnose(old(rendered), old(reference), clip_duration=6),
    )
