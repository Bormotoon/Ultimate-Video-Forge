from studio.reels.analysis.contracts import coerce_moment_record, has_quote_evidence, replace_record


def test_moment_contract_coerces_legacy_evidence_and_preserves_extensions() -> None:
    record = coerce_moment_record(
        {
            "start": "1.25",
            "end": 4,
            "title": "Title",
            "quote": "two quoted words",
            "evidence": "why",
            "score": "8",
            "legacy": True,
            "crop_confidence": 0.5,
        }
    )
    assert record is not None
    assert record.why == "why"
    assert record.extra == {"legacy": True}
    assert record.to_dict()["legacy"] is True
    assert has_quote_evidence(record)
    assert replace_record(record, end=0) == record
