"""Optional voice-enhancement pass over the rendered voice monolith.

Runs (if enabled) right after rendering and before self-check, so self-check
validates whatever audio the user actually gets. Six variants were compared
in a listening test (see README's "Voice Enhancement" section for the full
pros/cons table); the user chose to expose all of them as a selectable
``voice_enhance`` option rather than pick one winner. Three are wired up here
— "denoise"/"denoise_dereverb" (reusing the ``.sep-venv`` stack already used
by ``ambience_track``, see ``separation.py``) and "resemble" (the
``resemble-enhance`` CLI, installed into that same ``.sep-venv``). The
remaining three (sgmse_denoise/sgmse_dereverb/reuse) need separate
environments the user sets up themselves and are added in later phases;
selecting one is reported BEFORE the run starts (see ``run_pipeline``'s
pre-flight) rather than after hours of work.

Every backend must hand back audio that matches the ORIGINAL file's exact
duration/sample-rate/channels — a third-party tool's own native rate or a
few samples of resampling drift would otherwise desync the timeline.
``timestretch.conform_wav_to`` guarantees this regardless of what the tool
actually produced.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from whispersync.engine import separation
from whispersync.engine.media import pcm_codec_for, probe
from whispersync.engine.proc import run_logged
from whispersync.engine.timestretch import conform_wav_to

logger = logging.getLogger(__name__)

MODES = (
    "off",
    "denoise",
    "denoise_dereverb",
    "resemble",
    "sgmse_denoise",
    "sgmse_dereverb",
    "reuse",
)

# Already cached under models/separator/ (see setup_sep_venv.sh / the ambience
# feature's models). Denoise keeps the "dry" (non-noise) stem; dereverb keeps
# the "noreverb" stem — same audio-separator CLI as ambience, different model
# and kept stem.
DENOISE_MODEL = "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt"
DEREVERB_MODEL = "dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt"
_DENOISE_STEM = "dry"
_DEREVERB_STEM = "noreverb"

# Modes wired up so far; the rest raise a clear "not available yet" error.
SEP_VENV_MODES = ("denoise", "denoise_dereverb")
# Resemble Enhance ships its own model weights and is installed INTO .sep-venv
# (it needs the same older Python), but it is a different tool from
# audio-separator, hence its own availability probe.
RESEMBLE_MODES = ("resemble",)
IMPLEMENTED_MODES = ("off", *SEP_VENV_MODES, *RESEMBLE_MODES)

# Its own console script is NOT used: as of torchaudio 2.9 the script's
# torchaudio.load/save delegate to TorchCodec, which isn't installed in
# .sep-venv, so `resemble-enhance in/ out/` dies with an ImportError before
# any audio is read. Its *Python* API is unaffected (pure tensor ops), so we
# drive that instead, doing file I/O with soundfile — already in .sep-venv.
# The script below runs inside .sep-venv; it can't import anything from the
# main app, and it is written out at run time rather than shipped as a module
# so it also works from a PyInstaller bundle (where package files aren't
# separate files on disk).
_RESEMBLE_RUNNER = '''
"""Enhance every WAV in in_dir into out_dir. Runs inside .sep-venv."""
import sys
from pathlib import Path

import soundfile as sf
import torch

from resemble_enhance.enhancer import download as _download
from resemble_enhance.enhancer.inference import enhance

in_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
nfe, solver, lambd, tau = int(sys.argv[3]), sys.argv[4], float(sys.argv[5]), float(sys.argv[6])

# Prefer the weights already on disk. resemble_enhance's own download() runs a
# `git pull` on every call and raises if it fails, which would make an offline
# machine unable to use weights it already has.
run_dir = _download.REPO_DIR / "enhancer_stage2"
if not (run_dir / "ds" / "G" / "default" / "mp_rank_00_model_states.pt").exists():
    run_dir = None

# Process in overlapping CHUNKS rather than whole files. An hour of stereo
# 48 kHz float32 is ~1.38 GB for the input array alone, before the mono tensor,
# the model and its internal buffers — on the GPU this is a straightforward
# out-of-memory, and on CPU it is an hour of swapping. Chunks bound the peak to
# a fixed cost regardless of clip length. Neighbouring chunks overlap and are
# crossfaded, so the seam never lands as a click: a generative model
# resynthesises each chunk independently and its output does not join
# sample-exactly at an abrupt cut.
CHUNK_S = 60.0
OVERLAP_S = 1.0

device = "cuda" if torch.cuda.is_available() else "cpu"


def enhance_array(mono, sr):
    """Enhance a mono float32 array in overlapping chunks; returns (wav, sr)."""
    chunk_n = int(CHUNK_S * sr)
    overlap_n = int(OVERLAP_S * sr)
    if len(mono) <= chunk_n:
        hwav, out_sr = enhance(
            dwav=torch.from_numpy(mono),
            sr=sr,
            device=device,
            nfe=nfe,
            solver=solver,
            lambd=lambd,
            tau=tau,
            run_dir=run_dir,
        )
        return hwav.cpu().numpy(), out_sr

    import numpy as np

    pieces = []
    out_sr = sr
    pos = 0
    while pos < len(mono):
        end = min(len(mono), pos + chunk_n)
        hwav, out_sr = enhance(
            dwav=torch.from_numpy(mono[pos:end]),
            sr=sr,
            device=device,
            nfe=nfe,
            solver=solver,
            lambd=lambd,
            tau=tau,
            run_dir=run_dir,
        )
        pieces.append(hwav.cpu().numpy())
        del hwav
        if device == "cuda":
            torch.cuda.empty_cache()
        if end >= len(mono):
            break
        pos = end - overlap_n

    # Equal-power crossfade over the overlap, in OUTPUT sample rate.
    fade_n = max(1, int(OVERLAP_S * out_sr))
    result = pieces[0]
    for piece in pieces[1:]:
        n = min(fade_n, len(result), len(piece))
        ramp = np.linspace(0.0, 1.0, n, dtype="float32")
        tail = result[-n:] * (1.0 - ramp) + piece[:n] * ramp
        result = np.concatenate([result[:-n], tail, piece[n:]])
    return result, out_sr


out_dir.mkdir(parents=True, exist_ok=True)
failures = 0
paths = sorted(in_dir.glob("*.wav"))
for path in paths:
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        # The model is mono-in/mono-out; the caller conforms the result back to
        # the original channel count.
        mono = data.mean(1)
        del data
        hwav, out_sr = enhance_array(mono, sr)
        del mono
        sf.write(str(out_dir / path.name), hwav, out_sr, subtype="FLOAT")
        del hwav
        print("OK " + path.name, flush=True)
    except Exception as e:  # one bad file must not cost the whole batch
        failures += 1
        print("FAIL " + path.name + ": " + repr(e), file=sys.stderr, flush=True)
sys.exit(1 if failures and failures == len(paths) else 0)
'''

# Upstream CLI defaults (resemble_enhance/enhancer/__main__.py) — the settings
# the listening-test comparison was made with.
_RESEMBLE_NFE = 64
_RESEMBLE_SOLVER = "midpoint"
_RESEMBLE_LAMBD = 1.0
_RESEMBLE_TAU = 0.5


def resemble_package_dir(repo_root: Path) -> Path | None:
    """The separation venv's installed ``resemble_enhance`` package, or None.

    Searches every venv location ``separation.sep_venv_candidates`` knows about
    (explicit env var, repo, user data dir, cwd) and both layouts — POSIX
    ``lib/pythonX.Y/site-packages`` and Windows ``Lib/site-packages`` — so an
    installed WhisperSync can find a backend the user actually set up.
    """
    for venv in separation.sep_venv_candidates(repo_root):
        for pattern in ("lib/python*/site-packages", "Lib/site-packages"):
            for site in venv.glob(pattern):
                candidate = site / "resemble_enhance"
                if candidate.is_dir():
                    return candidate
    return None


def is_available(mode: str, repo_root: Path) -> bool:
    """Whether ``mode``'s environment is ready to run right now."""
    if mode == "off":
        return True
    if mode in SEP_VENV_MODES:
        return separation.is_available(repo_root)
    if mode in RESEMBLE_MODES:
        return (
            separation.separator_python(repo_root) is not None
            and resemble_package_dir(repo_root) is not None
        )
    return False


