from dataclasses import replace
from pathlib import Path

import pytest

from studio.core.subtitles import Cue, SubtitleStyle, load_edits, save_edits, to_ass, validate
from studio.core.transcript import Segment, Transcript, Word


def test_saved_edits_roundtrip_and_stale_guard(tmp_path: Path) -> None:
    transcript = Transcript(
        tmp_path / "video.mp4", "en", 5, [Segment(1, 3, (Word("original", 1, 3),))]
    )
    cues = [Cue(1.2, 2.8, "Edited text")]
    style = SubtitleStyle(size=60, color="#FFFF00", alignment=8)
    save_edits(tmp_path, "reel-001", transcript, cues, style)
    assert load_edits(tmp_path, "reel-001", transcript) == (cues, style)
    with pytest.raises(ValueError, match="older transcript"):
        load_edits(tmp_path, "reel-001", replace(transcript, duration=6))
    assert transcript.words[0].text == "original"


@pytest.mark.parametrize(
    "cues",
    [
        [Cue(-1, 2, "x")],
        [Cue(1, 6, "x")],
        [Cue(1, 2, "")],
        [Cue(1, 3, "x"), Cue(2, 4, "y")],
        [Cue(float("nan"), 2, "x")],
    ],
)
def test_invalid_cues_are_rejected(cues: list[Cue]) -> None:
    with pytest.raises(ValueError):
        validate(cues, SubtitleStyle(), 5)


def test_ass_has_style_and_no_user_override_tags() -> None:
    ass = to_ass([Cue(1, 2, "{\\pos(0,0)}hello\nworld")], SubtitleStyle(color="#FFCC00", size=60))
    assert "&H0000CCFF" in ass
    assert "0:00:01.00,0:00:02.00" in ass
    assert "hello\\Nworld" in ass
    assert "{\\pos" not in ass
