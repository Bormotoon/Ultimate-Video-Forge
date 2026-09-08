from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def check_ffmpeg() -> dict:
    """Probe ffmpeg and ffprobe, reporting every way each can be unusable.

    A diagnostic must never fail in the same way as the thing it is
    diagnosing. Only ``FileNotFoundError`` was handled here, so an ffmpeg that
    hung (``TimeoutExpired``) or was not executable (``PermissionError``) took
    the whole system check down with a traceback — abandoning the CUDA, disk,
    dependency and environment checks the user was about to read, and telling
    them nothing about the ffmpeg problem either. Each outcome is now a
    reported status: ok / not found / permission denied / timeout / error.
    """
    result: dict = {"ffmpeg": False, "ffprobe": False, "details": {}}
    for cmd in ("ffmpeg", "ffprobe"):
        try:
            r = subprocess.run([cmd, "-version"], capture_output=True, text=True, timeout=10)
        except FileNotFoundError:
            _fail(f"{cmd} not found in PATH")
            result["details"][cmd] = "not found"
            continue
        except PermissionError as e:
            _fail(f"{cmd} found but not executable ({e})")
            result["details"][cmd] = f"permission denied: {e}"
            continue
        except subprocess.TimeoutExpired:
            _fail(f"{cmd} did not respond within 10s")
            result["details"][cmd] = "timeout"
            continue
        except OSError as e:  # pragma: no cover - platform dependent
            _fail(f"{cmd} could not be started ({e})")
            result["details"][cmd] = f"error: {e}"
            continue
        if r.returncode == 0:
            line = r.stdout.split("\n")[0]
            _ok(f"{cmd} — {line}")
            result[cmd] = True
            result["details"][cmd] = line
        else:
            _fail(f"{cmd} not working (exit {r.returncode})")
            result["details"][cmd] = r.stderr.strip() or f"exit {r.returncode}"
    return result


def check_cuda() -> dict:
    """Report whether faster-whisper (the actual transcription backend) can use
    CUDA — via ctranslate2's own device probe, the same check
    ``transcriber.resolve_device`` makes at runtime. The app does not depend on
    torch at all (transcription runs on ctranslate2); checking only
    ``torch.cuda.is_available()`` (the old implementation) reported "CUDA
    check skipped" on a perfectly working GPU machine whenever torch wasn't
    installed, which is the common case here. See PROJECT_ANALYSIS.md §3.6.
    Torch, if present, additionally supplies the GPU name/VRAM for the report.
    """
    result: dict = {"cuda_available": False, "gpu_name": None, "vram_gb": None}
    try:
        from whispersync.engine.transcriber import _ct2_cuda_available

        ct2_cuda = _ct2_cuda_available()
    except ImportError as e:
        _fail(f"CUDA check error (ctranslate2/faster_whisper not importable): {e}")
        result["details"] = str(e)
        return result

    result["cuda_available"] = ct2_cuda
    if not ct2_cuda:
        _warn("CUDA not available to ctranslate2 (faster-whisper will run on CPU)")
        result["details"] = "ctranslate2 reports no CUDA device"
        return result

    # torch is optional; use it only to enrich the report with GPU name/VRAM.
    try:
        import torch

        if torch.cuda.is_available():
            device = torch.cuda.current_device()
            name = torch.cuda.get_device_name(device)
            props = torch.cuda.get_device_properties(device)
            vram_gb = round(props.total_memory / (1024**3), 2)
            result["gpu_name"] = name
            result["vram_gb"] = vram_gb
            _ok(f"CUDA — {name}, {vram_gb} GB VRAM (ctranslate2 + torch)")
            return result
    except ImportError:
        pass
    _ok("CUDA — available to ctranslate2 (install torch for GPU name/VRAM detail)")
    return result


def check_python() -> dict:
    result: dict = {"python_version": sys.version}
    _ok(f"Python {sys.version.split()[0]}")
    return result


def check_dependencies() -> dict:
    deps = {
        "ctranslate2": "ctranslate2",
        "faster_whisper": "faster_whisper",
        "PyQt6": "PyQt6",
    }
    result: dict = {}
    for name, mod in deps.items():
        try:
            __import__(mod)
            result[name] = True
            _ok(f"{name} — installed")
        except ImportError:
            result[name] = False
            _warn(f"{name} — not installed")
    return result


def check_ambience_separator() -> dict:
    """Whether the optional ``.sep-venv`` environment (ambience-track feature,
    ``--ambience-track``) is set up. Not fatal — the feature is opt-in — but
    unlike the other checks this one previously wasn't reported at all, so a
    user enabling the GUI checkbox only learned it was missing after a run
    failed. See PROJECT_ANALYSIS.md §3.6."""
    from whispersync.engine import separation

    repo_root = Path(__file__).resolve().parents[2]
    available = separation.is_available(repo_root)
    if available:
        _ok("Ambience separator (.sep-venv) — available")
    else:
        _warn(
            "Ambience separator (.sep-venv) — not set up "
            "(optional; run setup_sep_venv.sh to enable --ambience-track)"
        )
    return {"available": available, "repo_root": str(repo_root)}


