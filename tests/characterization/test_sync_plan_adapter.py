import pytest
from whispersync.config import WhisperSyncConfig
from whispersync.engine.pipeline import clip_pieces
from whispersync.models import AlignmentMap, Anchor

from studio.core.timeline import audio_source_to_rendered, rendered_to_audio_source
from studio.stages.sync_plan import adapt_piece_plan


def test_adapter_trims_actual_nonlinear_hybrid_without_changing_factors() -> None:
    alignment = AlignmentMap(
        anchors=[Anchor(
            cam_time=float(t) if t <= 10 else 10 + (t - 10) * 1.02,
            rec_time=float(t), token=str(t), confidence=0.9,
        ) for t in range(2, 30, 2)], offset=0.0, k=1.01, residual_ms=30.0,
    )
    lead, pieces = clip_pieces(
        alignment, 30.0, 40.0, 3, WhisperSyncConfig(),
        rec_words=[(4.0, 5.0), (5.1, 6.0), (16.0, 17.0), (17.1, 18.0), (24.0, 25.0)],
    )
    plan = adapt_piece_plan(
        pieces, lead_silence_s=lead, duration_s=30.0,
        map_id="hybrid", source_asset_id="recorder", target_asset_id="camera", strategy=3,
    )
    assert plan.warp.rendered_duration_s == pytest.approx(30.0)
    assert plan.tail_silence_s == 0
    assert [piece.factor for piece in plan.warp.pieces] == pytest.approx(
        [factor for _, _, factor in pieces],
    )
    assert plan.warp.pieces[-1].source_duration_s < pieces[-1][1]
    for piece in plan.warp.pieces:
        source_mid = piece.source_start_s + piece.source_duration_s / 2
        rendered = audio_source_to_rendered(source_mid, plan.warp)
        assert rendered is not None
        assert rendered_to_audio_source(rendered, plan.warp) == pytest.approx(source_mid)


def test_adapter_keeps_padding_outside_invertible_map() -> None:
    plan = adapt_piece_plan(
        [(10.0, 2.0, 1.0)], lead_silence_s=0.5, duration_s=3.0,
        map_id="padded", source_asset_id="recorder", target_asset_id="camera", strategy=1,
    )
    assert plan.lead_silence_s == plan.tail_silence_s == 0.5
    assert rendered_to_audio_source(0.25, plan.warp) is None
    assert rendered_to_audio_source(2.75, plan.warp) is None
    assert plan.warp.pieces[0].method == "copy"


@pytest.mark.parametrize("pieces", [
    [(0.0, 1.0, 1.0), (2.0, 1.0, 1.0)],
    [(0.0, 1.0, float("nan"))], [],
])
def test_adapter_rejects_invalid_geometry(pieces: list[tuple[float, float, float]]) -> None:
    with pytest.raises(ValueError):
        adapt_piece_plan(
            pieces, lead_silence_s=0.0, duration_s=3.0,
            map_id="invalid", source_asset_id="recorder", target_asset_id=None, strategy=3,
        )
