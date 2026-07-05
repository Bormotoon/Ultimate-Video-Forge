"""Tests for the voice-enhancement dispatch (engine/enhance.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from whispersync.engine import enhance
from whispersync.engine.media import MediaInfo


def _info(
    path: Path, duration: float = 2.0, sr: int = 48000, ch: int = 2, bits: int = 24
) -> MediaInfo:
    return MediaInfo(
        path=path,
        duration=duration,
        fps=None,
        width=None,
        height=None,
        video_codec=None,
        audio_codec="pcm_s24le",
        audio_channels=ch,
        audio_sample_rate=sr,
        audio_bits_per_sample=bits,
    )


def test_is_available_off_always_true(tmp_path: Path) -> None:
    assert enhance.is_available("off", tmp_path) is True


def test_is_available_denoise_needs_sep_venv(tmp_path: Path) -> None:
    assert enhance.is_available("denoise", tmp_path) is False
    cli = tmp_path / ".sep-venv" / "bin" / "audio-separator"
    cli.parent.mkdir(parents=True)
    cli.write_text("")
    assert enhance.is_available("denoise", tmp_path) is True
    assert enhance.is_available("denoise_dereverb", tmp_path) is True


def test_is_available_unwired_modes_are_false(tmp_path: Path) -> None:
    for mode in ("resemble", "sgmse_denoise", "sgmse_dereverb", "reuse"):
        assert enhance.is_available(mode, tmp_path) is False


def test_run_batch_off_is_noop(tmp_path: Path) -> None:
    assert enhance.run_batch("off", [tmp_path / "a.wav"], tmp_path, tmp_path) == {}


def test_run_batch_empty_paths_is_noop(tmp_path: Path) -> None:
    assert enhance.run_batch("denoise", [], tmp_path, tmp_path) == {}


def test_run_batch_unknown_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Unknown voice_enhance mode"):
        enhance.run_batch("bogus", [tmp_path / "a.wav"], tmp_path, tmp_path)


def test_run_batch_unwired_mode_raises_not_available(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not available yet"):
        enhance.run_batch("resemble", [tmp_path / "a.wav"], tmp_path, tmp_path)


def test_run_batch_denoise_requires_sep_venv(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="sep-venv"):
        enhance.run_batch("denoise", [tmp_path / "a.wav"], tmp_path, tmp_path)


def test_run_batch_denoise_conforms_each_output(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "voice.wav"
    src.write_bytes(b"RIFF")
    produced = tmp_path / "raw_denoised.wav"

    monkeypatch.setattr(
        enhance.separation,
        "run_separator_batch",
        lambda paths, out_dir, repo_root, model, stem, **kw: {src: produced},
    )
    monkeypatch.setattr(enhance, "probe", lambda p: _info(p, duration=3.5, sr=48000, ch=2, bits=24))

    calls: list[tuple] = []

    def fake_conform(input_path, output_path, duration, sample_rate, channels, codec):
        calls.append((input_path, output_path, duration, sample_rate, channels, codec))
        return output_path

    monkeypatch.setattr(enhance, "conform_wav_to", fake_conform)

    result = enhance.run_batch("denoise", [src], tmp_path, tmp_path)
    assert src in result
    assert calls == [(produced, result[src], 3.5, 48000, 2, "pcm_s24le")]


def test_run_batch_denoise_dereverb_chains_both_passes(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "voice.wav"
    src.write_bytes(b"RIFF")
    dry = tmp_path / "dry.wav"
    dereverbed = tmp_path / "dereverbed.wav"

    calls: list[str] = []

    def fake_run_separator_batch(paths, out_dir, repo_root, model, stem, **kw):
        if stem == enhance._DENOISE_STEM:
            calls.append("denoise")
            return {src: dry}
        calls.append("dereverb")
        assert paths == [dry]
        return {dry: dereverbed}

    monkeypatch.setattr(enhance.separation, "run_separator_batch", fake_run_separator_batch)
    monkeypatch.setattr(enhance, "probe", lambda p: _info(p))
    monkeypatch.setattr(enhance, "conform_wav_to", lambda *a, **k: a[1])

    result = enhance.run_batch("denoise_dereverb", [src], tmp_path, tmp_path)
    assert calls == ["denoise", "dereverb"]
    assert src in result
