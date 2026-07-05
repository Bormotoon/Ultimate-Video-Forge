"""Optional voice-enhancement pass over the rendered voice monolith.

Runs (if enabled) right after rendering and before self-check, so self-check
validates whatever audio the user actually gets. Six variants were compared
in a listening test (see README's "Voice Enhancement" section for the full
pros/cons table); the user chose to expose all of them as a selectable
``voice_enhance`` option rather than pick one winner. Two are wired up here
("denoise"/"denoise_dereverb", both reusing the ``.sep-venv`` stack already
used by ``ambience_track`` — see ``separation.py``); the remaining four
(resemble/sgmse_denoise/sgmse_dereverb/reuse) need separate environments the
user sets up themselves and are added in later phases.

Every backend must hand back audio that matches the ORIGINAL file's exact
duration/sample-rate/channels — a third-party tool's own native rate or a
few samples of resampling drift would otherwise desync the timeline.
``timestretch.conform_wav_to`` guarantees this regardless of what the tool
actually produced.
"""

from __future__ import annotations

import logging
from pathlib import Path

from whispersync.engine import separation
from whispersync.engine.media import pcm_codec_for_bit_depth, probe
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


def is_available(mode: str, repo_root: Path) -> bool:
    """Whether ``mode``'s environment is ready to run right now."""
    if mode == "off":
        return True
    if mode in SEP_VENV_MODES:
        return separation.is_available(repo_root)
    return False


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


def _conform_to_originals(
    raw: dict[Path, Path], sources: list[Path], out_dir: Path
) -> dict[Path, Path]:
    """Conform every produced output back to its ORIGINAL file's exact
    duration/sample-rate/channels/codec (see module docstring)."""
    conformed: dict[Path, Path] = {}
    for src in sources:
        produced = raw.get(src)
        if produced is None:
            continue
        info = probe(src)
        codec = pcm_codec_for_bit_depth(info.audio_bits_per_sample)
        out = out_dir / f"{src.stem}_enhanced.wav"
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
    else:
        raise RuntimeError(f"Voice enhancement mode '{mode}' is not available yet in this build.")
    return _conform_to_originals(raw, paths, out_dir)
