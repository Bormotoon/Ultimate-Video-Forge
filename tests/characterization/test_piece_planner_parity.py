import pytest
from whispersync.config import WhisperSyncConfig
from whispersync.engine.pipeline import clip_pieces as frozen_clip_pieces
from whispersync.models import AlignmentMap, Anchor

from studio.stages.sync_pieces import clip_pieces


@pytest.mark.parametrize("strategy", [1, 2, 3])
@pytest.mark.parametrize("offset", [-1.0, 0.0, 1.0])
@pytest.mark.parametrize("with_words", [False, True])
def test_extracted_planner_matches_frozen_geometry(
    strategy: int, offset: float, with_words: bool,
) -> None:
    alignment = AlignmentMap(
        anchors=[Anchor(
            cam_time=offset + t + 0.002 * t * t,
            rec_time=float(t), token=str(t), confidence=0.9,
        ) for t in range(2, 30, 2)], offset=offset, k=1.01, residual_ms=30.0,
    )
    settings = WhisperSyncConfig()
    words = [(4.0, 6.0), (16.0, 18.0), (24.0, 25.0)] if with_words else None
    expected = frozen_clip_pieces(
        alignment, 30.0, 40.0, strategy, settings, rec_word_gaps=[10.1], rec_words=words,
    )
    actual = clip_pieces(
        alignment, 30.0, 40.0, strategy, settings, rec_word_gaps=[10.1], rec_words=words,
    )
    assert actual == expected
