from dataclasses import dataclass

import pytest

from studio.core.timeline import (
    AudioWarpMap,
    AudioWarpPiece,
    EditMap,
    KeepRange,
    SourcePlacement,
    TimeDomain,
    audio_source_to_rendered,
    edited_to_timeline,
    file_to_timeline,
    map_words,
    rendered_to_audio_source,
    timeline_to_edited,
    timeline_to_file,
)


def _hybrid_warp() -> AudioWarpMap:
    return AudioWarpMap(
        id="hybrid",
        source_asset_id="recorder",
        target_asset_id="camera",
        source_domain=TimeDomain.FILE,
        strategy=3,
        pieces=(
            AudioWarpPiece(0.0, 10.0, 0.0, 9.99, "resample"),
            AudioWarpPiece(10.0, 4.0, 9.99, 4.02, "atempo"),
            AudioWarpPiece(14.0, 6.0, 14.01, 6.0, "copy"),
        ),
    )


def test_source_placement_round_trip_with_negative_calibration_and_drift() -> None:
    placement = SourcePlacement("rec", offset_s=-0.125, in_s=2.0, duration_s=60.0, k=1.0008)
    timeline = file_to_timeline(37.25, placement)
    assert timeline_to_file(timeline, placement) == pytest.approx(37.25)


def test_piecewise_hybrid_round_trip_is_monotonic() -> None:
    warp = _hybrid_warp()
    rendered = [audio_source_to_rendered(value, warp) for value in (0.0, 5.0, 10.0, 12.0, 20.0)]
    assert rendered == sorted(rendered)  # type: ignore[type-var]
    for source in (0.0, 5.0, 10.0, 12.0, 14.0, 20.0):
        mapped = audio_source_to_rendered(source, warp)
        assert mapped is not None
        assert rendered_to_audio_source(mapped, warp) == pytest.approx(source)


def test_warp_rejects_gaps_and_overlaps_in_both_domains() -> None:
    with pytest.raises(ValueError, match="source pieces"):
        AudioWarpMap(
            id="bad",
            source_asset_id="rec",
            target_asset_id=None,
            source_domain=TimeDomain.FILE,
            strategy=3,
            pieces=(
                AudioWarpPiece(0.0, 2.0, 0.0, 2.0, "copy"),
                AudioWarpPiece(2.1, 1.0, 2.0, 1.0, "copy"),
            ),
        )
    with pytest.raises(ValueError, match="rendered pieces"):
        AudioWarpMap(
            id="bad",
            source_asset_id="rec",
            target_asset_id=None,
            source_domain=TimeDomain.FILE,
            strategy=3,
            pieces=(
                AudioWarpPiece(0.0, 2.0, 0.0, 2.0, "copy"),
                AudioWarpPiece(2.0, 1.0, 1.9, 1.0, "copy"),
            ),
        )


def test_edit_map_round_trip_and_removed_range() -> None:
    edit = EditMap("cut", (KeepRange(2.0, 5.0), KeepRange(8.0, 12.0)))
    assert timeline_to_edited(3.0, edit) == 1.0
    assert timeline_to_edited(7.0, edit) is None
    assert edited_to_timeline(5.0, edit) == 10.0


@dataclass
class _Word:
    start: float
    end: float
    text: str


def test_map_words_drops_words_outside_the_map() -> None:
    edit = EditMap("cut", (KeepRange(2.0, 5.0),))
    words = [_Word(1.0, 1.5, "drop"), _Word(2.5, 3.0, "keep")]
    mapped = map_words(words, lambda value: timeline_to_edited(value, edit))
    assert [(word.text, start, end) for word, start, end in mapped] == [("keep", 0.5, 1.0)]
