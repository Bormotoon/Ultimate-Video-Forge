from studio.core.audio_spans import Span, parse_silencedetect, word_gaps
from studio.core.transcript import Word


def test_word_gaps_and_silencedetect() -> None:
    words = [Word("a", 0, 1), Word("b", 2.5, 3)]
    assert word_gaps(words, minimum_s=1.0) == [Span(1, 2.5)]
    log = "[silencedetect] silence_start: 1.0\n[silencedetect] silence_end: 2.5"
    assert parse_silencedetect(log) == [Span(1, 2.5)]
