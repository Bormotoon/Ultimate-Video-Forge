from studio.core.transcript import Segment, Transcript, Word
from studio.core.transcript_index import TranscriptIndex, normalized_tokens


def _index() -> TranscriptIndex:
    transcript = Transcript(
        source_audio="audio.wav",  # type: ignore[arg-type]
        language="ru",
        duration=4.0,
        segments=[
            Segment(0.0, 2.0, (Word("Privet.", 0.0, 1.0), Word("Mir", 1.0, 2.0))),
            Segment(2.0, 4.0, (Word("Again", 2.0, 3.0), Word("now.", 3.0, 4.0))),
        ],
    )
    return TranscriptIndex.from_transcript(transcript)


def test_index_queries_words_text_and_tokens() -> None:
    index = _index()
    assert [word.text for word in index.words_between(0.5, 2.5)] == ["Privet.", "Mir", "Again"]
    assert index.text_between(0.0, 2.0) == "Privet. Mir"
    assert index.timed_tokens(0.0, 1.1) == [("privet", 0.0, 1.0), ("mir", 1.0, 2.0)]
    assert normalized_tokens("Vse, Eshche!") == ["vse", "eshche"]


def test_index_snaps_to_sentence_and_word_boundaries() -> None:
    index = _index()
    assert index.snap_start(0.3, max_shift=1.0) == 0.0
    assert index.snap_end(3.6, max_shift=1.0) == 4.0