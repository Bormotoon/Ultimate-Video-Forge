from dataclasses import asdict

import pytest
from whispersync.config import WhisperSyncConfig
from whispersync.engine.retakes import detect_retakes as frozen_detect
from whispersync.models import Word as FrozenWord

from studio.core.transcript import Word
from studio.stages.retake_models import RetakeSettings
from studio.stages.retakes import detect_retakes


@pytest.mark.parametrize("starts", [(0, 3), (0, 3, 6), (0, 3, 6, 9), (0, 30)])
def test_retakes_preserve_frozen_behavior(starts: tuple[int, ...]) -> None:
    words = [Word(token, start + index * 0.3, start + index * 0.3 + 0.2, 0.9)
             for start in starts
             for index, token in enumerate("one two three four five six".split())]
    expected = frozen_detect(
        [FrozenWord(word.text, word.start, word.end, word.probability) for word in words],
        WhisperSyncConfig(detect_retakes=True),
    )
    actual = detect_retakes(words, RetakeSettings(detect_retakes=True))
    assert [asdict(group) for group in actual] == [asdict(group) for group in expected]
