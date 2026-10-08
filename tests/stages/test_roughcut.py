from pathlib import Path

from studio.core.transcript import Segment, Transcript, Word
from studio.stages.roughcut import build_edit_list


def test_roughcut_trims_head_tail_and_long_pause_but_marks_fillers() -> None:
    transcript = Transcript(
        Path("audio.wav"),
        "en",
        10,
        [
            Segment(1, 2, (Word("hello", 1, 2),)),
            Segment(5, 6, (Word("um", 5, 5.2), Word("world", 5.3, 6))),
        ],
    )
    edit = build_edit_list(
        transcript,
        mode="cut",
        minimum_pause_s=1,
        keep_pause_s=0.4,
        edge_pad_s=0.2,
    )
    assert [cut.reason for cut in edit.cuts] == ["head", "pause", "tail"]
    assert edit.markers[0].kind == "filler"
    assert all(end > start for start, end in edit.keep)
