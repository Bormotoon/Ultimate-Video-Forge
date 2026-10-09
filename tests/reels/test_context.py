import pytest

from studio.core.transcript_index import TimedSentence, TranscriptIndex
from studio.reels.analysis.context import (
    build_transcript_digest,
    format_episode_context,
    format_metadata_for_digest,
    stratified_batches,
)
from studio.reels.analysis.contracts import coerce_moment_record


def test_digest_covers_episode_and_adds_signal_within_budget() -> None:
    sentences = [TimedSentence(i * 60, i * 60 + 5, f"Topic at position {i}.") for i in range(20)]
    digest = build_transcript_digest(TranscriptIndex(sentences=sentences), max_chars=800)
    assert len(digest) <= 800
    assert "[0.0s]" in digest
    assert "[1080.0s]" in digest or "[1140.0s]" in digest
    assert build_transcript_digest(TranscriptIndex(), max_chars=800) == ""
    assert len(build_transcript_digest(TranscriptIndex(sentences=sentences), max_chars=10)) <= 10


def test_judge_batches_mix_quality_and_preserve_every_record() -> None:
    records = [
        coerce_moment_record(
            {"start": i, "end": i + 1, "score": 10 - i, "quote": "Evidence", "title": str(i)}
        )
        for i in range(9)
    ]
    assert all(record is not None for record in records)
    batches = stratified_batches(records, 3)
    assert [[record.title for record in batch] for batch in batches] == [
        ["0", "3", "6"],
        ["1", "4", "7"],
        ["2", "5", "8"],
    ]
    assert stratified_batches([], 3) == []


@pytest.mark.parametrize("payload", [[], {}, {"summary": 42}, {"topics": [42]}])
def test_context_rejects_unusable_schema(payload) -> None:
    with pytest.raises(ValueError):
        format_episode_context(payload)


def test_metadata_is_bounded_and_skips_invalid_chapters() -> None:
    result = format_metadata_for_digest(
        {
            "title": "Episode",
            "description": "x" * 5000,
            "chapters": [
                None,
                {"start_time": -1, "title": "negative"},
                {"start_time": "NaN", "title": "nonfinite"},
                {"start_time": 12, "title": "Valid chapter"},
            ],
        }
    )
    assert len(result) <= 2000
    assert "Valid chapter" in result
    assert "negative" not in result and "nonfinite" not in result
    assert format_metadata_for_digest({}) == ""
