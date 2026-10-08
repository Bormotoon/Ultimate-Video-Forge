"""Preserved planning-to-rendering facade for an already aligned camera clip."""

from pathlib import Path

from studio.stages.sync_geometry import AlignmentMap, PieceSettings
from studio.stages.sync_pieces import clip_pieces, recorder_word_gaps
from studio.stages.sync_plan import RenderedAudioPlan
from studio.stages.sync_render import render_audio_plan


def render_aligned_clip(
    alignment: AlignmentMap, settings: PieceSettings, source: Path, output: Path,
    *, clip_duration_s: float, recorder_duration_s: float,
    recorder_words: list[tuple[float, float]], strategy: int,
    source_asset_id: str, target_asset_id: str,
    sample_rate: int = 48000, channels: int = 1, codec: str = "pcm_s24le",
    fade_ms: int = 10, stretch_method: str = "auto",
) -> RenderedAudioPlan:
    if strategy not in {1, 2, 3}:
        raise ValueError("synchronization strategy must be 1, 2, or 3")
    lead, pieces = clip_pieces(
        alignment, clip_duration_s, recorder_duration_s, strategy, settings,
        rec_word_gaps=recorder_word_gaps(recorder_words), rec_words=recorder_words,
    )
    if any(start < 0 or start + duration > recorder_duration_s + 1e-6
           for start, duration, _factor in pieces):
        raise ValueError("piece plan reads outside the recorder")
    return render_audio_plan(
        source, output, pieces, lead_silence_s=lead, duration_s=clip_duration_s,
        map_id=f"warp-{source_asset_id}-{target_asset_id}",
        source_asset_id=source_asset_id, target_asset_id=target_asset_id, strategy=strategy,
        sample_rate=sample_rate, channels=channels, codec=codec, fade_ms=fade_ms,
        stretch_method=stretch_method,
    )