def unavailable_reason(mode: str, repo_root: Path) -> str | None:
    """A user-facing explanation of why ``mode`` can't run, or None if it can.

    Used by the pipeline's pre-flight so an unavailable backend is reported in
    the first seconds of a run instead of after every clip has been rendered.
    """
    if mode not in MODES:
        return f"Unknown voice_enhance mode '{mode}'."
    if is_available(mode, repo_root):
        return None
    if mode in SEP_VENV_MODES:
        return (
            f"Voice enhancement '{mode}' needs the '.sep-venv' environment "
            "(audio-separator). It is not set up — run setup_sep_venv.sh."
        )
    if mode in RESEMBLE_MODES:
        return (
            f"Voice enhancement '{mode}' needs 'resemble-enhance' installed in "
            "the '.sep-venv' environment: .sep-venv/bin/pip install resemble-enhance."
        )
    return f"Voice enhancement mode '{mode}' is not available yet in this build."


def _default_model_dir(repo_root: Path) -> Path | None:
    candidate = repo_root / "models" / "separator"
    return candidate if candidate.exists() else None


def _denoise_batch(
    paths: list[Path], out_dir: Path, repo_root: Path, model_dir: Path | None
) -> dict[Path, Path]:
    return separation.run_separator_batch(
        paths,
        out_dir / "denoise",
        repo_root,
        DENOISE_MODEL,
        _DENOISE_STEM,
        model_dir=model_dir,
        error_label="Voice denoise",
        unavailable_message=(
            "Voice enhancement ('denoise') needs the '.sep-venv' environment "
            "(audio-separator). It is not set up — run setup_sep_venv.sh."
        ),
    )


