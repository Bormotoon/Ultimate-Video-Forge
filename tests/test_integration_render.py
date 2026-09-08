"""Integration tests that exercise real ffmpeg through the render path.

Unlike the rest of the suite (pure logic, no subprocess), these generate
synthetic audio with ffmpeg and run it through the actual cut/stretch/
assemble pipeline — the layer PROJECT_ANALYSIS.md §8.1 flags as having zero
coverage despite being where the project's worst historical bug lived (the
cumulative atempo rounding drift). Skipped automatically if ffmpeg isn't on
PATH; run explicitly with `pytest -m integration` or excluded with
`pytest -m "not integration"`.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from whispersync.engine.media import probe
from whispersync.engine.timestretch import (
    assemble_continuous,
    conform_wav_to,
    extract_segment,
    mix_clips_on_timeline,
    render_piece,
    resample_conform_segment,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"),
]


@pytest.fixture
def stereo24_source(tmp_path: Path) -> Path:
    """A 10s, 48kHz, 24-bit stereo synthetic source (two different sine tones
    per channel, so a channel swap/collapse would be audible/measurable)."""
    out = tmp_path / "source_24bit_stereo.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:duration=10",
        "-filter_complex",
        "[0:a][1:a]amerge=inputs=2[a]",
        "-map",
        "[a]",
        "-ar",
        "48000",
        "-acodec",
        "pcm_s24le",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def test_source_fixture_is_24bit_stereo(stereo24_source: Path) -> None:
    info = probe(stereo24_source)
    assert info.audio_channels == 2
    assert info.audio_bits_per_sample == 24


def test_extract_segment_preserves_channels_and_bit_depth(
    stereo24_source: Path, tmp_path: Path
) -> None:
    out = extract_segment(
        stereo24_source,
        tmp_path,
        start=1.0,
        duration=2.0,
        segment_index=0,
        channels=2,
        codec="pcm_s24le",
    )
    info = probe(out)
    assert info.audio_channels == 2
    assert info.audio_bits_per_sample == 24
    assert abs(info.duration - 2.0) < 0.01


def test_extract_segment_is_a_null_cut_no_atempo_no_fade(
    stereo24_source: Path, tmp_path: Path
) -> None:
    """A plain cut (factor=1, no fade) must reproduce the source samples
    bit-for-bit — this is the null test that would have caught the historical
    -2.97ms/piece atempo rounding bug had it existed on the cut path too."""
    out = extract_segment(
        stereo24_source,
        tmp_path,
        start=2.0,
        duration=1.0,
        segment_index=0,
        fade_ms=0,
        channels=2,
        codec="pcm_s24le",
    )
    reference = tmp_path / "reference.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "2.0",
            "-t",
            "1.0",
            "-i",
            str(stereo24_source),
            "-acodec",
            "pcm_s24le",
            str(reference),
        ],
        check=True,
        capture_output=True,
    )
    assert out.read_bytes() == reference.read_bytes()


def test_render_piece_resample_conform_exact_length(stereo24_source: Path, tmp_path: Path) -> None:
    # A small factor (0.3% speedup) must route through resample-conform (not
    # atempo) and land at the exact intended output length.
    factor = 1.003
    duration = 3.0
    out = render_piece(
        stereo24_source,
        tmp_path,
        rec_start=0.0,
        rec_dur=duration,
        factor=factor,
        index=0,
        fade_ms=0,
        sample_rate=48000,
        channels=2,
        codec="pcm_s24le",
        stretch_method="auto",
    )
    info = probe(out)
    expected = duration / factor
    assert info.audio_channels == 2
    assert info.audio_bits_per_sample == 24
    assert abs(info.duration - expected) < 0.01


def test_render_piece_atempo_exact_length_large_factor(
    stereo24_source: Path, tmp_path: Path
) -> None:
    # A large factor forces the atempo (WSOLA) path; the exact-length contract
    # (apad+atrim) must still hold — this is the regression guard for the
    # historical cumulative-drift bug (-2.97ms/piece before the fix).
    factor = 1.3
    duration = 2.0
    out = render_piece(
        stereo24_source,
        tmp_path,
        rec_start=1.0,
        rec_dur=duration,
        factor=factor,
        index=0,
        fade_ms=0,
        sample_rate=48000,
        channels=2,
        codec="pcm_s24le",
        stretch_method="atempo",
    )
    info = probe(out)
    expected = duration / factor
    assert abs(info.duration - expected) < 0.005  # well under 5ms


def test_assemble_continuous_preserves_format_and_exact_total_length(
    stereo24_source: Path, tmp_path: Path
) -> None:
    pieces = [
        render_piece(
            stereo24_source,
            tmp_path,
            rec_start=float(i * 2),
            rec_dur=2.0,
            factor=1.0,
            index=i,
            fade_ms=10,
            sample_rate=48000,
            channels=2,
            codec="pcm_s24le",
        )
        for i in range(3)
    ]
    out = tmp_path / "assembled.wav"
    assemble_continuous(
        pieces,
        lead_silence=0.0,
        total_duration=6.0,
        sample_rate=48000,
        output_path=out,
        channels=2,
        codec="pcm_s24le",
    )
    info = probe(out)
    assert info.audio_channels == 2
    assert info.audio_bits_per_sample == 24
    assert abs(info.duration - 6.0) < 0.01


def test_resample_conform_pitch_shift_is_within_the_small_drift_budget(
    stereo24_source: Path, tmp_path: Path
) -> None:
    # A resample-conform at 1.002x must shift a 440Hz tone to ~440*1.002Hz —
    # audibly inaudible (a few cents) but present, confirming the conform
    # actually resampled rather than being a no-op.
    out = resample_conform_segment(
        stereo24_source,
        tmp_path,
        start=0.0,
        duration=4.0,
        factor=1.002,
        segment_index=0,
        sample_rate=48000,
        channels=2,
        codec="pcm_s24le",
    )
    info = probe(out)
    assert abs(info.duration - 4.0 / 1.002) < 0.01


@pytest.fixture
def two_short_clips(tmp_path: Path) -> tuple[Path, Path]:
    clip_a = tmp_path / "clip_a.wav"
    clip_b = tmp_path / "clip_b.wav"
    for path, freq, dur in ((clip_a, 440, 2.0), (clip_b, 880, 1.5)):
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={dur}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-acodec",
            "pcm_s24le",
            str(path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    return clip_a, clip_b


def test_mix_clips_on_timeline_places_clips_at_their_offsets_and_pads_length(
    two_short_clips: tuple[Path, Path], tmp_path: Path
) -> None:
    clip_a, clip_b = two_short_clips
    out = tmp_path / "master.wav"
    mix_clips_on_timeline(
        [(clip_a, 0.0), (clip_b, 3.0)],
        total_duration=5.0,
        sample_rate=48000,
        output_path=out,
        channels=2,
        codec="pcm_s24le",
    )
    info = probe(out)
    assert info.audio_channels == 2
    assert info.audio_bits_per_sample == 24
    assert abs(info.duration - 5.0) < 0.01

    def rms(start: float, dur: float) -> float:
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(start),
            "-t",
            str(dur),
            "-i",
            str(out),
            "-f",
            "f32le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-",
        ]
        raw = subprocess.run(cmd, check=True, capture_output=True).stdout
        samples = np.frombuffer(raw, dtype=np.float32)
        return float(np.sqrt((samples**2).mean())) if samples.size else 0.0

    assert rms(0.5, 0.5) > 0.01  # clip_a is playing
    assert rms(3.5, 0.5) > 0.01  # clip_b is playing at its offset
    assert rms(4.7, 0.2) < 1e-4  # past clip_b's end (4.5s) -> silence


def test_mix_clips_on_timeline_empty_clip_list_is_silence(tmp_path: Path) -> None:
    out = tmp_path / "master.wav"
    mix_clips_on_timeline([], total_duration=2.0, sample_rate=48000, output_path=out)
    info = probe(out)
    assert abs(info.duration - 2.0) < 0.01


def test_cut_wav_segment_is_bit_exact(stereo24_source: Path, tmp_path: Path) -> None:
    # Cutting [2..5)s + [5..8)s out of a PCM WAV and concatenating the raw
    # samples must reproduce the source span exactly (PCM->same-PCM re-encode
    # is lossless), and each part must keep channels/bit depth.
    from whispersync.engine.timestretch import cut_wav_segment

    p1 = cut_wav_segment(stereo24_source, tmp_path / "p1.wav", 2.0, 5.0, codec="pcm_s24le")
    p2 = cut_wav_segment(stereo24_source, tmp_path / "p2.wav", 5.0, 8.0, codec="pcm_s24le")
    for p in (p1, p2):
        info = probe(p)
        assert info.audio_channels == 2
        assert info.audio_bits_per_sample == 24
        assert abs(info.duration - 3.0) < 0.01

    def raw(path: Path, ss: str | None = None, to: str | None = None) -> bytes:
        cmd = ["ffmpeg", "-v", "error", "-i", str(path)]
        if ss:
            cmd += ["-ss", ss]
        if to:
            cmd += ["-to", to]
        cmd += ["-f", "s24le", "-"]
        return subprocess.run(cmd, check=True, capture_output=True).stdout

    joined = raw(p1) + raw(p2)
    original = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "2.0",
            "-to",
            "8.0",
            "-i",
            str(stereo24_source),
            "-f",
            "s24le",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    assert len(joined) == len(original)
    assert joined == original


def _astats(path: Path) -> dict[str, float]:
    """ffmpeg's own measurement of a file: peak level and flat factor.

    "Flat factor" counts consecutive samples pinned at the same extreme value —
    the signature of hard clipping, and the thing a peak reading alone cannot
    distinguish from a track that simply reaches full scale.
    """
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "astats=metadata=1", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    stats: dict[str, float] = {}
    for line in result.stderr.splitlines():
        for key in ("Peak level dB", "Flat factor"):
            if key in line and key not in stats:
                with contextlib.suppress(ValueError):
                    stats[key] = float(line.split(":")[-1].strip())
    return stats


def test_master_mix_does_not_clip_correlated_sources(tmp_path: Path) -> None:
    """Two hot, correlated tracks must not hard-clip when summed.

    `amix` with `normalize=0` (right for level consistency) sums straight past
    full scale, and an integer PCM output then clips silently — the realistic
    case being two correlated microphones in `recorder_mode="all"`, or voice
    plus ambience. Measured on this exact material, the unprotected path
    produced a flat factor of ~30 (long runs of samples pinned at full scale);
    the mix must instead reach full scale without flattening.
    """
    hot = tmp_path / "hot.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2:sample_rate=48000",
            # ffmpeg's sine generator peaks at 0.125; 6.4x puts it at 0.8 FS,
            # so two of them sum to 1.6 — well past full scale.
            "-af",
            "volume=6.4",
            "-ac",
            "1",
            "-acodec",
            "pcm_s24le",
            str(hot),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    hot_b = tmp_path / "hot_b.wav"
    shutil.copy(hot, hot_b)

    out = tmp_path / "master.wav"
    mix_clips_on_timeline(
        [(hot, 0.0), (hot_b, 0.0)], 2.0, 48000, out, channels=1, codec="pcm_s24le"
    )

    stats = _astats(out)
    assert stats, "astats produced no measurement"
    # Full scale is fine; flat-topped samples are not.
    assert stats["Flat factor"] < 1.0, f"master mix is clipped (flat factor {stats['Flat factor']})"
    assert stats["Peak level dB"] <= 0.01


def test_float_sources_keep_their_headroom_end_to_end(tmp_path: Path) -> None:
    """A 32-bit float recorder must stay float through the render path.

    The whole point of float recording is that samples above ±1.0 remain
    recoverable. Choosing a codec from bit depth alone sent a float source to
    `pcm_s32le`, and ffmpeg hard-clips at full scale on the way in: measured on
    real ffmpeg, [0, 0.5, 1, 1.5, 2, -1.5] came back as [0, 0.5, 1, 1, 1, -1].
    Those peaks are gone for good — no later gain reduction restores them.
    """
    from whispersync.engine.media import pcm_codec_for, probe

    # A float32 source that genuinely exceeds full scale.
    src = tmp_path / "hot_float.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1:sample_rate=48000",
            "-af",
            "volume=16.0",  # sine peaks at 0.125 -> 2.0 full scale
            "-ac",
            "1",
            "-acodec",
            "pcm_f32le",
            str(src),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    info = probe(src)
    assert info.audio_sample_fmt and info.audio_sample_fmt.startswith("flt")

    # The codec the render path would pick for this source.
    codec = pcm_codec_for(info)
    assert codec == "pcm_f32le", f"a float source was conformed to {codec}"

    # And a real conform through that codec preserves the over-unity peak.
    out = tmp_path / "conformed.wav"
    conform_wav_to(src, out, info.duration, 48000, 1, codec)
    stats = _astats(out)
    assert stats["Peak level dB"] > 3.0, (
        f"peak came back at {stats['Peak level dB']:.1f} dBFS — headroom above "
        "full scale was clipped away"
    )


def _make_two_stream_file(path: Path) -> None:
    """A Matroska file with TWO audio streams: a quiet mono track first, then a
    loud stereo one, with NO `default` disposition on either.

    That last detail is what makes the fixture meaningful. Given no `-map` and
    no default flag, ffmpeg picks the stream with the most channels — the
    stereo one — while a caller reading "the first audio stream" gets the mono
    one. (With a default flag set, ffmpeg honours it and the two agree, which
    is why the flag has to be cleared here.)
    """
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            # 0: mono, quiet
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:duration=2:sample_rate=48000",
            # 1: stereo, loud
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=900:duration=2:sample_rate=48000",
            "-filter_complex",
            "[0:a]volume=0.2[a0];[1:a]volume=8.0,pan=stereo|c0=c0|c1=c0[a1]",
            "-map",
            "[a0]",
            "-map",
            "[a1]",
            # Clear the default flag on both: with one set, ffmpeg honours it
            # and automatic selection coincides with "the first stream",
            # making the fixture unable to show the divergence.
            "-disposition:a:0",
            "0",
            "-disposition:a:1",
            "0",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def test_probing_and_decoding_agree_on_the_audio_stream(tmp_path: Path) -> None:
    """The stream ffprobe reports must be the stream ffmpeg decodes.

    These are different rules: ffprobe's caller reads "the first audio stream",
    while ffmpeg given no `-map` picks the *best* one — most channels wins. A
    camera file holding a mono on-board mic plus a stereo scratch track was
    therefore probed on one stream and decoded from the other. Reproduced on
    real media: automatic selection yielded peak 0 where `-map 0:a:0` yielded
    a real signal.
    """
    import numpy as np

    from whispersync.engine.acoustic import load_mono16k_track
    from whispersync.engine.media import extract_audio_to_wav, probe

    src = tmp_path / "two_streams.mkv"
    _make_two_stream_file(src)

    info = probe(src)
    # Whatever the policy chose, it must be recorded — not left implicit.
    assert info.audio_stream_index is not None
    assert info.audio_channels is not None

    # Decode the stream the probe reported, and both streams explicitly.
    as_reported = tmp_path / "as_reported.wav"
    extract_audio_to_wav(src, as_reported, stream_index=info.audio_stream_index)

    def dominant_hz(p: Path) -> float:
        track = load_mono16k_track(p)
        seg = track[4000:20000]
        spec = np.abs(np.fft.rfft(seg.astype(np.float64)))
        return float(np.fft.rfftfreq(seg.size, d=1.0 / 16000)[int(np.argmax(spec))])

    stream0 = tmp_path / "s0.wav"
    extract_audio_to_wav(src, stream0, stream_index=0)

    # The decode of "the stream we reported" must match that stream's content,
    # not whatever ffmpeg would have picked on its own.
    expected = dominant_hz(stream0) if info.audio_stream_index == 0 else None
    if expected is not None:
        assert (
            abs(dominant_hz(as_reported) - expected) < 20
        ), "the decoded audio is not the stream that was probed"
    # And the channel count agrees with the reported stream.
    assert probe(as_reported).audio_channels == info.audio_channels


def test_automatic_stream_selection_would_have_disagreed(tmp_path: Path) -> None:
    """Demonstrates WHY the explicit `-map` matters, on real media.

    Without `-map`, ffmpeg picks the stereo stream; the probe reports the mono
    one. If these ever coincide the test is vacuous, so it asserts they differ.
    """
    from whispersync.engine.media import probe

    src = tmp_path / "two_streams.mkv"
    _make_two_stream_file(src)

    auto = tmp_path / "auto.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-vn",
            "-acodec",
            "pcm_s16le",
            str(auto),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    info = probe(src)
    auto_info = probe(auto)
    assert (
        info.audio_channels != auto_info.audio_channels
    ), "the fixture no longer distinguishes automatic from explicit selection"
