"""Tests for the ambience-separation helper (path logic, output naming)."""

from __future__ import annotations

from pathlib import Path

from whispersync.engine import separation


def _fake_cli(tmp_path: Path) -> Path:
    """A stand-in separator entry point that is actually EXECUTABLE.

    Availability now requires the executable bit, because `exists()` accepted a
    non-executable placeholder as a working backend.
    """
    cli = tmp_path / ".sep-venv" / "bin" / "audio-separator"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    return cli


def test_separator_absent(tmp_path: Path) -> None:
    assert separation.separator_cli(tmp_path) is None
    assert separation.is_available(tmp_path) is False


def test_separator_present(tmp_path: Path) -> None:
    cli = _fake_cli(tmp_path)
    assert separation.separator_cli(tmp_path) == cli
    assert separation.is_available(tmp_path) is True


def test_non_executable_entrypoint_is_not_available(tmp_path: Path) -> None:
    """A file existing where an executable should be is not a working backend.

    Availability used to be `path.exists()`, so an empty, non-executable
    placeholder counted as "the separator is set up" — and the real failure
    surfaced much later as a confusing subprocess error.
    """
    cli = tmp_path / ".sep-venv" / "bin" / "audio-separator"
    cli.parent.mkdir(parents=True)
    cli.write_text("")
    cli.chmod(0o644)
    assert separation.separator_cli(tmp_path) is None
    assert separation.is_available(tmp_path) is False


def test_explicit_venv_path_wins(tmp_path: Path, monkeypatch) -> None:
    """An installed WhisperSync lives in site-packages, where no `.sep-venv`
    will ever be — the user must be able to say where theirs is."""
    elsewhere = tmp_path / "custom-venv"
    cli = elsewhere / "bin" / "audio-separator"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setenv(separation.SEP_VENV_ENV_VAR, str(elsewhere))
    assert separation.separator_cli(tmp_path / "unrelated-repo") == cli


def test_expected_output_name(tmp_path: Path) -> None:
    out = separation._expected_output(
        tmp_path, Path("/x/DJI_0829.wav"), "melband_roformer_inst_v2.ckpt"
    )
    assert out.name == "DJI_0829_(Instrumental)_melband_roformer_inst_v2.wav"


def test_extract_requires_venv(tmp_path: Path) -> None:
    # No .sep-venv under repo_root → clear error, no subprocess attempted.
    import pytest

    with pytest.raises(RuntimeError, match="sep-venv"):
        separation.extract_ambience(tmp_path / "cam.wav", tmp_path / "out", tmp_path, "model.ckpt")


