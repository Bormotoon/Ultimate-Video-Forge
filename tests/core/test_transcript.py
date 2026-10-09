from pathlib import Path

from studio.core.timeline import TimeDomain
from studio.core.transcript import Segment, Transcript, Word, to_srt


def _transcript() -> Transcript:
    return Transcript(
        Path("audio.wav"),
        "ru",
        3.25,
        [
            Segment(0.1, 1.0, (Word("Привет.", 0.1, 1.0, 0.988),), 0.9, -0.1),
            Segment(1.2, 3.25, (Word("Мир", 1.2, 3.25, 0.8),)),
        ],
        model="large-v3",
        device="cuda",
        compute_type="float16",
        mode="fast",
        time_domain=TimeDomain.FILE,
    )


def test_forge_compatible_shape_and_round_trip(tmp_path: Path) -> None:
    transcript = _transcript()
    data = transcript.to_dict()
    assert data["source_audio"] == "audio.wav"
    assert data["segments"][0]["words"][0]["word"] == "Привет."
    assert data["sentences"][0]["text"] == "Привет."
    path = tmp_path / "transcript.json"
    transcript.save(path)
    assert Transcript.load(path) == transcript


def test_legacy_json_without_schema_or_time_domain_is_accepted() -> None:
    data = _transcript().to_dict()
    data.pop("schema_version")
    data.pop("time_domain")
    restored = Transcript.from_dict(data)
    assert restored.time_domain is TimeDomain.FILE


def test_srt_uses_segment_cues() -> None:
    assert to_srt(_transcript()).startswith("1\n00:00:00,100 --> 00:00:01,000\nПривет.")


def test_corrected_segment_round_trips_original_words_for_audit() -> None:
    transcript = _transcript()
    corrected = Transcript(
        transcript.source_audio,
        transcript.language,
        transcript.duration,
        [
            Segment(
                transcript.segments[0].start,
                transcript.segments[0].end,
                transcript.segments[0].words,
                text_override="Здравствуйте.",
                raw_words=transcript.segments[0].words,
            )
        ],
    )
    restored = Transcript.from_dict(corrected.to_dict())
    assert restored.segments[0].text == "Здравствуйте."
    assert restored.segments[0].raw_words == transcript.segments[0].words
