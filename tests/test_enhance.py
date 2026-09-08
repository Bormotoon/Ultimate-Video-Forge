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
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    assert enhance.is_available("denoise", tmp_path) is True
    assert enhance.is_available("denoise_dereverb", tmp_path) is True


def test_is_available_unwired_modes_are_false(tmp_path: Path) -> None:
    for mode in ("sgmse_denoise", "sgmse_dereverb", "reuse"):
        assert enhance.is_available(mode, tmp_path) is False
        assert mode not in enhance.IMPLEMENTED_MODES


def _install_fake_sep_venv(tmp_path: Path, resemble: bool = False) -> None:
    bin_dir = tmp_path / ".sep-venv" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("audio-separator", "python"):
        entry = bin_dir / name
        # Executable, because availability now requires it: a non-executable
        # placeholder used to count as a working backend.
        entry.write_text("#!/bin/sh\n")
        entry.chmod(0o755)
    if resemble:
        pkg = tmp_path / ".sep-venv" / "lib" / "python3.12" / "site-packages" / "resemble_enhance"
        pkg.mkdir(parents=True)


def test_is_available_resemble_needs_its_package(tmp_path: Path) -> None:
    # Installed into .sep-venv, but it's a different tool from audio-separator:
    # having the separator alone must NOT report resemble as available.
    _install_fake_sep_venv(tmp_path)
    assert enhance.is_available("resemble", tmp_path) is False
    _install_fake_sep_venv(tmp_path, resemble=True)
    assert enhance.is_available("resemble", tmp_path) is True


def test_unavailable_reason_names_the_fix(tmp_path: Path) -> None:
    assert enhance.unavailable_reason("off", tmp_path) is None
    assert "setup_sep_venv.sh" in (enhance.unavailable_reason("denoise", tmp_path) or "")
    assert "resemble-enhance" in (enhance.unavailable_reason("resemble", tmp_path) or "")
    assert "not available yet" in (enhance.unavailable_reason("reuse", tmp_path) or "")
    assert "Unknown" in (enhance.unavailable_reason("bogus", tmp_path) or "")


def test_run_batch_off_is_noop(tmp_path: Path) -> None:
    assert enhance.run_batch("off", [tmp_path / "a.wav"], tmp_path, tmp_path) == {}


def test_run_batch_empty_paths_is_noop(tmp_path: Path) -> None:
    assert enhance.run_batch("denoise", [], tmp_path, tmp_path) == {}


def test_run_batch_unknown_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="Unknown voice_enhance mode"):
        enhance.run_batch("bogus", [tmp_path / "a.wav"], tmp_path, tmp_path)


def test_run_batch_unwired_mode_raises_not_available(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="not available yet"):
        enhance.run_batch("sgmse_denoise", [tmp_path / "a.wav"], tmp_path, tmp_path)


def test_run_batch_resemble_requires_its_cli(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="resemble-enhance"):
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


# --- resemble ---------------------------------------------------------------


def _fake_resemble(monkeypatch, tmp_path: Path, produce: bool = True):
    """Stand in for the .sep-venv runner process: mirror in_dir -> out_dir."""
    _install_fake_sep_venv(tmp_path, resemble=True)
    seen: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        seen.append(cmd)
        in_dir, out_dir = Path(cmd[2]), Path(cmd[3])  # python runner in_dir out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        if produce:
            for f in in_dir.iterdir():
                (out_dir / f.name).write_bytes(b"RIFF")
        return _Proc()

    monkeypatch.setattr(enhance.subprocess, "run", fake_run)
    monkeypatch.setattr(enhance, "probe", lambda p: _info(p))
    monkeypatch.setattr(enhance, "conform_wav_to", lambda *a, **k: a[1])
    return seen


def test_run_batch_resemble_maps_outputs_back_to_inputs(tmp_path: Path, monkeypatch) -> None:
    # Two clips from different cameras can share a filename — the staged
    # names must still map each output back to the right original.
    a = tmp_path / "camA" / "DJI_0001.wav"
    b = tmp_path / "camB" / "DJI_0001.wav"
    for p in (a, b):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF")

    seen = _fake_resemble(monkeypatch, tmp_path)
    result = enhance.run_batch("resemble", [a, b], tmp_path / "work", tmp_path)

    assert set(result) == {a, b}
    assert result[a] != result[b]
    assert len(seen) == 1  # one process for the whole batch, not one per clip


def test_run_batch_resemble_no_output_raises(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "voice.wav"
    src.write_bytes(b"RIFF")
    _fake_resemble(monkeypatch, tmp_path, produce=False)
    with pytest.raises(RuntimeError, match="produced no output"):
        enhance.run_batch("resemble", [src], tmp_path / "work", tmp_path)


def test_resemble_staging_links_actually_open(tmp_path: Path, monkeypatch) -> None:
    """Staged inputs must be READABLE, not merely present.

    Symlinking a RELATIVE source path stores that text verbatim, so a link in
    enhance_tmp/resemble_in pointing at "output/audio_synced/a.wav" resolves
    against the LINK's own directory and lands on nothing. Creating a dangling
    symlink succeeds, so the copy fallback never fired and the runner received
    a directory of broken links — while every existence check passed.
    """
    import os

    src_dir = tmp_path / "output" / "audio_synced"
    src_dir.mkdir(parents=True)
    src = src_dir / "a.wav"
    src.write_bytes(b"RIFF" + b"\0" * 1024)

    opened: list[bytes] = []
    _install_fake_sep_venv(tmp_path, resemble=True)

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        in_dir, out_dir = Path(cmd[2]), Path(cmd[3])
        out_dir.mkdir(parents=True, exist_ok=True)
        for staged in sorted(in_dir.glob("*.wav")):
            # THE assertion: the runner must be able to read what it was given.
            opened.append(staged.read_bytes())
            (out_dir / staged.name).write_bytes(b"RIFF")
        return _Proc()

    monkeypatch.setattr(enhance.subprocess, "run", fake_run)
    monkeypatch.setattr(enhance, "probe", lambda p: _info(p))
    monkeypatch.setattr(enhance, "conform_wav_to", lambda *a, **k: a[1])

    # Run from a directory that is NOT the source's parent, so a relative
    # symlink target would resolve to nothing.
    cwd = os.getcwd()
    os.chdir(tmp_path / "output")
    try:
        relative_src = Path(os.path.relpath(src))
        got = enhance._resemble_batch([relative_src], tmp_path / "work", tmp_path)
    finally:
        os.chdir(cwd)
    assert got
    assert opened and opened[0].startswith(b"RIFF")


def test_resemble_timeout_becomes_a_runtime_error(tmp_path: Path, monkeypatch) -> None:
    """A slow OPTIONAL stage must degrade, not destroy the run: TimeoutExpired
    is a SubprocessError and slipped past every caller's fallback."""
    import subprocess

    src = tmp_path / "a.wav"
    src.write_bytes(b"RIFF" + b"\0" * 1024)
    _install_fake_sep_venv(tmp_path, resemble=True)

    def timeout_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(enhance.subprocess, "run", timeout_run)
    monkeypatch.setattr(enhance, "probe", lambda p: _info(p))
    with pytest.raises(RuntimeError, match="timed out"):
        enhance._resemble_batch([src], tmp_path / "out", tmp_path)