def test_extract_batch_requires_venv(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(RuntimeError, match="sep-venv"):
        separation.extract_ambience_batch(
            [tmp_path / "a.wav", tmp_path / "b.wav"], tmp_path / "out", tmp_path, "model.ckpt"
        )


def test_extract_batch_empty_input_is_noop(tmp_path: Path) -> None:
    # No .sep-venv either, but an empty batch must short-circuit before
    # checking for it — nothing to separate, nothing to fail on.
    assert separation.extract_ambience_batch([], tmp_path / "out", tmp_path, "model.ckpt") == {}


# --- _find_output: matching separator outputs back to inputs ------------------


def _touch_wav(d: Path, name: str) -> Path:
    p = d / name
    p.write_bytes(b"RIFF")
    return p


def test_find_output_exact_name(tmp_path: Path) -> None:
    out = _touch_wav(tmp_path, "clip_(Instrumental)_melband_roformer_inst_v2.wav")
    got = separation._find_output(tmp_path, Path("/x/clip.wav"), "melband_roformer_inst_v2.ckpt")
    assert got == out


def test_find_output_trailing_underscore_stripped_by_separator(tmp_path: Path) -> None:
    # Field failure: input "tmpsj40fum_.wav" produced
    # "tmpsj40fum_(Instrumental)_..." — the separator swallowed the trailing
    # underscore, so the exact/glob prediction never matched.
    out = _touch_wav(tmp_path, "tmpsj40fum_(Instrumental)_melband_roformer_inst_v2.wav")
    got = separation._find_output(
        tmp_path, Path("/x/tmpsj40fum_.wav"), "melband_roformer_inst_v2.ckpt"
    )
    assert got == out


def test_find_output_prefix_stems_do_not_cross_match(tmp_path: Path) -> None:
    # "take1" must not claim take10's output even though it's a name prefix.
    _touch_wav(tmp_path, "take10_(Instrumental)_model.wav")
    assert separation._find_output(tmp_path, Path("/x/take1.wav"), "model.ckpt") is None


def test_find_output_missing_returns_none(tmp_path: Path) -> None:
    assert separation._find_output(tmp_path, Path("/x/none.wav"), "model.ckpt") is None


def test_sanitize_base_matches_audio_separator_rules(tmp_path: Path) -> None:
    # Same three rules as CommonSeparator.sanitize_filename: invalid path
    # characters -> "_", runs of "_" collapsed, leading/trailing "_. " stripped.
    assert separation._sanitize_base("a:b") == "a_b"
    assert separation._sanitize_base("a__b") == "a_b"
    assert separation._sanitize_base("_clip_.") == "clip"
    out = _touch_wav(tmp_path, "a_b_(Instrumental)_model.wav")
    assert separation._find_output(tmp_path, Path("/x/a__b.wav"), "model.ckpt") == out


# --- run_separator_batch: partial failures, short inputs -----------------------


class _Proc:
    def __init__(self, stderr: str = "") -> None:
        self.returncode = 0
        self.stdout = ""
        self.stderr = stderr


# Separator outputs must survive the usability check before they are accepted,
# so fakes write a plausibly sized file and `probe` is stubbed to report a real
# duration. Both are part of what makes an output *this run's*, so tests that
# skip them would not be exercising the real path.
_WAV_BYTES = b"RIFF" + b"\0" * 4096


def _stub_probe(monkeypatch, duration: float = 12.0) -> None:
    monkeypatch.setattr(
        separation,
        "probe",
        lambda p, **kw: type(
            "I",
            (),
            {
                "duration": duration,
                "audio_sample_rate": 48000,
                "audio_channels": 2,
                "audio_bits_per_sample": 16,
                "audio_sample_fmt": "s16",
            },
        )(),
    )


def _cmd_output_dir(cmd: list[str]) -> Path:
    """The directory the separator was TOLD to write to.

    Fakes honour this rather than a directory captured in the closure: the
    batch now runs into a private per-run directory and publishes verified
    files afterwards, and a fake writing somewhere else would silently test
    nothing.
    """
    return Path(cmd[cmd.index("--output_dir") + 1])


def _install_fake_separator(monkeypatch, out_dir: Path, skip: set[str], stderr: str = ""):
    """Fake CLI run that writes an output for every input except ``skip``."""
    runs: list[list[str]] = []

    def fake_run(cmd, **kw):
        if cmd[0].endswith("ffmpeg"):  # padding call
            Path(cmd[-1]).write_bytes(_WAV_BYTES)
            return _Proc()
        runs.append(cmd)
        target = _cmd_output_dir(cmd)
        target.mkdir(parents=True, exist_ok=True)
        for arg in cmd[1:]:
            if not arg.endswith(".wav"):
                continue
            stem = Path(arg).stem
            if stem in skip:
                continue
            (target / f"{stem}_(Instrumental)_model.wav").write_bytes(_WAV_BYTES)
        return _Proc(stderr)

    monkeypatch.setattr(separation.subprocess, "run", fake_run)
    return runs


def test_batch_keeps_the_clips_that_worked(tmp_path: Path, monkeypatch) -> None:
    # The field failure this guards against: one unprocessable clip out of 71
    # used to raise and throw away the ambience for ALL of them.
    _fake_cli(tmp_path)
    out_dir = tmp_path / "out"
    inputs = [tmp_path / f"DJI_{i}.wav" for i in range(3)]
    for p in inputs:
        p.write_bytes(b"RIFF")
    monkeypatch.setattr(separation, "_pad_to_minimum", lambda src, work: None)
    _stub_probe(monkeypatch)
    _install_fake_separator(
        monkeypatch,
        out_dir,
        skip={"DJI_1"},
        stderr="ERROR - separator - Failed to process file DJI_1.wav: tensor size mismatch",
    )

    got = separation.run_separator_batch(inputs, out_dir, tmp_path, "model.ckpt", "Instrumental")
    assert set(got) == {inputs[0], inputs[2]}


def test_batch_retries_missing_files_individually(tmp_path: Path, monkeypatch) -> None:
    _fake_cli(tmp_path)
    out_dir = tmp_path / "out"
    inputs = [tmp_path / f"DJI_{i}.wav" for i in range(3)]
    for p in inputs:
        p.write_bytes(b"RIFF")
    monkeypatch.setattr(separation, "_pad_to_minimum", lambda src, work: None)
    _stub_probe(monkeypatch)

    state = {"first": True}
    runs: list[list[str]] = []

    def fake_run(cmd, **kw):
        runs.append(cmd)
        target = _cmd_output_dir(cmd)
        target.mkdir(parents=True, exist_ok=True)
        for arg in cmd[1:]:
            if not arg.endswith(".wav"):
                continue
            stem = Path(arg).stem
            if stem == "DJI_1" and state["first"]:
                continue  # dropped by the batch, succeeds when retried alone
            (target / f"{stem}_(Instrumental)_model.wav").write_bytes(_WAV_BYTES)
        state["first"] = False
        return _Proc()

    monkeypatch.setattr(separation.subprocess, "run", fake_run)
    got = separation.run_separator_batch(inputs, out_dir, tmp_path, "model.ckpt", "Instrumental")
    assert set(got) == set(inputs)
    assert len(runs) == 2  # the batch, then one retry


def test_batch_raises_only_when_nothing_was_produced(tmp_path: Path, monkeypatch) -> None:
    _fake_cli(tmp_path)
    out_dir = tmp_path / "out"
    inputs = [tmp_path / "a.wav", tmp_path / "b.wav"]
    for p in inputs:
        p.write_bytes(b"RIFF")
    monkeypatch.setattr(separation, "_pad_to_minimum", lambda src, work: None)
    _install_fake_separator(
        monkeypatch,
        out_dir,
        skip={"a", "b"},
        stderr="ERROR - separator - Failed to process file a.wav: boom",
    )

    import pytest

    with pytest.raises(RuntimeError, match="produced no"):
        separation.run_separator_batch(inputs, out_dir, tmp_path, "model.ckpt", "Instrumental")


def test_short_inputs_are_padded_and_trimmed_back(tmp_path: Path, monkeypatch) -> None:
    # A 1.75 s clip crashes audio-separator's short-audio path; it must be
    # padded on the way in and cut back to its own length on the way out.
    _fake_cli(tmp_path)
    out_dir = tmp_path / "out"
    src = tmp_path / "DJI_0762.wav"
    src.write_bytes(b"RIFF")

    _stub_probe(monkeypatch, duration=1.75)
    _install_fake_separator(monkeypatch, out_dir, skip=set())
    conformed: list[tuple] = []

    def fake_conform(inp, outp, duration, sr, ch, codec):
        conformed.append((inp, duration, sr, ch, codec))
        Path(outp).write_bytes(_WAV_BYTES)
        return outp

    monkeypatch.setattr(separation, "conform_wav_to", fake_conform)

    got = separation.run_separator_batch([src], out_dir, tmp_path, "model.ckpt", "Instrumental")
    assert set(got) == {src}
    assert conformed and conformed[0][1] == 1.75


def test_colliding_input_names_are_rejected(tmp_path: Path) -> None:
    import pytest

    _fake_cli(tmp_path)
    a = tmp_path / "camA" / "DJI_0001.wav"
    b = tmp_path / "camB" / "DJI_0001.wav"
    for p in (a, b):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"RIFF")
    with pytest.raises(RuntimeError, match="distinct filenames"):
        separation.run_separator_batch([a, b], tmp_path / "out", tmp_path, "m.ckpt", "Instrumental")


