import json
import math
import struct
import wave
from pathlib import Path

import numpy as np
import pytest
from whispersync.config import WhisperSyncConfig
from whispersync.engine import acoustic
from whispersync.engine.pipeline import clip_pieces
from whispersync.engine.timestretch import assemble_continuous, render_piece
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


def test_hybrid_follows_nonlinear_clock_without_source_gaps() -> None:
    alignment = AlignmentMap(
        anchors=[
            Anchor(
                cam_time=float(t) if t <= 10 else 10 + (t - 10) * 1.02,
                rec_time=float(t), token=str(t), confidence=0.9,
            ) for t in range(2, 30, 2)
        ], offset=0.0, k=1.01, residual_ms=30.0,
    )
    lead, pieces = clip_pieces(
        alignment, 30.0, 40.0, 3, WhisperSyncConfig(),
        rec_words=[(4.0, 5.0), (5.1, 6.0), (16.0, 17.0), (17.1, 18.0), (24.0, 25.0)],
    )
    assert lead == pytest.approx(0.0)
    assert [round(start, 2) for start, _, _ in pieces] == [
        0, 3.92, 6.08, 15.92, 18.08, 23.92, 25.08,
    ]
    assert [factor for _, _, factor in pieces] == pytest.approx(
        [1.005789, 0.988422, 0.986446, 0.984577, 0.982726, 0.982245, 0.990099], abs=1e-6,
    )
    # The frozen planner overshoots; assembly trims the final WAV to clip length.
    assert lead + sum(duration / factor for _, duration, factor in pieces) == pytest.approx(
        30.044594672, abs=1e-9,
    )
    for previous, following in zip(pieces, pieces[1:], strict=False):
        assert previous[0] + previous[1] == pytest.approx(following[0])


@pytest.mark.parametrize("lag", [0.08, -0.08])
def test_boundary_flex_moves_speech_in_measured_direction(
    monkeypatch: pytest.MonkeyPatch, lag: float,
) -> None:
    monkeypatch.setattr(acoustic, "load_mono16k_track", lambda _path: np.zeros(120 * 16000))
    monkeypatch.setattr(acoustic, "gcc_phat", lambda *_args: (lag, 200.0))
    lead, pieces = acoustic.refine_piece_boundaries(
        [(100.0, 5.0, 1.0), (105.0, 5.0, 1.0), (110.0, 5.0, 1.0)],
        0.0, Path("camera"), Path("recorder"), 15.0, 120.0,
        WhisperSyncConfig(boundary_flex=True, flex_min_sharpness=80.0, flex_max_shift_s=0.15),
    )
    cursor = lead
    event = None
    for start, duration, factor in pieces:
        if start <= 107.0 < start + duration:
            event = cursor + (107.0 - start) / factor
        cursor += duration / factor
    assert event == pytest.approx(7.0 + lag, abs=0.005)
    assert cursor == pytest.approx(15.0)
    for previous, following in zip(pieces, pieces[1:], strict=False):
        assert previous[0] + previous[1] == pytest.approx(following[0])


def test_assembly_preserves_order_lead_and_tail_samples(tmp_path: Path) -> None:
    rate = 48000
    paths = []
    for index, value in enumerate([1000, -2000]):
        path = tmp_path / f"piece-{index}.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((2, 2, rate, 0, "NONE", "not compressed"))
            audio.writeframes(struct.pack("<hh", value, -value) * (rate // 2))
        paths.append(path)
    output = assemble_continuous(
        paths, 0.25, 1.5, rate, tmp_path / "assembled.wav", channels=2, codec="pcm_s16le",
    )
    with wave.open(str(output), "rb") as audio:
        assert audio.getnframes() == round(rate * 1.5)
        samples = audio.readframes(audio.getnframes())
    expected = (
        b"\0" * (rate // 4 * 4)
        + struct.pack("<hh", 1000, -1000) * (rate // 2)
        + struct.pack("<hh", -2000, 2000) * (rate // 2)
        + b"\0" * (rate // 4 * 4)
    )
    assert samples == expected
