from studio.reels.analysis.chunking import build_analysis_chunks, transcript_units_from_segments


def _words(text: str, start: float) -> list[dict[str, float | str]]:
    return [
        {"word": word, "start": start + index, "end": start + index + 1}
        for index, word in enumerate(text.split())
    ]


def test_units_use_word_timing_for_sentence_boundaries() -> None:
    text = "Yes. This is a much longer second sentence."
    units = transcript_units_from_segments(
        [{"start": 0, "end": 10, "text": text, "words": _words(text, 0)}]
    )
    assert [unit.text for unit in units] == ["Yes.", "This is a much longer second sentence."]
    assert units[0].end == 1
    assert units[1].start == 1


def test_chunks_respect_budgets_and_overlap() -> None:
    segments = [
        {"start": index * 10, "end": index * 10 + 10, "text": f"Sentence {index}."}
        for index in range(20)
    ]
    chunks = build_analysis_chunks(segments, chunk_seconds=50, max_chars=10_000, overlap_seconds=20)
    assert len(chunks) > 1
    assert all(chunk.end - chunk.start <= 50 for chunk in chunks)
    assert chunks[1].start < chunks[0].end
