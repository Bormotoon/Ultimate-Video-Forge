import pytest

from studio.stages.events import EventType, StageEvent


def test_event_round_trip() -> None:
    event = StageEvent(EventType.PROGRESS, "scan", {"value": 0.5, "message": "half"})
    assert StageEvent.from_json(event.to_json()) == event


@pytest.mark.parametrize(
    "line",
    ["not-json", '{"t":"unknown","stage":"scan"}', '{"t":"progress","stage":"scan","value":2}'],
)
def test_malformed_worker_events_are_rejected(line: str) -> None:
    with pytest.raises(ValueError):
        StageEvent.from_json(line)
