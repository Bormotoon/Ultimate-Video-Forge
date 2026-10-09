from studio.reels.analysis.contracts import coerce_moment_record
from studio.reels.analysis.decisions import CLEANUP_EDITABLE, apply_stage_decisions
from studio.reels.analysis.refine import (
    build_cleanup_payload,
    cleanup_batches,
    limit_review_candidates,
)


def record(start, candidate_id):
    result = coerce_moment_record(
        {
            "start": start,
            "end": start + 10,
            "candidate_id": candidate_id,
            "quote": candidate_id,
            "title": "Presentation",
            "why": "Evidence",
            "score": 8,
        }
    )
    assert result is not None
    return result


def test_cleanup_groups_by_time_and_removes_duplicate_evidence() -> None:
    early, late = record(0, "early"), record(100, "late")
    batches = cleanup_batches([late, early, early], 2)
    assert [[item.candidate_id for item in batch] for batch in batches] == [["early", "late"]]


def test_review_cap_is_deterministic_and_tracks_excluded_candidates() -> None:
    early, late = record(0, "early"), record(100, "late")
    selected, excluded = limit_review_candidates([late, early, early], 1)
    assert [item.candidate_id for item in selected] == ["early"]
    assert [item.candidate_id for item in excluded] == ["late"]
    assert limit_review_candidates([early, late], 1) == (selected, excluded)


def test_refine_payload_and_decisions_preserve_source_evidence() -> None:
    source = record(0, "source")
    payload = build_cleanup_payload([source])[0]
    assert payload["evidence"] == "Evidence"
    assert "title" not in payload and "caption" not in payload
    outcome = apply_stage_decisions(
        [source],
        {
            "decisions": [
                {
                    "candidate_id": "source",
                    "keep": True,
                    "start": 99,
                    "end": 999,
                    "quote": "invented",
                    "title": "rewritten",
                }
            ]
        },
        stage="cleanup",
        editable=CLEANUP_EDITABLE,
    )
    assert outcome.records[0].start == source.start
    assert outcome.records[0].end == source.end
    assert outcome.records[0].quote == source.quote
    assert outcome.records[0].title == source.title