def _denoise_dereverb_batch(
    paths: list[Path], out_dir: Path, repo_root: Path, model_dir: Path | None
) -> dict[Path, Path]:
    dry = _denoise_batch(paths, out_dir, repo_root, model_dir)
    dry_paths = [dry[p] for p in paths if p in dry]
    dereverbed = separation.run_separator_batch(
        dry_paths,
        out_dir / "dereverb",
        repo_root,
        DEREVERB_MODEL,
        _DEREVERB_STEM,
        model_dir=model_dir,
        error_label="Voice dereverb",
        unavailable_message=(
            "Voice enhancement ('denoise_dereverb') needs the '.sep-venv' "
            "environment (audio-separator). It is not set up — run setup_sep_venv.sh."
        ),
    )
    return {p: dereverbed[dry[p]] for p in paths if p in dry and dry[p] in dereverbed}


def _resemble_batch(paths: list[Path], out_dir: Path, repo_root: Path) -> dict[Path, Path]:
    """Run Resemble Enhance over every path in one .sep-venv process.

    The runner is directory-in/directory-out (one process = one model load for
    the whole shoot, same rationale as the separator batch), so inputs are
    staged into a scratch dir under index-prefixed names — index-prefixed
    because two clips from different cameras can share a filename and the name
    is the only thing tying an output back to its input. Staging uses symlinks:
    a monolith can be hundreds of MB and copying the whole shoot just to rename
    it would be pure I/O.

    NOTE: Resemble Enhance is a generative MONO model — it downmixes and
    resynthesises at its own 44.1 kHz. ``_conform_to_originals`` puts the
    result back to the original's exact duration/rate/channels, but a stereo
    monolith comes back as duplicated mono. That is inherent to the model.

    The runner processes each file in overlapping chunks (see
    ``_RESEMBLE_RUNNER``) so peak memory does not scale with clip length: an
    hour of stereo 48 kHz float32 is ~1.38 GB for the input array alone, before
    the mono tensor, the model and its buffers.
    """
    python = separation.separator_python(repo_root)
    if python is None or resemble_package_dir(repo_root) is None:
        raise RuntimeError(unavailable_reason("resemble", repo_root))

    # A private, uniquely named work area per invocation. Fixed names under
    # `out_dir` meant a second run reading and overwriting a first run's
    # staging and results, and made a leftover output from an earlier run
    # indistinguishable from one this call produced.
    out_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="ws_resemble_", dir=out_dir))
    stage_dir = work_root / "in"
    result_dir = work_root / "out"
    stage_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)

    staged: dict[Path, str] = {}
    for i, src in enumerate(paths):
        name = f"{i:04d}_{src.name}"
        link = stage_dir / name
        try:
            # ABSOLUTE, verified target. `symlink_to(src)` with a relative src
            # stores that text verbatim, so a link in enhance_tmp/resemble_in
            # pointing at "output/audio_synced/a.wav" resolves relative to the
            # LINK's directory and lands on nothing. Creating a dangling
            # symlink succeeds, so the copy fallback never fired and the runner
            # was handed a staging directory of broken links. `strict=True`
            # turns a missing source into an error here, where it is fixable.
            link.symlink_to(src.resolve(strict=True))
            # Prove the link actually opens; a symlink that exists is not a
            # symlink that resolves.
            with link.open("rb") as fh:
                fh.read(1)
        except OSError:
            with contextlib.suppress(OSError):
                link.unlink()
            shutil.copy2(src, link)
        staged[src] = name

    runner = work_root / "_resemble_runner.py"
    runner.write_text(_RESEMBLE_RUNNER)

    # Generative diffusion runs at roughly 0.4x realtime on a current GPU and
    # the whole shoot goes through one process; budget 20x realtime (30-minute
    # floor) rather than let a long episode trip a fixed timeout mid-way.
    total_s = 0.0
    for src in paths:
        with contextlib.suppress(RuntimeError, OSError, ValueError):
            total_s += probe(src).duration
    timeout = max(1800, int(total_s * 20))

    cmd = [
        str(python),
        str(runner),
        str(stage_dir),
        str(result_dir),
        str(_RESEMBLE_NFE),
        _RESEMBLE_SOLVER,
        str(_RESEMBLE_LAMBD),
        str(_RESEMBLE_TAU),
    ]
    logger.info(
        "Voice enhancement (resemble) on %d file(s), %.0f s of audio, timeout %d s",
        len(paths),
        total_s,
        timeout,
    )
    try:
        # Streamed to disk rather than buffered: a generative pass over a whole
        # shoot is a long, chatty process, and the parent has no use for more
        # than the tail of its output.
        proc = run_logged(cmd, timeout=timeout, log_dir=work_root)
    except subprocess.TimeoutExpired as e:
        # Normalised to RuntimeError so the pipeline's optional-stage fallback
        # catches it: `TimeoutExpired` is a SubprocessError, and letting it
        # escape turned a slow OPTIONAL enhancement into a lost project.
        raise RuntimeError(
            f"Resemble Enhance timed out after {timeout}s on {len(paths)} file(s)"
        ) from e
    if proc.returncode != 0:
        raise RuntimeError(
            f"Resemble Enhance failed (exit {proc.returncode}): "
            f"{proc.stderr[-600:] or proc.stdout[-600:]}"
        )

    produced: dict[Path, Path] = {}
    for src, name in staged.items():
        candidate = result_dir / name
        if candidate.exists():
            produced[src] = candidate
        else:
            logger.warning(
                "Resemble Enhance produced no output for %s — %s",
                src.name,
                _resemble_failure(proc.stderr, name) or "no reason reported",
            )
    if not produced:
        raise RuntimeError(
            "Resemble Enhance reported success but produced no output at all "
            f"in {result_dir} — {proc.stderr[-300:] or proc.stdout[-300:]}"
        )
    return produced


