"""Evidence-only cleanup requests and time-local batching from Forge."""

from collections.abc import Sequence

from studio.reels.analysis.contracts import MomentRecord
from studio.reels.analysis.ranking import dedupe_moments, ranking_value


def limit_review_candidates(
    records: Sequence[MomentRecord], limit: int
) -> tuple[list[MomentRecord], list[MomentRecord]]:
    ordered = sorted(
        dedupe_moments(records),
        key=lambda record: (-ranking_value(record), record.start, record.end, record.candidate_id),
    )
    return ordered[:limit], ordered[limit:]


def cleanup_batches(records: Sequence[MomentRecord], batch_size: int) -> list[list[MomentRecord]]:
    ordered = sorted(dedupe_moments(records), key=lambda record: (record.start, record.end))
    size = max(1, batch_size)
    return [ordered[offset : offset + size] for offset in range(0, len(ordered), size)]


def build_cleanup_payload(records: Sequence[MomentRecord]) -> list[dict]:
    payload = []
    for record in records:
        item = {
            "candidate_id": record.candidate_id,
            "start": round(record.start, 1),
            "end": round(record.end, 1),
            "quote": record.quote,
            "evidence": record.why,
            "score": record.score,
        }
        if record.reason_codes:
            item["reason_codes"] = list(record.reason_codes)
        if record.quote_match_ratio is not None:
            item["quote_match_ratio"] = record.quote_match_ratio
        payload.append(item)
    return payload