def check_voice_enhance_environments() -> dict:
    """Whether each ``voice_enhance`` backend's environment is set up.

    "denoise"/"denoise_dereverb" reuse ``.sep-venv`` (see
    ``check_ambience_separator``) and "resemble" needs the ``resemble-enhance``
    CLI installed into that same venv — all three are probed. The rest
    (sgmse_denoise/dereverb, reuse) have no engine backend in this build yet
    and are reported as such. Not fatal either way — the feature is opt-in."""
    from whispersync.engine import enhance

    repo_root = Path(__file__).resolve().parents[2]
    result: dict = {}
    for mode in enhance.MODES:
        if mode == "off":
            continue
        available = enhance.is_available(mode, repo_root)
        result[mode] = available
        if available:
            _ok(f"Voice enhancement '{mode}' — available")
        elif mode in enhance.SEP_VENV_MODES:
            _warn(
                f"Voice enhancement '{mode}' — not set up "
                "(optional; run setup_sep_venv.sh to enable --voice-enhance)"
            )
        elif mode in enhance.RESEMBLE_MODES:
            _warn(
                f"Voice enhancement '{mode}' — not set up (optional; "
                ".sep-venv/bin/pip install resemble-enhance to enable it)"
            )
        else:
            _warn(f"Voice enhancement '{mode}' — not available in this build yet")
    return result


def check_disk_space(min_gb: int = 10, paths: dict[str, Path] | None = None) -> dict:
    """Free space on the filesystems this app actually writes to.

    Checking ``/`` answered a question nobody asked: renders go to the output
    folder, transcripts to the cache directory and scratch to the temp
    directory, and on a typical setup at least one of those is a different
    filesystem (a media volume, a small tmpfs). A green "500 GB free" on the
    root while the output volume has 2 GB is worse than no check at all.
    Filesystems are de-duplicated by device so one mount is not reported
    three times.
    """
    import tempfile

    from whispersync.config import WhisperSyncConfig

    if paths is None:
        cfg = WhisperSyncConfig()
        paths = {
            "output": cfg.resolved_output_dir,
            "cache": cfg.resolved_cache_dir,
            "temp": Path(tempfile.gettempdir()),
        }

    result: dict = {"ok": True, "filesystems": {}}
    seen_devices: set[int] = set()
    for label, path in paths.items():
        # An output directory that does not exist yet still lives on some
        # filesystem: walk up to the nearest existing ancestor.
        probe_path = path
        while not probe_path.exists() and probe_path != probe_path.parent:
            probe_path = probe_path.parent
        try:
            device = probe_path.stat().st_dev
            if device in seen_devices:
                continue
            seen_devices.add(device)
            usage = shutil.disk_usage(probe_path)
        except OSError as e:
            _warn(f"Disk space ({label}): could not be checked — {e}")
            result["filesystems"][label] = {"error": str(e)}
            continue
        free_gb = usage.free / (1024**3)
        ok = free_gb >= min_gb
        result["filesystems"][label] = {
            "path": str(probe_path),
            "free_gb": round(free_gb, 1),
            "ok": ok,
        }
        if ok:
            _ok(f"Disk space ({label}, {probe_path}): {free_gb:.1f} GB free")
        else:
            _fail(f"Disk space ({label}, {probe_path}): {free_gb:.1f} GB free (min {min_gb} GB)")
            result["ok"] = False
    # Kept for compatibility with existing report readers.
    frees = [
        fs["free_gb"]
        for fs in result["filesystems"].values()
        if isinstance(fs.get("free_gb"), float)
    ]
    result["free_gb"] = min(frees) if frees else None
    return result


def _isolated(label: str, fn: Callable[[], dict]) -> dict:
    """Run one check, converting ANY failure into a reported status.

    The point of a system check is to tell the user what is wrong. A check that
    raises stops every check after it, so the one broken component hides the
    state of everything else — the opposite of what was asked for.
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a diagnostic must not fail
        _fail(f"{label} check could not be completed: {e}")
        return {"status": "error", "error": str(e)}


def run_all_checks() -> dict:
    print("WhisperSync — System Check")
    print("=" * 40)

    print("\n[FFmpeg]")
    ff = _isolated("FFmpeg", check_ffmpeg)

    print("\n[CUDA]")
    cu = _isolated("CUDA", check_cuda)

    print("\n[Disk]")
    ds = _isolated("Disk", check_disk_space)

    print("\n[Python]")
    py = _isolated("Python", check_python)

    print("\n[Dependencies]")
    deps = _isolated("Dependencies", check_dependencies)

    print("\n[Ambience separator]")
    sep = _isolated("Ambience separator", check_ambience_separator)

    print("\n[Voice enhancement]")
    enh = _isolated("Voice enhancement", check_voice_enhance_environments)

    report = {
        "ffmpeg": ff,
        "ffprobe": ff,
        "cuda": cu,
        "disk": ds,
        "python": py,
        "dependencies": deps,
        "ambience_separator": sep,
        "voice_enhance": enh,
    }

    # In the current working directory (where the user runs the command from),
    # not next to the module inside the installed package — the README has
    # always documented "report.json" without a package-internal path, and a
    # user has no reason to go looking inside site-packages for it. See
    # PROJECT_ANALYSIS.md §3.6.
    path = Path.cwd() / "report.json"
    try:
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
    except OSError as e:
        # A read-only working directory must not swallow the report the user
        # just watched being produced.
        _warn(f"Could not write {path}: {e}")
    else:
        print(f"\nReport saved to {path}")

    return report


if __name__ == "__main__":
    run_all_checks()