def _resemble_failure(stderr: str, staged_name: str) -> str | None:
    """The runner's "FAIL <name>: <error>" line for one staged file, if any."""
    for line in stderr.splitlines():
        if line.startswith(f"FAIL {staged_name}: "):
            return line.split(": ", 1)[1]
    return None


def _conform_to_originals(
    raw: dict[Path, Path], sources: list[Path], out_dir: Path
) -> dict[Path, Path]:
    """Conform every produced output back to its ORIGINAL file's exact
    duration/sample-rate/channels/codec (see module docstring).

    Output names are de-duplicated: two sources from different directories can
    share a filename, and one conformed file standing in for both would splice
    one clip's audio over the other's."""
    conformed: dict[Path, Path] = {}
    used: set[str] = set()
    for src in sources:
        produced = raw.get(src)
        if produced is None:
            continue
        info = probe(src)
        codec = pcm_codec_for(info)
        name = f"{src.stem}_enhanced"
        dup = 2
        while name in used:
            name = f"{src.stem}_{dup}_enhanced"
            dup += 1
        used.add(name)
        out = out_dir / f"{name}.wav"
        conform_wav_to(
            produced,
            out,
            info.duration,
            info.audio_sample_rate or 48000,
            info.audio_channels or 2,
            codec,
        )
        conformed[src] = out
    return conformed


def run_batch(
    mode: str,
    paths: list[Path],
    out_dir: Path,
    repo_root: Path,
    model_dir: Path | None = None,
) -> dict[Path, Path]:
    """Run the selected enhancement backend over every path in one batch.

    Returns ``{original_path: conformed_output_path}`` — the caller can
    ``os.replace`` the conformed output straight over the original. Raises
    ``RuntimeError`` if the mode's environment isn't available or the backend
    fails; callers should catch this and fall back to the unenhanced audio
    with a warning (the same pattern as ``ambience_track``).
    """
    if mode == "off" or not paths:
        return {}
    if mode not in MODES:
        raise RuntimeError(f"Unknown voice_enhance mode '{mode}'.")
    if model_dir is None:
        model_dir = _default_model_dir(repo_root)

    if mode == "denoise":
        raw = _denoise_batch(paths, out_dir, repo_root, model_dir)
    elif mode == "denoise_dereverb":
        raw = _denoise_dereverb_batch(paths, out_dir, repo_root, model_dir)
    elif mode == "resemble":
        raw = _resemble_batch(paths, out_dir, repo_root)
    else:
        raise RuntimeError(f"Voice enhancement mode '{mode}' is not available yet in this build.")
    return _conform_to_originals(raw, paths, out_dir)
