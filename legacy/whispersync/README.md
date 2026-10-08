# WhisperSync — Advanced Audio/Video Synchronization

[![CI](https://github.com/Bormotoon/WhisperSync/actions/workflows/ci.yml/badge.svg)](https://github.com/Bormotoon/WhisperSync/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%E2%80%933.14-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Code style](https://img.shields.io/badge/code%20style-black%20%7C%20ruff%20%7C%20mypy-000000)
[![Donate](https://img.shields.io/badge/%E2%9D%A4-Support%20the%20project-E53935)](https://www.donationalerts.com/r/bormotoon)

**English** · [Русский](README.ru.md)

---

**WhisperSync** synchronizes audio and video for **dual-system sound**: your camera records video with scratch audio, while an external recorder (lavalier, Zoom, Tascam, a phone with a radio mic) captures clean sound separately. The two devices run on independent quartz clocks, so over minutes-to-hours their timing slowly diverges — a creeping, sometimes non-linear **clock drift** of up to a second that waveform matchers lock at the start but cannot track. WhisperSync finds the exact alignment *along the whole recording* and produces a ready-to-import **FCPXML** project for Final Cut Pro or DaVinci Resolve.

![WhisperSync GUI](docs/images/main_window.png)

> The main window: drag-and-drop sources, strategy selection, a live multitrack timeline, and a real-time log.

The pipeline: **transcribe** → **match word anchors** → **fit K/offset (RANSAC)** → **apply a sync strategy** → **render synced audio** → **export FCPXML**.

Using [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2), WhisperSync transcribes both audio streams with word-level timestamps, matches shared words (anchors) via sequence alignment, runs a RANSAC linear regression to robustly estimate the clock ratio **K** and the **offset**, then re-assembles the recorder audio under the picture with one of three sync strategies. The render preserves the recorder's native channel count and bit depth end to end (no forced mono/16-bit downmix) and uses a transparent resample instead of time-stretch wherever the real clock drift is small enough. Your source media is never modified.

## Table of Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage](#usage) — [GUI](#gui) · [CLI](#cli)
- [Sync Strategies Guide](#sync-strategies-guide)
- [Retake Detection](#retake-detection)
- [Self-Check](#self-check)
- [Voice Enhancement](#voice-enhancement)
- [Configuration](#configuration)
- [Output Files](#output-files)
- [Verifying the Result](#verifying-the-result)
- [Troubleshooting](#troubleshooting)
- [Architecture](#architecture)
- [Development & Contributing](#development--contributing)
- [Support the Project](#support-the-project)
- [License](#license)
- [Acknowledgements](#acknowledgements)

## Features

### Synchronization engine

- **Word-level anchor matching** — both tracks are transcribed with per-word timestamps; shared words become time anchors. Works where waveform matchers give up (echoey camera audio, distant mics, noisy rooms).
- **Coarse-then-fine matching for long sources** — a clip is first roughly located inside a possibly multi-hour recorder by rare-word voting, then matched precisely in a narrow window.
- **RANSAC linear fit + two-stage outlier rejection** — a robust estimate of clock ratio K and offset that survives transcription mistakes and false word matches; Unicode-aware token normalization keeps anchors intact in any language («ё», dashes, quotes and all).
- **Three honest sync strategies** — Global Linear, Local Time-Stretch, and the recommended Hybrid (per-phrase placement with pauses absorbing the drift). See the [guide](#sync-strategies-guide).
- **Auto-strategy advice** — after each run, the measured drift character (residual, local rate spread) is checked against the strategy you used; if another would fit better, you get a warning saying exactly why.
- **Acoustic fallback ("Strategy 0")** — a clip with *no* usable transcript match against any recorder (music, noise, a language Whisper garbles, near-silence) falls back to a coarse GCC-PHAT waveform cross-correlation scan to estimate offset/K without words.
- **Boundary Flex** — sub-frame lip-sync refinement: each rendered piece's start is acoustically nudged by GCC-PHAT cross-correlation between camera and recorder audio (on by default).
- **Seam-snap-to-silence** — piece boundaries in the piecewise strategies snap to the nearest inter-word silence in the recorder, so a cut never lands mid-word (no "stutter" artifacts).
- **Per-camera lip-sync calibration** — a constant mic-to-lips delay baked into a camera's own audio pipeline is invisible to any audio-only method; `camera_av_offset_ms` (global or per-camera) corrects it.
- **Anchor-count gating** — a clip with too few anchors is never trusted to a shaky timecode fit; it falls back to filename order with a warning instead of landing at a wildly wrong position.

### Audio quality (bit-perfect render path)

- **Native channels & bit depth preserved end to end** — no forced mono, no forced 16-bit; a 24-bit stereo recorder comes out as 24-bit stereo. Pieces are cut from a lossless PCM master (fixes non-sample-accurate seeking in mp3/m4a sources too).
- **Transparent resample instead of time-stretch** — real clock drift is a fraction of a percent; WhisperSync conforms such pieces by resampling ("varispeed", pitch shift of a few cents — inaudible on speech) instead of `atempo`/WSOLA and its phase artifacts. WSOLA only kicks in when a piece genuinely needs a bigger correction.
- **Fades only where needed** — seams between acoustically continuous pieces are joined butt-to-butt; a fade (which carves an audible dip) is applied only to genuinely discontinuous seams.
- **Single-pass assembly** — concatenation, exact-length padding, and optional pause ducking happen in one ffmpeg encode, not a chain of lossy generations; `libsoxr` resampling when available.
- **Pause ducking (optional)** — attenuates the recorder during pauses where *both* tracks are silent (computed from full word lists, not anchor gaps), hiding any ambience desync between phrases.

### Inputs & outputs

- **Multi-camera** — put each camera's clips in its own sub-folder; each camera gets its own timeline lane, and the clean audio is synced once from a reference camera (auto-picked or `--audio-source-camera`).
- **Multiple recorders** — pass `--audio-file` several times; `--recorder-mode best` picks the strongest recorder per clip (one audio lane), `all` puts every recorder on its own lane (multi-mic/multi-speaker shoots).
- **FCPXML export** (v1.9 by default) — references your untouched video files plus the rendered synced WAVs, with honest per-asset audio channel/rate attributes; validated before it's handed to you.
- **`--render-master-wav`** — additionally mixes every synced voice clip (and the ambience track, if enabled) at its timeline offset onto one silence-padded WAV spanning the whole timeline, for people without an NLE.
- **Ambience track (optional)** — an AI source-separation model strips the camera's own (echoey, slightly off-sync) voice while keeping the room tone, on its own lane next to the clean voice; runs in an isolated `.sep-venv` environment, batch-processed with a single model load.
- **Retake detection (optional, `--detect-retakes`)** — finds lines the speaker re-recorded back-to-back in an unedited take (flub, stop, restart) and **marks** each attempt on the timeline, so you can jump straight to them instead of hunting for the flubs yourself. A transcript-based heuristic, fully non-destructive — nothing is cut, moved or re-timed.
- **Self-check (optional, `--self-check warn|repair`)** — after rendering, re-transcribes each clip's voice monolith and compares it word-for-word against the camera clip's own transcript, flagging spans where content or timing diverge beyond normal cross-run Whisper jitter. Catches defects `--verify`'s acoustic lag measurement can't see (a dropped/duplicated word, a piece built from the wrong recorder span). `warn` just reports findings; `repair` additionally re-aligns and re-renders each flagged span's own small stretch of audio, then re-checks it once more. Costs one extra Whisper pass per clip (two if any span needs a repair attempt).
- **Transcript export** — full transcripts of every recorder and camera clip saved as JSON + SRT next to the output (word-level timestamps included).

### GUI

- **PyQt6 dark-theme app** with drag-and-drop zones for the video folder, recorder file(s), and output folder.
- **Multi-recorder drop zone** — drop several audio files at once; a `best`/`all` recorder-mode picker unlocks at 2+ files.
- **Live multitrack timeline** — one row per camera and per audio lane, showing every clip's real position, applied speed change (e.g. `+0.10%`), and live status: pending (dashed), working (orange outline), done (solid). Hover for offset / duration / in-point / speed.
- **Transcription Settings dialog** — model, language, device, compute type, transcribe mode (fast/quality), and initial prompt without touching a config file.
- **Re-run with Selected Strategy** — after a run, switch the strategy radio and re-run; transcripts are cached, so it skips straight to alignment/render.
- **Detect retakes checkbox** — finds re-recorded lines and marks each attempt on the timeline (see [Retake Detection](#retake-detection)); off by default.
- **Self-check dropdown (Off / Warn only / Warn + auto-repair)** — re-transcribes each rendered clip and flags content/timing spans that diverge from the camera's own transcript beyond normal Whisper jitter; the repair option additionally re-aligns and re-renders each flagged span. Off by default (one extra Whisper pass per clip; two if any span needs repair).
- **Voice enhancement dropdown** — optionally cleans up the rendered voice (denoise / denoise+de-reverb / Resemble Enhance / SGMSE+ diffusion / RE-USE) before self-check validates it; pros/cons of each are in the Help tab. Off by default.
- **Weighted overall progress** — one continuous progress bar across all stages (no per-stage resets), plus explicit "Loading Whisper model…" status during a first-time model download.
- **Pipeline warnings surfaced in the log** — unaligned clips, high residual, strategy advice, validation problems.
- **Responsive cancellation** — cancel takes effect mid-clip, even during a large multi-core render.
- **Built-in Help tab** — a full tutorial plus an interactive micro-sync simulator that shows how each strategy re-shapes the audio as you drag drift/phrase-length sliders.

### CLI & automation

- **Full-control headless mode** — every setting reachable via flags or a JSON config; `--json` prints a machine-readable report to stdout while progress goes to stderr.
- **Meaningful exit codes** — `0` success, `1` run failure, `2` usage/config error.
- **`--dry-run`** — scan + transcribe + align only, prints the alignment summary without touching audio.
- **`--verify`** — after rendering, measures the *realized* lip-sync lag per clip via GCC-PHAT and prints a median/p90/max summary (also available standalone as `tools/verify_sync.py`).
- **`--self-check warn|repair`** — after rendering, re-transcribes each clip's voice monolith and compares it against the camera clip's own transcript, flagging content/timing spans `--verify`'s acoustic measurement can't see; `repair` also re-aligns and re-renders each flagged span (see [Self-Check](#self-check)).
- **`--voice-enhance`** — optionally clean up the rendered voice (denoise/de-reverb/generative/diffusion models) before self-check validates it; six variants to choose from (see [Voice Enhancement](#voice-enhancement)).
- **Environment self-check** — `python -m whispersync.engine.system_check` validates ffmpeg, CUDA (through the same ctranslate2 path the engine uses), dependencies, disk space, and which voice-enhancement/ambience environments are set up; writes `report.json`.

### Performance & reliability

- **NVIDIA GPU acceleration through ctranslate2** — no torch required; batched inference is the main speed lever, with an automatic OOM ladder (smaller batch → smaller compute type → CPU).
- **Transcription cache** — SHA-256-keyed by file, settings, and the *resolved* device/compute type; re-runs skip transcription entirely. Optional age-based pruning (`cache_max_age_days`).
- **One shared render pool** — pieces of all clips render across all CPU cores through a single process pool; each clip's final assembly overlaps the next clips' rendering.
- **In-memory Boundary Flex** — both tracks are decoded to memory once per clip and windows are sliced from arrays (no per-boundary ffmpeg spawns).
- **Run isolation** — each run owns a uniquely named scratch directory on the output volume and holds a cooperative lock on its output folder, so two runs can never overwrite each other's audio or delete each other's working files; a second run on the same folder is refused with an explanation rather than silently interleaved.
- **Atomic results** — the FCPXML, the master WAV and every rendered voice track are written to a temporary beside their destination and moved into place only when complete, so a crash or a cancellation can never leave a fragment where a previous good result was.
- **Alignment acceptance gate** — every clock map is checked for evidence, coverage, residual and a physically plausible clock ratio *before* anything is placed or rendered with it; a map that fails becomes a clearly reported unresolved clip instead of a confident wrong render.
- **Fork safety** — the render pool never forks (Qt, CTranslate2 and CUDA native threads are invisible to any "am I single-threaded" check); forkserver/spawn are used instead.
- **Early VRAM release** — the Whisper model is unloaded right after alignment, freeing GPU memory for rendering/separation.
- **Configuration validated up front** — types, enums, ranges and finite numbers are checked once, after CLI/GUI overrides are merged and before transcription starts; a bad value is a one-line usage error (exit 2), not a strange result hours later.
- **Cross-platform** — Windows, macOS, Linux; CI-tested on Python 3.10–3.14, plus a job that installs the built wheel into a clean environment outside the checkout and smoke-tests the entry points, late imports and packaged resources.

## Screenshots

### Live multitrack timeline

![Timeline](docs/images/timeline.png)

One row per camera and per audio lane. You can see each clip's real position (`DJI_0838` and `DJI_0839` with a genuine gap between them; the second camera `GX010024` at its own offset on a separate row), the audio speed change (`−0.10%`, `+0.11%`), and the live status: **done** (solid), **working** (orange outline), **pending** (dashed).

### Strategy diagrams

| Strategy 1 — Global Linear | Strategy 2 — Local Time-Stretch | Strategy 3 — Hybrid |
|----------------------------|---------------------------------|----------------------|
| ![S1](docs/images/strategy_1.png) | ![S2](docs/images/strategy_2.png) | ![S3](docs/images/strategy_3.png) |
| One block, uniform conform | Per-segment factors between anchors | Phrases corrected + pauses absorb the rest |

### Transcription settings dialog

![Settings dialog](docs/images/settings_dialog.png)

### Help tab with the interactive simulator

![Simulator](docs/images/simulator.png)

The **Help** tab is a built-in tutorial: it walks through the whole process and includes an interactive **micro-sync simulator**. Drag the **drift** and **phrase length** sliders, switch the strategy on the left — and watch the recorder track (red) re-shape itself under the picture (blue), exactly like the real timeline. The accuracy and distortion-index readouts make the time-stretch-vs-pauses trade-off tangible before you run anything.

## How It Works

1. **Probe** — read the duration and audio format (channels, bit depth, sample rate) of every clip.
2. **Transcribe** — Whisper turns both the camera scratch audio and the recorder audio into word-level transcripts. Multi-hour recorders run through batched GPU inference; results are cached.
3. **Match anchors** — words shared by both transcripts become time anchors. A coarse rare-word vote first locates each clip inside the recorder, then a precise match runs in a narrow window.
4. **Fit** — a RANSAC linear regression estimates the clock ratio **K** and the **offset**, discarding mismatched words as outliers (two-stage: gross window + post-fit residual filter).
5. **Re-align** — the chosen strategy converts the alignment into render "pieces" (recorder start, duration, tempo factor); seam-snap and Boundary Flex refine the cut points; each piece is conformed by transparent resampling or atempo and assembled into one continuous WAV per camera clip, at the recorder's native quality.
6. **Export** — an FCPXML is written referencing your original video files and the rendered synced audio, ready for Final Cut Pro / DaVinci Resolve.

## Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Python | ≥ 3.10 | 3.12+ |
| GPU | none (CPU fallback) | NVIDIA GPU (CUDA/cuDNN) |
| ffmpeg / ffprobe | on PATH | a recent build with `libsoxr` |
| RAM | 4 GB | 8+ GB |
| Disk | 10 GB free | SSD |

- **NVIDIA GPU** is recommended for fast transcription. Transcription runs on [faster-whisper](https://github.com/SYSTRAN/faster-whisper)/ctranslate2, which does **not** need torch — CUDA is detected by ctranslate2's own probe. Without a GPU everything still works on CPU (slower).
- **ffmpeg/ffprobe** must be on `PATH` — used for audio extraction, resampling/time-stretch, and assembly. A build with `libsoxr` gives higher-quality resampling (WhisperSync falls back to the built-in resampler automatically).
- **CUDA/cuDNN** are only needed for GPU mode — install via the [NVIDIA CUDA Toolkit](https://developer.nvidia.com/cuda-toolkit).

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/Bormotoon/WhisperSync.git
cd WhisperSync

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate   # Linux/macOS
# venv\Scripts\activate    # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Check your environment
python -m whispersync.engine.system_check
```

`system_check` verifies ffmpeg, CUDA (through ctranslate2 — the same path the transcription engine uses at runtime), Python, dependencies, the optional `.sep-venv` (ambience-track feature), and free disk space. It prints a colored table and writes `report.json` to the current directory.

**Optional — ambience track.** The AI source-separation feature lives in an isolated environment so its heavy dependencies never touch the main install:

```bash
./setup_sep_venv.sh
```

## Quick Start

```bash
# GUI
python main.py

# CLI — everything on defaults (Hybrid strategy, auto device)
python main.py --cli --video-dir ./videos --audio-file recorder.wav
```

Drop your video folder and recorder file(s) into the GUI, press **SYNC**, and import the generated `sync_output.fcpxml` into Final Cut Pro or DaVinci Resolve.

## Usage

### GUI

```bash
python main.py
```

- **Drag-and-drop** the video folder and recorder audio file(s) — drop several recorder files at once; with 2+ files a `best`/`all` recorder-mode picker unlocks (mirrors `--recorder-mode`).
- Pick a strategy (3 radio buttons) and options: timebase, crossfade, Boundary Flex, pause ducking, ambience track.
- **Transcription Settings…** opens a dialog with model / language / device / compute type / initial prompt / transcribe mode.
- **SYNC** starts the run; the timeline, progress bar, and log update live; **Cancel** takes effect mid-clip.
- After a successful run: **Open Output Folder** and **Re-run with Selected Strategy** (transcripts are cached — a strategy change skips straight to alignment/render).
- The status line tells you exactly what the model stage is doing: a model already on disk reports "found on disk — loading into memory", and only a genuinely missing one reports a (one-time) Hugging Face download. A cached model is loaded straight from its local path — no network round-trips on start.

### CLI

```bash
# Basic (default strategy — 3, Hybrid)
python main.py --cli --video-dir ./videos --audio-file rec.wav --output out.fcpxml

# Strategy 1 — Global Linear, JSON report for automation
python main.py --cli --video-dir ./videos --audio-file rec.wav \
  --strategy 1 --json 2>/dev/null

# Multi-camera (sub-folders) + two lavaliers on separate lanes
python main.py --cli --video-dir ./shoot \
  --audio-file lavA.wav --audio-file lavB.wav --recorder-mode all

# CPU, smaller model, alignment only
python main.py --cli --video-dir ./videos --audio-file rec.wav \
  --device cpu --compute-type int8 --model medium --dry-run

# Post-render lip-sync self-check + a single master WAV for editors without an NLE
python main.py --cli --video-dir ./videos --audio-file rec.wav \
  --verify --render-master-wav

# Denoise the synced voice before self-check validates it
python main.py --cli --video-dir ./videos --audio-file rec.wav \
  --voice-enhance denoise --self-check warn
```

#### CLI options

| Flag | Type | Description |
|------|------|-------------|
| `--video-dir` | Path | **Required.** Folder with video files (sub-folders = cameras) |
| `--audio-file` | Path | **Required.** Recorder audio file (repeat for several recorders) |
| `--strategy` | int | `1`, `2`, or `3` (default: `WhisperSyncConfig.default_strategy` = `3`/Hybrid); `4` is accepted as a deprecated alias of `3` |
| `--output` | Path | FCPXML path (default: `<video-dir>/sync_output.fcpxml`) |
| `--model` | str | Whisper model (default `large-v3`) |
| `--device` | str | `auto` / `cuda` / `cpu` (default `auto`) |
| `--compute-type` | str | `auto` / `float16` / `int8` / … (default `auto`) |
| `--batch-size` | int | Batched-inference batch size, the main GPU speed lever (default `16`) |
| `--mode` | str | `fast` (batched) or `quality` (sequential, context-aware, ~10× slower) |
| `--initial-prompt` | str | Domain context to bias Whisper vocabulary |
| `--language` | str | Language code (`ru`, `en`, …); omit for auto-detect |
| `--fcpxml-version` | str | FCPXML version (default `1.9`) |
| `--timebase-source` | str | `camera` or `recorder` — which sample rate anchors FCPXML time values |
| `--audio-source-camera` | str | Multicam: camera sub-folder the synced audio derives from (default: auto) |
| `--camera-av-offset-ms` | float | Constant per-camera lip-sync calibration in ms added to synced audio positions (default `0`) |
| `--recorder-mode` | str | `best` (one lane, strongest recorder per clip) or `all` (every recorder on its own lane) |
| `--crossfade` / `--no-crossfade` | flag | Declick fades on genuinely discontinuous seams (default on) |
| `--crossfade-ms` | int | Fade length in ms (default `10`) |
| `--render-workers` | int | Parallel ffmpeg render processes (`0` = auto = CPU count, `1` = sequential) |
| `--boundary-flex` / `--no-boundary-flex` | flag | Acoustic sub-frame refinement of each piece's start (default **on**) |
| `--pause-duck` / `--no-pause-duck` | flag | Attenuate pauses where both tracks are silent (default off) |
| `--pause-duck-db` | float | Duck depth in dB: `0` = off … very negative → silence (default `-18`) |
| `--ambience-track` | flag | Voice-free camera-ambience lane (needs `.sep-venv`, see `setup_sep_venv.sh`; default **on**, skipped with a warning when `.sep-venv` is missing) |
| `--voice-segment-minutes` | int | Split each rendered voice WAV into ~N-minute segments, cut at the quietest point near each boundary (`0` = one continuous file per clip, default). Lets the NLE's own audio sync re-align every few minutes. Typical: `1`/`2`/`3`/`5`/`10` |
| `--render-master-wav` | flag | Also render one WAV spanning the whole timeline (voice + ambience mixed at their offsets over silence) next to the FCPXML (default off) |
| `--detect-retakes` | flag | Find re-recorded lines and mark each attempt on the timeline (default off) |
| `--self-check {warn,repair}` | str | Re-transcribe each rendered clip and flag content/timing spans against the camera's own transcript; `repair` also re-aligns and re-renders each flagged span (default off; one extra Whisper pass per clip, two if a span needs repair) |
| `--voice-enhance {denoise,denoise_dereverb,resemble,sgmse_denoise,sgmse_dereverb,reuse}` | str | Run a third-party model over the rendered voice monolith before self-check (default off) — see [Voice Enhancement](#voice-enhancement) |
| `--reuse-source-dir` | Path | Directory with NVIDIA's own RE-USE inference source (see [Voice Enhancement](#voice-enhancement)); only used with `--voice-enhance reuse` |
| `--save-transcripts` / `--no-save-transcripts` | flag | Save full transcripts (JSON+SRT) to `output/transcripts/` (default on) |
| `--config` | Path | JSON config file (a missing path is an error, not a silent fallback) |
| `--no-cache` | flag | Disable the transcription cache |
| `--dry-run` | flag | Alignment only, no audio processing |
| `--verify` | flag | Measure realized per-clip lip-sync lag after the render (GCC-PHAT) and print a summary |
| `--json` | flag | JSON report to stdout; progress and warnings go to stderr |
| `--verbose` | flag | Debug logging |
| `--version` | flag | Print version and exit |

Exit codes: `0` success · `1` run failure (no anchors, ffmpeg error, …) · `2` usage/config error.

#### Sample output

```
=== Sync Complete ===
  Anchors:    412
  K:          1.000237
  Offset:     12.8470 s
  Residual:   11.8 ms
  Output:     output/sync_output.fcpxml
```

## Sync Strategies Guide

| Strategy | Name | When to use |
|----------|------|-------------|
| **1** | Global Linear | Linear clock drift (the most common case). One tempo-conform factor for the entire file. Fastest, minimal processing. |
| **2** | Local Time-Stretch | Non-linear drift, varying tempo. Each segment between anchors gets its own factor; boundaries snap to inter-word silences. |
| **3** | Hybrid (Global + Silence) | General purpose, **recommended default**. Sentence-wise: the recorder is cut ONLY in the real pauses between sentences (a pause ≥ `phrase_gap_threshold` ends a sentence), each sentence is conformed as one piece at the smoothed local drift rate (a transparent resample — anchor jitter never reaches the speech), and the pause pieces absorb all placement residue (stretching room tone is inaudible). Robust to non-linear drift, with no cut ever landing inside speech. |

```
Strategy 1        Video:  |========================>
                  Audio:  |========================>  × conform(1/K)

Strategy 2        Video:  |=== seg1 ===|=== seg2 ===|=== seg3 ===>
                  Audio:  |== seg1 ==>|==== seg2 ====|== seg3 ==>   (per-segment factors)

Strategy 3        Video:  |== phrase ==| pause |== phrase ==| pause |== phrase ==>
                  Audio:  |==×(1/K)===| silence|==×(1/K)===| silence|==×(1/K)==>
```

> **About pitch.** Real clock drift is a fraction of a percent, and WhisperSync conforms such pieces with a transparent resample instead of time-stretch — pitch shifts by the same tiny fraction (a few cents, inaudible on speech) with none of WSOLA's phase artifacts. `atempo` engages only when a piece's actual correction exceeds the threshold (`stretch_method` / `RESAMPLE_CONFORM_MAX_DEVIATION`).

> **Mid-word stutter.** Strategy 3 cannot cut inside speech at all — its boundaries are DEFINED by the inter-sentence pauses. In strategy 2, piece boundaries snap to the nearest inter-word silence (seam-snap-to-silence, moving both the recorder and camera side of the breakpoint so tempo factors stay stable). Boundary Flex moves boundaries without ever repeating or skipping recorder content, so its lip-sync nudges can't create micro-repeats.

> **Auto-strategy.** After every run the measured drift character is compared against the strategy you used; if a different one would fit better, a warning tells you which and why. Re-running is cheap — transcripts are cached.

> **Strategy 0 (acoustic fallback).** If a clip has no usable transcript match against any recorder (music, noise, an unsupported language), a coarse GCC-PHAT cross-correlation grid scan estimates offset/K directly from the waveforms — less precise than word anchors, but it turns a hard failure into a working placement, as long as the same physical sound reaches both mics.

> **Clip placement.** Every clip aligns to the recorder independently, and its timeline position comes from matched timecodes — clips need not be contiguous; real gaps between takes are preserved. A clip with fewer than `min_anchors` anchors falls back to filename order with a warning instead of trusting a shaky fit.

> **Multi-camera.** Put each camera's clips in its own sub-folder of `--video-dir` (e.g. `videos/camA/`, `videos/camB/`). Each camera gets its own lane; the clean audio is synced once from a reference camera (`--audio-source-camera`, auto-picked by best alignment) so it doesn't double up across angles. Video files left in the root of `--video-dir` when camera sub-folders exist are ignored with a warning.

> **Multiple recorders.** Pass `--audio-file` several times. Every clip aligns against every recorder; the timeline is built from the "primary" (best coverage). `--recorder-mode best` (default) keeps one audio lane with the strongest recorder per clip; `all` gives every recorder its own lane (multiple lavaliers/speakers). **Note:** if your files are just sequential chunks of *one* device (a recorder that splits every 15 min), they share one clock — losslessly concatenate them first (`ffmpeg` concat) instead of passing them as separate recorders.

## Retake Detection

An unedited lecture or monologue recording is often full of flubbed lines that got re-recorded on the spot: the speaker stumbles, stops, and restarts the same line — sometimes several times — before continuing. `--detect-retakes` (off by default) finds these automatically and places a **marker** on each attempt, labelled `Retake N — keep` for the one the speaker settled on and `Retake N — take K` for the discarded ones. The timeline itself is untouched: markers tell you where to look, and the cutting stays yours.

> **Why markers rather than auditions.** Earlier versions exported each group as a Final Cut *audition*. That desynchronised the result: the audition began at the group's start but played the keeper take's audio, which comes from later in the clip, while the picture did not switch at all — takes at [2 s, 4 s] and [5 s, 8 s] put the voice 3 s ahead of the image and left a hole in the clean track. A real audition has to switch the linked A/V ranges together and re-cut the surrounding material, which is a separate feature with its own timing model; a marker conveys exactly the same finding with no risk to the A/V relationship.

```bash
python main.py --cli --video-dir ./videos --audio-file rec.wav --detect-retakes
```

> **How it works.** Detection runs on the recorder's own transcript (already computed for sync), scanning at the word-token level for a short run of words that repeats verbatim shortly after it was first spoken — not just whole-sentence repeats, since a restart is usually a resumed sentence, not a cleanly bounded phrase. Consecutive restarts of the same line chain into one group (2 or more attempts, and three or more identical takes all belong to the same group); nothing is ever deleted or reordered — the algorithm only decides which spans to mark.
>
> **Tuning.** `retake_min_words` (default 4) sets how many words must repeat before it counts as a restart — raise it if short common phrases ("что это", "то есть") are being flagged as false positives. `retake_max_gap_s` (default 6.0s) caps how long a pause may separate two attempts of the same line before they're treated as an unrelated callback instead of a retake.
>
> **This is a heuristic, reviewed non-destructively.** Exact-repeat detection won't catch every paraphrased restart, and can occasionally group a coincidental phrase repetition that isn't really a retake — but since nothing is cut, a false positive is one marker the editor ignores, and a missed retake is no worse than not running the feature at all. A future LLM-based refinement pass (mirroring [Podcast Reels Forge](https://github.com/Bormotoon/Podcast-Reels-Forge)'s local llama.cpp moment-scoring) is planned to catch paraphrased restarts and judge which take was best-delivered, rather than just "the last one."

## Self-Check

`--verify` measures the *realized* lip-sync lag via acoustic cross-correlation — but it's blind to CONTENT defects: a dropped word, a duplicated phrase, or a piece rendered from the wrong recorder span can still show a small lag if enough of the surrounding audio still lines up. `--self-check` (off by default) closes that gap: after a clip's voice monolith is rendered, it re-transcribes that rendered audio with Whisper and compares it word-for-word against the camera clip's own transcript (already computed during alignment). Spans where the two disagree — either a timing drift beyond normal cross-run Whisper jitter, or words that simply don't match — are reported. Two modes build on the same detection:

- **`warn`** — just reports flagged spans for you to check in the NLE. Nothing is re-rendered.
- **`repair`** — additionally re-aligns each flagged span's own small stretch of recorder audio and re-renders only the piece(s) covering it, then re-checks the result once more before accepting the fix. A span that can't be confidently re-aligned (or still doesn't match after the attempt) is reported exactly like a `warn`-mode finding instead of risking a worse edit — the rest of the clip is never touched.

```bash
python main.py --cli --video-dir ./videos --audio-file rec.wav --self-check warn
python main.py --cli --video-dir ./videos --audio-file rec.wav --self-check repair
```

> **How it works (detection).** The rendered voice WAV is transcribed fresh (`self_check_transcribe_mode`, default `fast`) and matched against the camera transcript at the token level via the same normalize+difflib approach used for anchor matching. A run of `self_check_min_run_words` (default 5) or more consecutive matched words whose median timing delta exceeds `self_check_shift_threshold_s` (default 0.35s) is flagged `shifted`; a run of `self_check_min_content_words` (default 5) or more words with no counterpart at all on the other side is flagged `content`. Two discriminators keep transcription noise out (both field-calibrated on real footage whose measured acoustic lag was <25 ms everywhere): a word's delta is the *minimum* over its start and end edges (echo smears word onsets on the camera track by 300-500 ms even when sync is perfect — a real shift moves both edges), and a large delta is discarded when the same word also exists on the camera side at the *right* time (difflib pairing a common word or a repeated phrase with the wrong far-away occurrence, not a render defect).
>
> **How it works (repair).** An ffmpeg render is deterministic — re-rendering the exact same recorder span verbatim would reproduce a content defect byte-for-byte, so the only thing that fixes either a `shifted` or a `content` span is re-deriving where in the recorder this stretch of speech actually comes from. `repair` re-aligns just that neighbourhood: first a transcript re-match restricted to the flagged span plus a few seconds of context (the same normalize+difflib+RANSAC approach used for the whole-clip alignment, just windowed), falling back to a local GCC-PHAT acoustic re-check (as used by Boundary Flex / the acoustic fallback) when there aren't enough words nearby to trust a re-match. The repaired stretch is re-planned with the same sentence-wise piece logic as the main render, rendered, and spliced into the existing monolith — everything outside the flagged span+margin is untouched, byte-for-byte.
>
> **Cost.** This is an extra full Whisper pass per rendered clip (a second pass if any span needed a repair attempt, to verify the fix). The main transcription engine is still unloaded before rendering as usual (rendering is pure ffmpeg); self-check then loads its own engine — one model reload, never two copies of the model in VRAM at once.

## Voice Enhancement

`--voice-enhance` (off by default) runs a third-party model over each rendered voice monolith right after rendering and **before** self-check, so self-check validates whatever audio you actually get. Six variants were compared in a listening test; there's no single best default — the right choice depends on your material and how much time you can spend rendering:

| Mode | What it does | Speed | Notes |
|------|---------------|-------|-------|
| `denoise` | Mel-Roformer noise removal | Fast | Same `.sep-venv` stack as the ambience track. Safest choice — barely touches the voice's own timbre. |
| `denoise_dereverb` | + a second pass stripping room reflections | Fast | Can thin out consonant tails or room warmth on some material — A/B listen before committing to a project. |
| `resemble` | Resemble Enhance (generative) | ~2.5× realtime on a current GPU | Can produce a studio-like timbre, at the risk of subtle generative artifacts on hard passages. **Experimental**, and a **mono** model — a stereo monolith comes back as duplicated mono. Install it into the same `.sep-venv`: `.sep-venv/bin/pip install resemble-enhance`. |
| `sgmse_denoise` / `sgmse_dereverb` | SGMSE+ diffusion denoise/de-reverb | **~5× slower than realtime** | The cleanest result of the six, but only realistic for short clips — needs a separate `.enh-venv`. |
| `reuse` | NVIDIA RE-USE (denoise+dereverb+declip in one pass) | Fast | The strongest single-pass result, but its weights are **NSCLv1 (noncommercial-only)** and it runs via Docker with NVIDIA's own inference code, which you must supply yourself under their license — this project cannot redistribute it. Set `--reuse-source-dir` to where you cloned it. |

```bash
python main.py --cli --video-dir ./videos --audio-file rec.wav --voice-enhance denoise
python main.py --cli --video-dir ./videos --audio-file rec.wav --voice-enhance denoise_dereverb
```

> **Environments.** `denoise`/`denoise_dereverb` reuse the `.sep-venv` environment already used by `--ambience-track` (see `setup_sep_venv.sh`) — nothing extra to install if you already have ambience working. `resemble` goes into that same venv (`.sep-venv/bin/pip install resemble-enhance`); its own `resemble-enhance` console script is bypassed (as of torchaudio 2.9 it needs TorchCodec just to read a file), so WhisperSync drives its Python API directly and does the file I/O itself. `sgmse_*`/`reuse` need environments this repo doesn't bundle (a separate venv for the diffusion models, a Docker image plus NVIDIA's own source for RE-USE) and have no backend in this build yet — they are greyed out in the GUI. `python -m whispersync.engine.system_check` reports which environments are set up, and an unusable mode is now reported **at the start of a run**, not after it: a 3-hour sync no longer ends with "the mode you picked isn't available".
>
> **Sync safety.** Every backend's output is conformed (resampled/padded/trimmed) back to the exact duration, sample rate, and channel count of the original rendered monolith before it replaces it — the property was verified sample-exact across all six variants during the original listening test, and the conform step makes it structural rather than coincidental.

## Configuration

WhisperSync reads a JSON config via `--config config.json`. **Priority: CLI flags > JSON config > defaults.** An unknown key logs a warning (so typos don't silently do nothing), and a missing `--config` path is a hard error.

```json
{
    "model": "large-v3",
    "device": "auto",
    "compute_type": "auto",
    "language": null,
    "vad_filter": true,
    "beam_size": 5,
    "batch_size": 16,
    "transcribe_mode": "fast",
    "quality_beam_size": 10,
    "initial_prompt": "",
    "video_exts": [".mp4", ".mov", ".mxf", ".avi", ".mkv"],
    "audio_exts": [".wav", ".mp3", ".m4a", ".flac"],
    "fcpxml_version": "1.9",
    "default_strategy": 3,
    "cache_dir": null,
    "output_dir": null,
    "use_cache": true,
    "cache_max_age_days": 0,
    "save_transcripts": true,
    "timebase_source": "camera",
    "audio_source_camera": null,
    "camera_av_offset_ms": 0.0,
    "camera_av_offset_ms_by_camera": {},
    "recorder_mode": "best",
    "crossfade_enabled": true,
    "crossfade_ms": 10,
    "output_audio_format": "auto",
    "stretch_method": "auto",
    "seam_snap_max_s": 0.4,
    "render_workers": 0,
    "probe_timeout_s": 30.0,
    "min_anchors": 8,
    "anchor_min_confidence": 0.6,
    "phrase_gap_threshold": 0.6,
    "acoustic_fallback": true,
    "boundary_flex": true,
    "pause_duck_enabled": false,
    "pause_duck_db": -18.0,
    "ambience_track": true,
    "voice_segment_minutes": 0,
    "render_master_wav": false,
    "detect_retakes": false,
    "retake_min_words": 4,
    "retake_similarity": 0.6,
    "retake_max_gap_s": 6.0,
    "self_check_mode": "off",
    "self_check_transcribe_mode": "fast",
    "self_check_min_run_words": 5,
    "self_check_shift_threshold_s": 0.35,
    "self_check_min_content_words": 5,
    "voice_enhance": "off",
    "reuse_source_dir": null
}
```

### Key fields

| Field | Type | Description |
|-------|------|-------------|
| `model` | str | Whisper model (`tiny`, `base`, `small`, `medium`, `large-v3`, or a local path) |
| `device` / `compute_type` | str | `auto` resolves to CUDA when available; compute type picks float16/int8 to fit the hardware |
| `language` | str/null | Language code, or `null` for auto-detect |
| `transcribe_mode` | str | `fast` = batched GPU pipeline; `quality` = sequential with context + anti-hallucination guard (~10× slower, more accurate on hard audio) |
| `default_strategy` | int | Default strategy (`1`/`2`/`3`) — the single source of truth for both GUI and CLI |
| `stretch_method` | str | `auto` (resample on small drift, atempo on large), `atempo`, or `resample` |
| `seam_snap_max_s` | float | Max distance a piece boundary may move to reach an inter-word silence (s) |
| `boundary_flex` | bool | Acoustic sub-frame refinement of each piece's start (default on) |
| `acoustic_fallback` | bool | Waveform cross-correlation fallback for clips with no transcript match (default on) |
| `min_anchors` | int | Minimum anchors to trust a timecode fit (default 8) |
| `anchor_min_confidence` | float | Minimum word confidence to participate in anchor matching (0.0–1.0) |
| `camera_av_offset_ms` (+ `_by_camera`) | float / map | Constant lip-sync calibration added to synced audio positions, globally or per camera sub-folder |
| `render_workers` | int | Parallel ffmpeg render processes (`0` = auto) |
| `render_master_wav` | bool | Also render one WAV spanning the whole timeline (default off) |
| `cache_max_age_days` | float | Delete cached transcripts older than N days at engine start; `0` (default) keeps them forever |
| `voice_segment_minutes` | int | Split each voice WAV into ~N-minute segments cut in silence (`0` = monolith, default) |
| `detect_retakes` | bool | Find re-recorded lines and mark each attempt on the timeline (default off) |
| `retake_min_words` | int | Minimum token-run length to consider a restart candidate (default 4) |
| `retake_max_gap_s` | float | Max pause between consecutive attempts of the same line (default 6.0s) |
| `self_check_mode` | str | `off` / `warn` / `repair` — re-transcribe each rendered clip and flag (or additionally repair) content/timing spans vs. the camera transcript (default `off`) |
| `self_check_transcribe_mode` | str | `fast`/`quality` Whisper mode for the self-check pass, independent of `transcribe_mode` (default `fast`) |
| `self_check_shift_threshold_s` | float | Min-over-edges per-word timing delta (s) above which a run is flagged `shifted` (default 0.35, field-calibrated) |
| `voice_enhance` | str | `off` / `denoise` / `denoise_dereverb` / `resemble` / `sgmse_denoise` / `sgmse_dereverb` / `reuse` — third-party model run over the rendered voice before self-check (default `off`) — see [Voice Enhancement](#voice-enhancement) |
| `reuse_source_dir` | str/null | Directory with NVIDIA's own RE-USE inference source, for `voice_enhance: reuse` only |

## Output Files

Everything lands next to the FCPXML (default: inside your video folder):

| Path | What it is |
|------|------------|
| `sync_output.fcpxml` | The project file — import into Final Cut Pro / DaVinci Resolve |
| `audio_synced/<source-id>_voice.wav` | One continuous synced voice WAV per camera clip, at the recorder's native quality |
| `transcripts/<source-id>.json`, `.srt` | Full transcripts of every recorder and camera clip (word-level timestamps) |
| `ambience/<source-id>_ambience.wav` | Voice-free camera ambience (only with `--ambience-track`) |
| `sync_output_master.wav` | Single WAV spanning the whole timeline (only with `--render-master-wav`) |

Source video and recorder files are never modified.

> **`<source-id>`** is a short id derived from each input's role, camera folder
> and filename (`cam_camA_DJI_0001`, `rec_take`), made unique automatically.
> File stems alone are not identifiers: two recorders can both be `take.wav`,
> one camera can hold `clip.mov` and `clip.mp4`, and two cameras routinely hold
> identically named clips — each of those used to make one source's artifact
> overwrite another's. Display names in the FCPXML are unaffected.

While a run is in progress the output folder also holds `.whispersync-run.lock`
and a `.whispersync-run-<id>/` scratch directory; both are removed when the run
ends. A leftover lock from a crashed run is taken over automatically.

## Verifying the Result

- **`--verify`** — after a successful run, measures the *realized* lag between each rendered voice WAV and its camera audio via GCC-PHAT cross-correlation, printing median/p90/max per clip (and embedding the numbers in `--json` output).
- **`--self-check warn|repair`** — re-transcribes each rendered voice WAV and flags (or repairs) content/timing spans against the camera's own transcript — see [Self-Check](#self-check) for what `--verify` alone can't catch.
- **`tools/verify_sync.py`** — the same measurement as a standalone tool for any pair of audio files: `python -m tools.verify_sync --video camera.mov --voice voice.wav [--json]`. It is a thin wrapper; the measurement itself lives in the shipped package (`whispersync.engine.verify`), so `--verify` works from an installed wheel and not only from a checkout. Exit codes: `0` passed, `1` failed, `2` inconclusive or bad arguments — "inconclusive" is deliberately not a pass, because a clip that could not be measured has not been shown to be in sync.
- **`python -m whispersync.engine.system_check`** — environment audit: ffmpeg, CUDA-via-ctranslate2, dependencies, `.sep-venv`, disk space.

## Troubleshooting

### `CUDA not available, falling back to CPU`

Install the [NVIDIA CUDA Toolkit](https://developer.nvidia.com/cuda-toolkit) and cuDNN, then run `python -m whispersync.engine.system_check` — it checks CUDA through ctranslate2, the exact path the transcription engine uses at runtime (torch is not needed).

### `ffmpeg not found in PATH`

- **Ubuntu/Debian:** `sudo apt install ffmpeg`
- **macOS:** `brew install ffmpeg`
- **Windows:** download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH

### Low anchor count

```
Warning: Only 3 anchors found (minimum: 8)
```

Causes and fixes: a very short clip (more speech helps), heavy noise (keep `vad_filter` on), mismatched languages (set `--language`), quiet recorder signal (check levels). A clip below `min_anchors` is placed by filename order with a warning rather than trusted to a shaky fit. If a clip gets *no* text match against any recorder at all, the acoustic fallback (`acoustic_fallback`, on by default) takes over with a waveform cross-correlation scan — coarser, but it works on music/noise/silence, as long as the same physical sound reaches both mics.

### High residual

```
High residual alignment error: 156.2 ms
```

Drift may not be perfectly linear — try Strategy 2 or 3 (watch for the auto-strategy warning naming the better fit); make sure anchors span the whole clip; try `large-v3` over a small model, or `--mode quality` for hard audio.

### Transcription cache

The cache lives in `~/.cache/whispersync/` (per-platform equivalent elsewhere). The key includes the *resolved* device/compute type, so a GPU run and a CPU-fallback run never collide. To reset: `--no-cache` for one run, delete the folder, or set `cache_max_age_days` for automatic pruning.

## Architecture

```
WhisperSync/
├── main.py                          # Thin shim -> whispersync.app:main (checkout runs)
├── whispersync/
│   ├── app.py                       # Entry point (GUI/CLI dispatch); also the whispersync-gui script
│   ├── cli.py                       # argparse CLI
│   ├── config.py                    # WhisperSyncConfig dataclass + JSON loader
│   ├── models.py                    # Word, Segment, Transcript, Anchor, AlignmentMap, MediaClip, Take, RetakeGroup, SyncPlan, SyncResult
│   ├── engine/
│   │   ├── pipeline.py              # End-to-end orchestration (incl. clip_pieces — the real strategy planner)
│   │   ├── sources.py               # Stable per-input identity (sid) — artifacts are named from it, never from file stems
│   │   ├── workspace.py             # Per-run scratch, output lock, atomic publication
│   │   ├── transcriber.py           # WhisperEngine + SHA-256 cache (+ age pruning)
│   │   ├── matcher.py               # Anchors + RANSAC + outlier filter + acceptance gate + strategy recommendation
│   │   ├── strategies.py            # Strategy registry (id -> name/description)
│   │   ├── retakes.py               # Retake detection (token-level restart matching) → timeline markers
│   │   ├── self_check.py            # Post-render word diagnostics: rendered voice vs. camera transcript
│   │   ├── acoustic.py              # GCC-PHAT cross-correlation: Boundary Flex + acoustic fallback
│   │   ├── separation.py            # Ambience track via the isolated .sep-venv
│   │   ├── timestretch.py           # ffmpeg cut/resample-conform/atempo/assemble/master-mix wrappers
│   │   ├── media.py                 # ffprobe, audio extraction, lossless master, atempo chains
│   │   ├── export.py                # FCPXML generation + structural/reference validation + interval round-trip
│   │   ├── naming.py                # Natural filename sort
│   │   ├── transcript_export.py     # JSON + SRT transcript export
│   │   ├── verify.py                # Realized lip-sync lag measurement (GCC-PHAT) — the --verify backend
│   │   ├── proc.py                  # Long-running subprocesses with disk-backed, bounded logs
│   │   └── system_check.py          # Environment audit
│   └── gui/
│       ├── main_window.py           # PyQt6 MainWindow
│       ├── worker.py                # Background worker (weighted progress, cancellation)
│       ├── theme.qss                # Dark theme
│       └── widgets/                 # DropZone, LogView, TimelinePreview, StrategyDiagram,
│                                    #   SettingsDialog, HelpPage, SyncSimulator
├── tools/verify_sync.py             # CLI wrapper around whispersync.engine.verify
└── tests/                           # pytest suite (unit + ffmpeg integration markers)
```

### Data flow

```
Video files + recorder file(s)
        │
        ▼
   probe() ──────────────► MediaInfo (incl. audio channels / bit depth)
        │
        ▼
   extract_audio_master() ► lossless PCM master per recorder (native channels, target rate)
        │
        ▼
   WhisperEngine.transcribe() ► word-level Transcript (cached)
        │
        ▼
   matcher.align() ─────► anchors → outlier filters → RANSAC → AlignmentMap (K, offset)
        │                  └─ no match? → acoustic_coarse_align() (GCC-PHAT fallback)
        ▼
   clip_pieces() ───────► pieces (rec_start, duration, factor) with seam-snap-to-silence
        │
        ▼
   refine_piece_boundaries() ► Boundary Flex sub-frame refinement (optional, default on)
        │
        ▼
   shared render pool ──► pieces cut/conformed in parallel → assemble_continuous()
        │                  (native channels/bit depth, inline pause ducking)
        ▼
   generate_fcpxml() ───► .fcpxml + audio_synced/*.wav [+ master WAV, ambience, transcripts]
```

## Development & Contributing

Contributions are welcome — see:

- [CONTRIBUTING.md](CONTRIBUTING.md) — environment setup, code style, PR process
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — community rules
- [SECURITY.md](SECURITY.md) — private vulnerability reporting
- [CHANGELOG.md](CHANGELOG.md) — release history
- [PROJECT_ANALYSIS.md](PROJECT_ANALYSIS.md) — the full technical audit behind the current design

Before opening a PR, make sure the checks pass:

```bash
ruff check .
black --check .
mypy whispersync/ tools/ main.py
pytest                      # unit tests
pytest -m integration       # ffmpeg-backed integration tests
```

CI runs the same suite on Python 3.10–3.14 (dependencies locked on 3.12 for reproducibility).

## Support the Project

WhisperSync is free for noncommercial use and built in the author's spare time. If it saves you hours of manual syncing, consider supporting development:

[![Support WhisperSync — buy the author a coffee](docs/images/donate_banner.png)](https://www.donationalerts.com/r/bormotoon)

## License

WhisperSync is **source-available** and **free for noncommercial use** under the [PolyForm Noncommercial License 1.0.0](LICENSE).

You may use, copy, modify, and share it for any noncommercial purpose — personal projects, hobby shoots, research, education, and use by charitable/public organizations are all expressly permitted.

**Commercial use requires a separate license.** If you want to use WhisperSync in a commercial product or for commercial work, please [open an issue](https://github.com/Bormotoon/WhisperSync/issues) to discuss commercial licensing.

## Acknowledgements

WhisperSync stands on excellent open technology:

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / [CTranslate2](https://github.com/OpenNMT/CTranslate2) — fast Whisper inference
- [OpenAI Whisper](https://github.com/openai/whisper) — the speech-recognition model family
- [FFmpeg](https://ffmpeg.org/) — all audio surgery
- [PyQt6](https://riverbankcomputing.com/software/pyqt/) — the GUI toolkit
- [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) — the optional ambience separation
