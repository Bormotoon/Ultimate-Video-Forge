from pathlib import Path

from studio.core.timeline import EditMap, KeepRange, TimeDomain
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.program import map_transcript_to_edited


def test_program_transcript_uses_edited_time_and_drops_cut_words() -> None:
    transcript = Transcript(
        Path("audio.wav"),
        "en",
        10,
        [
            Segment(
                0,
                9,
                (
                    Word("keep", 1, 2),
                    Word("cut", 4, 5),
                    Word("again", 7, 8),
                ),
            )
        ],
        time_domain=TimeDomain.TIMELINE,
    )
    edit = EditMap("edit", (KeepRange(0, 3), KeepRange(6, 10)))
    result = map_transcript_to_edited(transcript, edit)
    assert result.time_domain is TimeDomain.EDITED
    assert [(word.text, word.start, word.end) for word in result.words] == [
        ("keep", 1, 2),
        ("again", 4, 5),
    ]
    assert result.duration == 7
