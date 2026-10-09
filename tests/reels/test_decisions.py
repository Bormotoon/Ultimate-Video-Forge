"""RU: Решения cleanup/judge по candidate_id, батчи judge и обзор эпизода.

EN: Cleanup/judge decisions by candidate_id, judge batching, and the episode
overview.
"""

from __future__ import annotations

from studio.reels.analysis.contracts import MomentRecord, coerce_moment_record
from studio.reels.analysis.decisions import (
    CLEANUP_EDITABLE,
    JUDGE_EDITABLE,
    apply_stage_decisions,
)


def _record(
    cid: str, start: float, end: float, *, quote: str = "", score: float = 7.0
) -> MomentRecord:
    record = coerce_moment_record(
        {
            "candidate_id": cid,
            "start": start,
            "end": end,
            "quote": quote or f"настоящая цитата номер {cid}",
            "why": "доказательство",
            "score": score,
        },
    )
    assert record is not None
    return record


INPUTS = [_record("a", 0, 40), _record("b", 30, 70), _record("c", 200, 240)]


# -- decisions ---------------------------------------------------------------


def test_judge_cannot_move_bounds_or_rewrite_the_quote() -> None:
    response = {
        "reviews": [
            {
                "candidate_id": "a",
                "keep": True,
                "score": 9,
                "title": "Новый заголовок",
                "quote": "переписанная цитата",
                "start": 500,
                "end": 560,
            },
        ],
    }
    outcome = apply_stage_decisions(INPUTS, response, stage="judge", editable=JUDGE_EDITABLE)
    judged = {r.candidate_id: r for r in outcome.records}["a"]
    assert judged.title == "Новый заголовок"
    assert judged.quote == INPUTS[0].quote
    assert (judged.start, judged.end) == (0.0, 40.0)
    assert judged.score == 9.0


def test_keep_false_drops_and_unmentioned_survive() -> None:
    """Truncation cuts the tail of the list; that is not a verdict."""
    response = {"decisions": [{"candidate_id": "b", "keep": False}]}
    outcome = apply_stage_decisions(INPUTS, response, stage="cleanup", editable=CLEANUP_EDITABLE)
    assert [r.candidate_id for r in outcome.records] == ["a", "c"]
    assert outcome.dropped == ["b"]
    assert outcome.unmentioned == 2


def test_merge_widens_over_overlapping_sources_and_records_lineage() -> None:
    response = {"decisions": [{"candidate_id": "a", "keep": True, "merge_ids": ["b"]}]}
    outcome = apply_stage_decisions(INPUTS, response, stage="cleanup", editable=CLEANUP_EDITABLE)
    merged = {r.candidate_id: r for r in outcome.records}
    assert "b" not in merged
    assert (merged["a"].start, merged["a"].end) == (0.0, 70.0)
    assert merged["a"].merged_ids == ("b",)
    assert merged["a"].quote == INPUTS[0].quote


def test_merge_of_a_disjoint_candidate_does_not_glue_footage() -> None:
    response = {"decisions": [{"candidate_id": "a", "keep": True, "merge_ids": ["c"]}]}
    outcome = apply_stage_decisions(INPUTS, response, stage="cleanup", editable=CLEANUP_EDITABLE)
    merged = {r.candidate_id: r for r in outcome.records}
    assert (merged["a"].start, merged["a"].end) == (0.0, 40.0)
    assert "c" not in merged


def test_unknown_ids_are_ignored_not_invented() -> None:
    response = {"reviews": [{"candidate_id": "zzz", "keep": True, "score": 10}]}
    outcome = apply_stage_decisions(INPUTS, response, stage="judge", editable=JUDGE_EDITABLE)
    assert outcome.untraceable == 1
    assert [r.candidate_id for r in outcome.records] == ["a", "b", "c"]


def test_empty_response_keeps_the_batch() -> None:
    outcome = apply_stage_decisions(INPUTS, [], stage="judge", editable=JUDGE_EDITABLE)
    assert outcome.records == INPUTS


def test_legacy_full_records_are_traced_and_evidence_restored() -> None:
    """Old custom prompts answer with full records; they still apply safely."""
    response = {
        "moments": [
            {"start": 1, "end": 39, "title": "Старый формат", "quote": INPUTS[0].quote, "score": 8},
        ],
    }
    outcome = apply_stage_decisions(INPUTS, response, stage="judge", editable=JUDGE_EDITABLE)
    assert outcome.mode == "legacy"
    assert [r.candidate_id for r in outcome.records] == ["a"]
    assert (outcome.records[0].start, outcome.records[0].end) == (0.0, 40.0)
    assert outcome.records[0].title == "Старый формат"


def test_string_booleans_are_understood() -> None:
    response = {"decisions": [{"candidate_id": "a", "keep": "false"}]}
    outcome = apply_stage_decisions(INPUTS, response, stage="cleanup", editable=CLEANUP_EDITABLE)
    assert "a" in outcome.dropped
