"""Configuration management for WhisperSync."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir

logger = logging.getLogger(__name__)

APP_NAME = "whispersync"


class ConfigError(ValueError):
    """A configuration value that would make the run wrong or impossible.

    Raised by ``WhisperSyncConfig.validate`` before any expensive work starts.
    Dataclass annotations are not validation — ``WhisperSyncConfig(**json)``
    accepted ``seed_bin_width=0`` (a later division by zero), the string
    ``"false"`` for a boolean (truthy, so the feature silently stayed ON),
    ``NaN``/``Infinity`` for any float (poisoning every time calculation
    downstream) and unknown enum values (a quietly different code path). Each
    of those surfaced hours later, if at all, as a strange result rather than
    an error anyone could act on.
    """


DEFAULT_VIDEO_EXTS = [".mp4", ".mov", ".mxf", ".avi", ".mkv"]
DEFAULT_AUDIO_EXTS = [".wav", ".mp3", ".m4a", ".flac"]

MIN_ANCHORS = 8
ANCHOR_MIN_CONFIDENCE = 0.6

# Minimum silence (seconds) between consecutive anchors to split speech blocks
# in the silence-padding / hybrid strategies.
PHRASE_GAP_THRESHOLD = 0.6

# Coarse-then-fine matching for long recordings: a clip is first roughly located
# in the (possibly multi-hour) reference by rare-word voting, then matched
# precisely only inside a window around that estimate.
MATCH_WINDOW_MARGIN = 90.0  # seconds of slack added around the coarse estimate
SEED_MAX_OCCURRENCES = 50  # ignore tokens appearing more than this in the reference
SEED_BIN_WIDTH = 2.0  # seconds — histogram bin width for the coarse-offset vote

WHISPER_BEAM_SIZE = 5

# Anti-hallucination temperature fallback ladder (proven on real podcast audio):
# a segment with high compression_ratio (repeats) or low logprob is retried at
# the next temperature, which kills the endless "Спасибо." loop on silence/music.
WHISPER_TEMPERATURE_LADDER = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

# Which source provides the audio sample-rate reference for FCPXML time values.
TIMEBASE_SOURCES = ("camera", "recorder")

# GCC-PHAT cross-correlation parameters, shared by Boundary Flex (below).
# Sharpness = peak/median(|cc|); measured speech windows score 240-335 and
# silence/mismatch ~12.
ACOUSTIC_MAX_LAG_S = 1.0
GCC_EPS = 1e-8

# Boundary Flex: acoustically refine each rendered piece's recorder start time by
# GCC-PHAT (camera audio ↔ recorder audio) so speech lands under the picture to
# sub-frame accuracy, independent of Whisper's ±50–100ms word timings. A piece's
# start is nudged only when the measurement is confident (sharpness gate) AND the
# residual exceeds a deadband (below it, the correction is within GCC noise and
# would inject jitter rather than remove it). Off by default.
FLEX_WINDOW_S = 4.0  # cross-correlation window per boundary
FLEX_MIN_SHARPNESS = 80.0  # stricter than the grid gate — small windows need a clear peak
FLEX_DEADBAND_S = 0.025  # ignore corrections within measurement noise (~25 ms)
FLEX_MAX_SHIFT_S = 0.15  # clamp any single boundary nudge

# Pause ducking: attenuate the recorder audio during inter-phrase pauses (gaps
# between speech blocks in the transcript) so a slightly mis-synced ambience/room
# tone in those gaps is inaudible. Gain is in dB (0 = no change … -inf = full
# silence); a short equal-power fade at each pause edge avoids clicks. Off by
# default; the gap that counts as a pause reuses phrase_gap_threshold.
PAUSE_DUCK_DB = -18.0
PAUSE_DUCK_FADE_MS = 80
PAUSE_DUCK_MIN_PAUSE_S = 0.6  # don't duck gaps shorter than this

# Ambience track: run a source-separation model over the camera audio to strip the
# camera's own (echoey, slightly-mis-synced) voice and keep only the room tone /
# ambience, placed on its own lane next to the clean synced voice. Off by default.
# The separator lives in the isolated ".sep-venv" environment (see separation.py);
# MelBand-RoFormer Inst V2 is the chosen model (best ambience-detail retention).
AMBIENCE_MODEL = "melband_roformer_inst_v2.ckpt"

# Accepted values for ``voice_enhance``. Kept here (not imported from
# engine.enhance) so validating a config never pulls in the heavy engine.
VOICE_ENHANCE_MODES = (
    "off",
    "denoise",
    "denoise_dereverb",
    "resemble",
    "sgmse_denoise",
    "sgmse_dereverb",
    "reuse",
)


@dataclass
class WhisperSyncConfig:
    model: str = "large-v3"
    # "auto" resolves to cuda when available, else cpu. compute_type "auto" picks
    # float16 on modern CUDA (capability >= 7.0), int8_float16 on older GPUs,
    # float32 on CPU.
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = None
    vad_filter: bool = True
    beam_size: int = WHISPER_BEAM_SIZE
    # Batched GPU inference — the main speed lever (esp. for multi-hour recorders).
    # On CUDA OOM the engine auto-halves the batch, then falls back to CPU.
    batch_size: int = 16
    best_of: int = 1
    patience: float = 1.0
    condition_on_previous_text: bool = False
    repetition_penalty: float = 1.1
    no_repeat_ngram_size: int = 3
    # "fast" = batched pipeline (no cross-segment context, ~real-time on GPU).
    # "quality" = sequential pipeline with context + hallucination guard: more
    # accurate on hard/quiet audio but ~10x slower.
    transcribe_mode: str = "fast"
    quality_beam_size: int = 10
    # Optional domain context to bias vocabulary (helps both modes).
    initial_prompt: str = ""
    video_exts: list[str] = field(default_factory=lambda: list(DEFAULT_VIDEO_EXTS))
    audio_exts: list[str] = field(default_factory=lambda: list(DEFAULT_AUDIO_EXTS))
    fcpxml_version: str = "1.9"
    # Hybrid (3) is the recommended default out of the box — near-perfect
    # alignment at roughly half the distortion of pure stretching. This is the
    # single source of truth for the default strategy: CLI (--strategy) and GUI
    # (the pre-checked radio) both read it instead of hard-coding their own
    # value, which used to disagree (CLI defaulted to 1, GUI pre-checked 4).
    # See PROJECT_ANALYSIS.md §4.4.
    default_strategy: int = 3
    cache_dir: str | None = None
    output_dir: str | None = None
    use_cache: bool = True
    # Transcript-cache retention: cache entries older than this many days are
    # deleted on engine startup. 0 (default) keeps them forever — transcripts
    # are expensive to recompute and cheap to store, but a busy machine
    # churning through many one-off projects can cap growth here.
    cache_max_age_days: float = 0.0
    # Save full transcripts (JSON + SRT) of every recorder and camera clip next
    # to the output, under output/transcripts/.
    save_transcripts: bool = True
    timebase_source: str = "camera"
    # Multicam: name (subfolder) of the camera the synced audio is derived from.
    # None = auto-pick the camera with the strongest alignment.
    audio_source_camera: str | None = None
    # Constant per-camera lip-sync calibration (milliseconds), added to every
    # synced audio clip's timeline offset. Acoustic methods (Boundary Flex,
    # the coarse text match) align recorder audio to CAMERA AUDIO — they
    # cannot see or correct a fixed mic-to-lips delay baked into a specific
    # camera's own audio pipeline (e.g. an internal ADC/encoder latency).
    # That constant is invisible to any audio-only measurement and needs a
    # one-time calibration (e.g. a clap synced by eye, frame-stepped) to
    # find. `camera_av_offset_ms` is the default for every camera;
    # `camera_av_offset_ms_by_camera` overrides it per camera sub-folder
    # name. Positive = delay the synced audio (recorder audio arrives EARLY
    # relative to this camera's lips); negative = advance it.
    camera_av_offset_ms: float = 0.0
    camera_av_offset_ms_by_camera: dict[str, float] = field(default_factory=dict)
    # Multiple recorders (different devices): "best" = one audio lane, each clip
    # synced from its best-matching recorder; "all" = every recorder on its own
    # audio lane (-1, -2, …) for multi-mic / multi-speaker setups.
    recorder_mode: str = "best"
    # Short equal-power fades at audio segment seams to declick joints (mainly
    # for the Local Time-Stretch strategy). Length-preserving, so no extra drift.
    # Only applied where a seam is not acoustically contiguous with its neighbour
    # (see pipeline._piece_seam_fades) — a fade on every piece boundary would carve
    # an audible volume dip into otherwise-continuous recorder audio.
    crossfade_enabled: bool = True
    crossfade_ms: int = 10
    # Render-path audio quality (see PROJECT_ANALYSIS.md §2.0). "auto" preserves
    # the recorder's channel count and a lossless PCM codec matching its bit depth
    # end to end, instead of the old hard-coded 16-bit mono. Only "auto" is
    # supported today; the field exists so a future explicit override is additive.
    output_audio_format: str = "auto"
    # Tempo-conform method for each rendered piece. "auto" uses a transparent
    # resample ("varispeed") for small factors (|factor-1| <= a few tenths of a
    # percent — real clock drift) and falls back to atempo (WSOLA) for larger
    # corrections; "atempo"/"resample" force one method. See
    # timestretch.RESAMPLE_CONFORM_MAX_DEVIATION.
    stretch_method: str = "auto"
    # Seam-snap-to-silence: interior piece boundaries (Local Time-Stretch / Hybrid)
    # are nudged to the nearest inter-word silence in the recorder, within this
    # many seconds, so a seam never lands mid-word — the actual fix for the
    # mid-word tempo-break stutter ("подготовил" -> "подга-га-товил"). Unlike the
    # old factor-smoothing approach (removed: it fixed the stutter by averaging
    # atempo factors, but that redistributed each piece's output length and let
    # speech drift off the picture by up to ~1.4s), this only moves WHERE the cut
    # happens — every piece's tempo factor, and hence the sync, is unchanged.
    seam_snap_max_s: float = 0.4
    # Parallelism for the CPU-bound audio render (ffmpeg has no GPU audio filters):
    # each piece / Flex window is an independent ffmpeg call, spread across a process
    # pool. 0 = auto (os.cpu_count()); 1 = sequential. Output is identical regardless.
    render_workers: int = 0
    # ffprobe timeout (seconds) when reading each media file's metadata. The
    # default is generous for local files; raise it for clips on slow
    # network/NAS storage that can legitimately take longer to respond.
    probe_timeout_s: float = 30.0
    min_anchors: int = MIN_ANCHORS
    anchor_min_confidence: float = ANCHOR_MIN_CONFIDENCE
    phrase_gap_threshold: float = PHRASE_GAP_THRESHOLD
    match_window_margin: float = MATCH_WINDOW_MARGIN
    seed_max_occurrences: int = SEED_MAX_OCCURRENCES
    seed_bin_width: float = SEED_BIN_WIDTH
    # GCC-PHAT cross-correlation parameters shared by Boundary Flex and the
    # acoustic fallback below.
    acoustic_max_lag_s: float = ACOUSTIC_MAX_LAG_S
    gcc_eps: float = GCC_EPS
    # Acoustic fallback ("Strategy 0"): when a clip can't be aligned to any
    # recorder via the transcript (too little transcribable speech — music,
    # background noise, a language Whisper garbles, near-silence), fall back
    # to a coarse GCC-PHAT cross-correlation grid scan across the whole
    # recorder span to estimate offset/K directly from the waveforms. Turns
    # "no usable words" from a hard failure into a still-working (if less
    # precise) placement, as long as the same physical audio event reaches
    # both the camera and the recorder mic. On by default; the anchors list
    # is empty for a fallback match, so clip_pieces uses one global tempo
    # conform for that clip regardless of the chosen strategy. See
    # PROJECT_ANALYSIS.md §10.2.
    acoustic_fallback: bool = True
    acoustic_fallback_grid_s: float = 30.0
    acoustic_fallback_window_s: float = 8.0
    acoustic_fallback_min_sharpness: float = 50.0
    # --- alignment acceptance gate (see matcher.evaluate_alignment) ---
    # A clock map is checked against these BEFORE anything is placed or
    # rendered with it. Existence is not quality: two anchors define a line
    # exactly, so a near-zero residual over a two-point fit is evidence of
    # nothing — a synthetic false match reproduced k=10 with sub-millisecond
    # residual. A map that fails the gate becomes an explicit "unresolved"
    # clip (placed by filename order, and reported) instead of a confident
    # wrong render.
    #
    # Max |k - 1|. Two devices recording the same event drift by parts per
    # million; percent-level ratios are wrong matches, not clock drift. Raise
    # this only for deliberately speed-changed material.
    alignment_max_k_deviation: float = 0.05
    # Max median residual (ms) of a transcript fit. Whisper word timings are
    # themselves ±50-100 ms, so this is deliberately well above that.
    alignment_max_residual_ms: float = 250.0
    # The supporting evidence must span at least this fraction of the clip.
    # A fit whose anchors all sit in the first 3 s of a 10-minute clip is an
    # extrapolation across the other 597 s, however tight it looks.
    alignment_min_coverage: float = 0.25
    # Minimum unambiguous grid points behind an ACOUSTIC map (its anchor list
    # is empty by construction, so min_anchors cannot judge it).
    alignment_min_acoustic_points: int = 3
    # Boundary Flex: acoustically nudge each piece's recorder start so speech
    # lands under the picture to sub-frame accuracy. On by default — it's the
    # best-out-of-the-box lip-sync setting (the GUI pre-checked this while the
    # CLI defaulted it off; this is the single source of truth both now read).
    # Costs a little extra processing; disable for the fastest run.
    boundary_flex: bool = True
    flex_window_s: float = FLEX_WINDOW_S
    flex_min_sharpness: float = FLEX_MIN_SHARPNESS
    flex_deadband_s: float = FLEX_DEADBAND_S
    flex_max_shift_s: float = FLEX_MAX_SHIFT_S
    # Pause ducking (off by default): attenuate inter-phrase pauses by pause_duck_db
    # (0 dB = off … -inf = full silence) to hide ambience desync in gaps.
    pause_duck_enabled: bool = False
    pause_duck_db: float = PAUSE_DUCK_DB
    pause_duck_fade_ms: int = PAUSE_DUCK_FADE_MS
    pause_duck_min_pause_s: float = PAUSE_DUCK_MIN_PAUSE_S
    # Ambience track (ON by default — validated on real shoots): extract
    # voice-free camera ambience onto its own lane so the only voice is the
    # clean synced one (no doubled/echoed voice). Requires the .sep-venv
    # environment; silently skipped (with a warning) when it isn't set up.
    ambience_track: bool = True
    ambience_model: str = AMBIENCE_MODEL
    # Voice enhancement (off by default): run a third-party model over the
    # rendered voice monolith before self-check, on its own lane's worth of
    # audio (not the ambience track — that stays untouched). Six variants were
    # compared in a listening test; each trades speed/quality/license
    # differently, so the user picks per project instead of a single default:
    #   "off"              — no enhancement (default).
    #   "denoise"          — Mel-Roformer denoise via .sep-venv. Fast, safest;
    #                        same stack as ambience_track.
    #   "denoise_dereverb" — + a second .sep-venv pass (De-Reverb model).
    #                        Fast; can thin out room tone/consonant tails —
    #                        listen before committing to a project.
    #   "resemble"         — Resemble Enhance (generative, studio-like
    #                        timbre). Needs resemble-enhance installed into
    #                        .sep-venv; EXPERIMENTAL — some upstream compat
    #                        patches are undocumented, may need re-patching.
    #   "sgmse_denoise"    — SGMSE+ diffusion denoise (separate ".enh-venv").
    #                        Cleanest result, but ~5x slower than realtime —
    #                        impractical for anything but short clips.
    #   "sgmse_dereverb"   — SGMSE+ diffusion de-reverb, same venv/cost.
    #   "reuse"            — NVIDIA RE-USE/SEMamba via Docker. Fast, handles
    #                        noise+reverb+clipping in one pass, but the model
    #                        is NSCLv1 (noncommercial-only) and its inference
    #                        code is NVIDIA all-rights-reserved — cannot be
    #                        redistributed; the repo only ships the generic
    #                        Docker environment, you supply NVIDIA's own
    #                        RE-USE source under their license (see README).
    # Any mode whose environment isn't set up is skipped with a warning,
    # keeping the unenhanced audio — never a hard failure. See engine/enhance.py.
    voice_enhance: str = "off"
    # Directory containing NVIDIA's own RE-USE inference code (git-cloned by
    # the user under NVIDIA's license — never shipped by this repo), mounted
    # into the "reuse-se:blackwell" Docker container at run time. Only used
    # when voice_enhance == "reuse".
    reuse_source_dir: str | None = None
    # Retake detection (off by default): find lines the speaker re-recorded
    # back-to-back in an unedited monologue (flub → stop → say it again) and
    # export each set of attempts as a Final Cut *audition* — the alternatives
    # stacked under one active pick (the last/best take) instead of every
    # flubbed attempt cluttering the timeline. Detection is transcript-based
    # (consecutive near-duplicate speech blocks); the thresholds below tune it.
    detect_retakes: bool = False
    retake_min_words: int = 4  # ignore blocks shorter than this (interjections)
    retake_similarity: float = 0.6  # min token-sequence similarity to call two blocks the same line
    retake_max_gap_s: float = 6.0  # max pause between consecutive attempts of one line
    # Post-render self-check (off by default): after a clip's voice monolith is
    # rendered, re-transcribe it with Whisper and compare its words against the
    # camera clip's OWN transcript (already computed during alignment) to catch
    # CONTENT defects that --verify's acoustic GCC-PHAT lag measurement can't
    # see (a dropped/duplicated word, a piece built from the wrong recorder
    # span) — see engine/self_check.py. "off" disables it; "warn" reports
    # flagged spans as warnings only; "repair" additionally re-aligns each
    # flagged span's own small stretch of recorder audio (transcript re-match,
    # falling back to a local acoustic re-check) and re-renders only the
    # pieces inside that span, then re-checks it once more before accepting
    # the fix — a span it can't confidently re-align, or that still doesn't
    # pass after repair, is left as a warning instead of a worse edit.
    self_check_mode: str = "off"
    # "fast" reuses the same batched pipeline as fast camera/recorder
    # transcription; "quality" is the slower sequential+context pipeline (see
    # transcribe_mode). Self-check runs AFTER the main Whisper engine was
    # already unloaded for rendering, so this is a deliberate second model
    # load/VRAM cost independent of transcribe_mode — "fast" is the sensible
    # default since self-check only needs to catch gross defects, not match
    # transcribe_mode's own accuracy bar.
    self_check_transcribe_mode: str = "fast"
    # Detection thresholds, field-calibrated on real footage (see
    # engine/self_check.py): the softest values that produced ZERO false
    # spans on a clip whose measured acoustic lag was <25 ms everywhere.
    self_check_min_run_words: int = 5  # words in a run before a timing shift can be flagged
    self_check_shift_threshold_s: float = 0.35  # min-over-edges per-word delta, beyond echo jitter
    self_check_min_content_words: int = 5  # words in a mismatch before it's flagged as content
    # Split each rendered voice WAV into segments of this many minutes
    # (0 = keep one continuous file per clip, the default). Cut points snap
    # to the quietest moment near each nominal boundary, so a cut never lands
    # inside speech. Useful when the NLE's own audio sync (e.g. FCPX
    # "Synchronize Clips") re-aligns each audio item independently: shorter
    # items let it correct residual drift every N minutes instead of once
    # per clip. Typical choices: 1, 2, 3, 5 or 10.
    voice_segment_minutes: int = 0
    # Render a single WAV spanning the whole timeline (every synced voice clip,
    # and the ambience track if enabled, mixed at their timeline offsets over a
    # silent bed) next to the FCPXML, for users without an NLE to drop the
    # project into. Off by default (one extra full-length render).
    render_master_wav: bool = False

    # --- validation ---------------------------------------------------

    def validate(self) -> WhisperSyncConfig:
        """Check the FINAL configuration (file + CLI/GUI overrides merged).

        Runs once, in one place, before transcription begins: a bad value must
        cost the user a message, not an hour of GPU time followed by a
        traceback from somewhere deep in the render. Returns ``self`` so it can
        be chained. Raises :class:`ConfigError` with a message naming the
        field, what it got and what is allowed.
        """
        problems: list[str] = []

        def _bool(name: str) -> None:
            value = getattr(self, name)
            if not isinstance(value, bool):
                problems.append(
                    f"{name}: expected true/false, got {value!r} — note that the "
                    'STRING "false" is a true value in JSON-loaded config'
                )

        def _number(
            name: str,
            *,
            minimum: float | None = None,
            maximum: float | None = None,
            allow_zero: bool = True,
        ) -> None:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(f"{name}: expected a number, got {value!r}")
                return
            if not math.isfinite(float(value)):
                problems.append(f"{name}: must be a finite number, got {value!r}")
                return
            if not allow_zero and value == 0:
                problems.append(f"{name}: must not be zero")
            if minimum is not None and value < minimum:
                problems.append(f"{name}: must be >= {minimum}, got {value!r}")
            if maximum is not None and value > maximum:
                problems.append(f"{name}: must be <= {maximum}, got {value!r}")

        def _choice(name: str, allowed: tuple[str, ...]) -> None:
            value = getattr(self, name)
            if value not in allowed:
                problems.append(f"{name}: {value!r} is not one of {', '.join(allowed)}")

        _choice("timebase_source", TIMEBASE_SOURCES)
        _choice("recorder_mode", ("best", "all"))
        _choice("transcribe_mode", ("fast", "quality"))
        _choice("self_check_transcribe_mode", ("fast", "quality"))
        _choice("self_check_mode", ("off", "warn", "repair"))
        _choice("stretch_method", ("auto", "atempo", "resample"))
        _choice("output_audio_format", ("auto",))
        _choice("voice_enhance", VOICE_ENHANCE_MODES)

        if self.default_strategy not in (1, 2, 3):
            problems.append(f"default_strategy: {self.default_strategy!r} is not one of 1, 2, 3")

        for name in (
            "vad_filter",
            "condition_on_previous_text",
            "use_cache",
            "save_transcripts",
            "crossfade_enabled",
            "acoustic_fallback",
            "boundary_flex",
            "pause_duck_enabled",
            "ambience_track",
            "detect_retakes",
            "render_master_wav",
        ):
            _bool(name)

        # Anything used as a divisor, a bin width or a window must be strictly
        # positive — zero turns into a division by zero or an infinite loop,
        # and a negative value silently reverses a search direction.
        for name in (
            "seed_bin_width",
            "phrase_gap_threshold",
            "probe_timeout_s",
            "flex_window_s",
            "acoustic_fallback_grid_s",
            "acoustic_fallback_window_s",
            "acoustic_max_lag_s",
            "gcc_eps",
            "pause_duck_min_pause_s",
        ):
            _number(name, minimum=0.0, allow_zero=False)

        _number("beam_size", minimum=1)
        _number("quality_beam_size", minimum=1)
        _number("batch_size", minimum=1)
        _number("best_of", minimum=1)
        _number("patience", minimum=0.0, allow_zero=False)
        _number("min_anchors", minimum=2)
        _number("anchor_min_confidence", minimum=0.0, maximum=1.0)
        _number("match_window_margin", minimum=0.0)
        _number("seed_max_occurrences", minimum=1)
        _number("cache_max_age_days", minimum=0.0)
        _number("crossfade_ms", minimum=0)
        _number("seam_snap_max_s", minimum=0.0)
        _number("render_workers", minimum=0)
        _number("voice_segment_minutes", minimum=0)
        _number("camera_av_offset_ms")
        _number("pause_duck_db", maximum=0.0)
        _number("pause_duck_fade_ms", minimum=0)
        _number("flex_min_sharpness", minimum=0.0)
        _number("flex_deadband_s", minimum=0.0)
        _number("flex_max_shift_s", minimum=0.0)
        _number("acoustic_fallback_min_sharpness", minimum=0.0)
        _number("alignment_max_k_deviation", minimum=0.0, allow_zero=False)
        _number("alignment_max_residual_ms", minimum=0.0)
        _number("alignment_min_coverage", minimum=0.0, maximum=1.0)
        _number("alignment_min_acoustic_points", minimum=2)
        _number("retake_min_words", minimum=1)
        _number("retake_similarity", minimum=0.0, maximum=1.0)
        _number("retake_max_gap_s", minimum=0.0)
        _number("self_check_min_run_words", minimum=1)
        _number("self_check_shift_threshold_s", minimum=0.0, allow_zero=False)
        _number("self_check_min_content_words", minimum=1)

        # Mutual constraints — each of these is individually valid but the
        # combination cannot do what it says.
        if self.flex_deadband_s > self.flex_max_shift_s:
            problems.append(
                f"flex_deadband_s ({self.flex_deadband_s}) exceeds flex_max_shift_s "
                f"({self.flex_max_shift_s}): every correction would be ignored"
            )
        if self.acoustic_fallback_window_s > self.acoustic_fallback_grid_s * 8:
            problems.append(
                f"acoustic_fallback_window_s ({self.acoustic_fallback_window_s}) is very "
                f"large relative to acoustic_fallback_grid_s ({self.acoustic_fallback_grid_s})"
            )

        for name in ("video_exts", "audio_exts"):
            value = getattr(self, name)
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                problems.append(f"{name}: expected a list of extension strings, got {value!r}")
            elif not all(v.startswith(".") for v in value):
                problems.append(f"{name}: every extension must start with '.', got {value!r}")

        if not isinstance(self.camera_av_offset_ms_by_camera, dict) or not all(
            isinstance(k, str) and isinstance(v, (int, float)) and math.isfinite(float(v))
            for k, v in self.camera_av_offset_ms_by_camera.items()
        ):
            problems.append(
                "camera_av_offset_ms_by_camera: expected {camera name: finite milliseconds}"
            )

        if problems:
            raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(problems))
        return self

    @property
    def resolved_cache_dir(self) -> Path:
        if self.cache_dir:
            return Path(self.cache_dir)
        return Path(user_cache_dir(APP_NAME))

    @property
    def resolved_output_dir(self) -> Path:
        if self.output_dir:
            return Path(self.output_dir)
        return Path.cwd() / "output"

    @property
    def resolved_config_dir(self) -> Path:
        return Path(user_config_dir(APP_NAME))

    @classmethod
    def from_file(cls, path: Path) -> WhisperSyncConfig:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except OSError as e:
            raise ConfigError(f"Could not read config file {path}: {e}") from e
        except UnicodeDecodeError as e:
            raise ConfigError(f"Config file {path} is not valid UTF-8: {e}") from e
        except json.JSONDecodeError as e:
            raise ConfigError(
                f"Config file {path} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}"
            ) from e
        if not isinstance(data, dict):
            raise ConfigError(
                f"Config file {path} must contain a JSON object, got {type(data).__name__}"
            )
        # Silently dropping unknown keys used to hide typos (e.g. a config
        # written with "pause_duck_dB" instead of "pause_duck_db" would just
        # never take effect, with no indication why). Warn about anything that
        # isn't a real field. See PROJECT_ANALYSIS.md §2.9.
        known = cls.__dataclass_fields__
        unknown = sorted(set(data) - set(known))
        if unknown:
            logger.warning(
                "Unknown config key(s) in %s (ignored — check for typos): %s",
                path,
                ", ".join(unknown),
            )
        return cls(**{k: v for k, v in data.items() if k in known})

    def merge_cli_args(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            if value is not None and hasattr(self, key):
                setattr(self, key, value)


def load_config(
    config_path: Path | None = None,
    validate: bool = True,
    **cli_overrides: object,
) -> WhisperSyncConfig:
    """Load, merge and (by default) VALIDATE the effective configuration.

    Validation happens after the overrides are merged, because that merged
    object is what the run will actually use — validating the file alone would
    miss a bad CLI flag, and validating each source separately would miss the
    combinations only the merge produces.
    """
    if config_path is not None:
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        cfg = WhisperSyncConfig.from_file(config_path)
    else:
        cfg = WhisperSyncConfig()
    cfg.merge_cli_args(**cli_overrides)
    if validate:
        cfg.validate()
    return cfg