def test_failure_reasons_parsed_from_stderr() -> None:
    stderr = (
        "2026-08-03 22:46:17.530 - ERROR - separator - Failed to process file "
        "/tmp/x/DJI_0762.wav: The size of tensor a (0) must match the size of tensor b (76734)\n"
    )
    reasons = separation._failure_reasons(stderr)
    assert reasons["DJI_0762.wav"].startswith("The size of tensor a (0)")


# --- provenance: an output must come from THIS run --------------------------


def test_stale_output_from_a_previous_run_is_not_accepted(tmp_path: Path, monkeypatch) -> None:
    """A file existing is not evidence of what produced it.

    The separator names outputs after their input, so a partially successful
    earlier run leaves files with exactly the names this run would write.
    `_find_output` then returned the OLD file as this run's result — confirmed
    with a mock subprocess where a "Failed to process file ...: OOM" line still
    yielded a stale WAV, accepted as that clip's ambience.
    """
    import pytest

    _fake_cli(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    src = tmp_path / "DJI_0001.wav"
    src.write_bytes(_WAV_BYTES)

    # Left behind by a previous run, under the exact name this run would use.
    stale = out_dir / "DJI_0001_(Instrumental)_model.wav"
    stale.write_bytes(b"STALE" + b"\0" * 4096)

    monkeypatch.setattr(separation, "_pad_to_minimum", lambda s, w: None)
    _stub_probe(monkeypatch)
    # This run produces nothing at all.
    _install_fake_separator(
        monkeypatch,
        out_dir,
        skip={"DJI_0001"},
        stderr="ERROR - separator - Failed to process file DJI_0001.wav: CUDA out of memory",
    )

    with pytest.raises(RuntimeError, match="produced no"):
        separation.run_separator_batch([src], out_dir, tmp_path, "model.ckpt", "Instrumental")
    # And the old file is left exactly as it was, not consumed or renamed.
    assert stale.read_bytes().startswith(b"STALE")


def test_unusable_output_is_treated_as_missing(tmp_path: Path, monkeypatch) -> None:
    """An output that cannot be read back is not an output."""
    import pytest

    _fake_cli(tmp_path)
    out_dir = tmp_path / "out"
    src = tmp_path / "DJI_0002.wav"
    src.write_bytes(_WAV_BYTES)
    monkeypatch.setattr(separation, "_pad_to_minimum", lambda s, w: None)
    monkeypatch.setattr(separation, "_output_is_usable", lambda p: False)
    _install_fake_separator(monkeypatch, out_dir, skip=set())

    with pytest.raises(RuntimeError, match="produced no"):
        separation.run_separator_batch([src], out_dir, tmp_path, "model.ckpt", "Instrumental")


def test_failed_trim_of_a_padded_clip_is_not_a_success(tmp_path: Path, monkeypatch) -> None:
    """A padded clip whose length could not be restored must be DROPPED.

    Padding a 1.75 s clip to 12 s and then failing to trim it back leaves a
    12-second file standing in for a 1.75-second clip: ten seconds of invented
    material, with every later clip pushed out of sync. The old code returned
    the padded file and the caller recorded it as a success.
    """
    import pytest

    _fake_cli(tmp_path)
    out_dir = tmp_path / "out"
    src = tmp_path / "DJI_0762.wav"
    src.write_bytes(_WAV_BYTES)
    _stub_probe(monkeypatch, duration=1.75)
    _install_fake_separator(monkeypatch, out_dir, skip=set())

    def failing_conform(*a, **kw):
        raise RuntimeError("ffmpeg conform failed")

    monkeypatch.setattr(separation, "conform_wav_to", failing_conform)

    with pytest.raises(RuntimeError, match="produced no"):
        separation.run_separator_batch([src], out_dir, tmp_path, "model.ckpt", "Instrumental")


def test_separator_timeout_becomes_a_runtime_error(tmp_path: Path, monkeypatch) -> None:
    """`TimeoutExpired` does not inherit from RuntimeError/OSError, so it flew
    straight past every caller's fallback and killed the run instead of
    degrading an optional stage."""
    import subprocess

    import pytest

    _fake_cli(tmp_path)
    src = tmp_path / "a.wav"
    src.write_bytes(_WAV_BYTES)
    monkeypatch.setattr(separation, "_pad_to_minimum", lambda s, w: None)

    def timeout_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(separation.subprocess, "run", timeout_run)
    with pytest.raises(RuntimeError, match="timed out"):
        separation.run_separator_batch(
            [src], tmp_path / "out", tmp_path, "model.ckpt", "Instrumental"
        )
