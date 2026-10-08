import json
import math
import struct
import wave
from pathlib import Path

import pytest
from whispersync.config import WhisperSyncConfig
from whispersync.engine.pipeline import clip_pieces
from whispersync.engine.timestretch import render_piece
from whispersync.models import AlignmentMap, Anchor


def test_hybrid_absorbs_offset_in_pause_and_preserves_speech_rate() -> None:
    alignment = AlignmentMap(
        anchors=[
            Anchor(cam_time=1.0 + t, rec_time=float(t), token=str(t), confidence=0.9)
            for t in range(2, 40, 2)
        ],
        offset=1.0, k=1.0, residual_ms=0.0,
    )
    lead, pieces = clip_pieces(
        alignment, 40.0, 60.0, 3, WhisperSyncConfig(),
        rec_words=[(5.0, 6.0), (6.1, 7.0), (17.0, 18.0), (18.1, 19.0)],
    )
    cursor = lead
    starts = []
    for _start, duration, factor in pieces:
        starts.append(round(cursor, 9))
        cursor += duration / factor
    actual = {
        "lead": lead,
        "pieces": [[round(value, 9) for value in piece] for piece in pieces],
        "rendered_starts": starts,
        "rendered_duration": round(cursor, 9),
    }
    golden = Path(__file__).parent / "golden" / "hybrid.json"
    assert actual == json.loads(golden.read_text())
    for previous, following in zip(pieces, pieces[1:], strict=False):
        assert previous[0] + previous[1] == pytest.approx(following[0])
    assert starts[1] + 0.08 == pytest.approx(6.0)
    assert starts[3] + 0.08 == pytest.approx(18.0)


@pytest.mark.parametrize("factor,method", [(1.0, "auto"), (1.003, "auto"), (1.3, "atempo")])
def test_rendered_piece_sample_count_and_format(
    tmp_path: Path, factor: float, method: str,
) -> None:
    source = tmp_path / "source.wav"
    rate = 48000
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"".join(
            struct.pack("<hh", sample, -sample)
            for index in range(rate * 3)
            for sample in [round(10000 * math.sin(2 * math.pi * 440 * index / rate))]
        ))
    rendered = render_piece(
        source, tmp_path, rec_start=0.5, rec_dur=2.0, factor=factor, index=0,
        fade_ms=0, sample_rate=rate, channels=2, codec="pcm_s16le", stretch_method=method,
    )
    with wave.open(str(rendered), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (rate, 2, 2)
        assert abs(audio.getnframes() - round(2.0 / factor * rate)) <= 1
        samples = audio.readframes(audio.getnframes())
    assert any(samples)
    if factor == 1.0:
        with wave.open(str(source), "rb") as audio:
            audio.setpos(rate // 2)
            assert samples == audio.readframes(rate * 2)
