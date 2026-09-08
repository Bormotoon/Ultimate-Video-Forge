"""Whisper-based transcription engine with caching.

Hardware/decoding settings are ported from the production Podcast Reels Forge
pipeline (RTX 5060 Ti 16GB): auto device/compute-type resolution, batched GPU
inference with an OOM fallback ladder, and the anti-hallucination decoding
controls that keep the transcript clean (which directly improves anchor quality).
"""

from __future__ import annotations

import contextlib
import gc
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# HF Xet protocol hangs on some networks (CLOSE-WAIT socket); force plain HTTP.
# Must be set before faster_whisper/huggingface_hub import.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from whispersync.config import WHISPER_TEMPERATURE_LADDER, WhisperSyncConfig
from whispersync.models import Segment, Transcript, Word

try:
    import torch

    _TORCH_AVAILABLE = True
except ImportError:  # pragma: no cover - torch optional at import time
    torch = None  # type: ignore[assignment]
    _TORCH_AVAILABLE = False

logger = logging.getLogger(__name__)


def _finite(value: object) -> float:
    """``float(value)`` when it is a real, finite number; else ``ValueError``.

    JSON happily round-trips ``NaN`` and ``Infinity``, and a cached transcript
    carrying either would propagate silently into every downstream time
    calculation — producing pieces of infinite length rather than an error
    anyone could act on.
    """
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number):
        raise ValueError(f"non-finite value {value!r}")
    return number


# CUDA compute capability at/above which float16 is the sensible default.
_CUDA_FLOAT16_MAJOR = 7


def _ct2_cuda_available() -> bool:
    """Whether CUDA is usable by faster-whisper (ctranslate2), independent of torch."""
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:  # pragma: no cover - ctranslate2 is always present with faster_whisper
        return False


def resolve_device(requested: str) -> str:
    """Resolve a requested device ("auto"/"cuda"/"cpu") to an available one.

    faster-whisper runs on ctranslate2, not torch, so CUDA can be used even when
    torch is absent — we fall back to ctranslate2's own device probe.
    """
    req = str(requested).strip().lower()
    cuda = (torch is not None and torch.cuda.is_available()) or _ct2_cuda_available()
    if req == "auto":
        return "cuda" if cuda else "cpu"
    if req == "cuda":
        if cuda:
            return "cuda"
        logger.warning("CUDA not available; falling back to CPU")
    return "cpu"


def _default_compute_type(device: str) -> str:
    if device != "cuda":
        return "float32"
    if torch is None:
        # No torch to probe capability; float16 is the right default for any modern
        # CUDA GPU (verified on the RTX 5060 Ti via ctranslate2 4.8).
        return "float16"
    try:
        major, _minor = torch.cuda.get_device_capability()
    except (RuntimeError, AttributeError):
        return "float32"
    return "float16" if major >= _CUDA_FLOAT16_MAJOR else "int8_float16"


def select_compute_type(device: str, requested: str) -> str:
    """Resolve compute_type, honouring an explicit value or "auto"."""
    if requested and requested.strip().lower() != "auto":
        return requested
    return _default_compute_type(device)


def _is_cuda_oom(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "out of memory" in msg and any(k in msg for k in ("cuda", "cudnn", "cublas", "gpu"))


def _local_model_path(model: str) -> str | None:
    """The path the model can be loaded from WITHOUT touching the network:
    the model string itself when it's already a local CTranslate2 directory,
    else the complete Hugging Face cache snapshot, else ``None`` (a download
    is genuinely needed).

    Loading from the returned path skips huggingface_hub entirely — without
    this, every single start re-checked the model revision online and printed
    "Fetching 5 files" progress bars, which looked exactly like the (long
    finished) 3 GB download happening all over again.
    """
    if Path(model).is_dir():
        return model
    try:
        from faster_whisper.utils import download_model

        return str(download_model(model, local_files_only=True))
    except Exception:  # any failure means "not available locally"
        return None


# Cache entries are named "<64 hex chars>.json" and carry this marker, so
# pruning can prove an entry is ours before deleting it.
CACHE_SCHEMA = "whispersync/transcript-cache/1"
_CACHE_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")


def transcripts_cache_dir(cache_dir: Path) -> Path:
    """The subdirectory this app owns inside ``cache_dir``.

    ``cache_dir`` is user-configurable and may well be a directory holding
    other things. Writing (and especially pruning) directly in it treated the
    whole directory as ours: with ``cache_max_age_days`` set, retention deleted
    every old ``*.json`` it found there — an unrelated ``/shared/report.json``
    included. Everything lives under our own subdirectory now, and pruning
    still verifies each file before removing it.
    """
    return cache_dir / "transcripts"


def _is_own_cache_entry(entry: Path) -> bool:
    """Whether ``entry`` is a transcript cache file this app wrote.

    Two independent proofs are required — the name shape AND the schema marker
    inside — because either alone can coincide with somebody else's file.
    """
    if not _CACHE_NAME_RE.match(entry.name):
        return False
    try:
        with entry.open("rb") as fh:
            head = fh.read(4096).decode("utf-8", errors="replace")
    except OSError:
        return False
    return CACHE_SCHEMA in head


def _prune_cache(cache_dir: Path, max_age_days: float) -> int:
    """Delete cached transcripts older than ``max_age_days`` (by mtime).

    Called once per engine start when ``config.cache_max_age_days > 0``.
    Only files this app can prove it wrote are removed (see
    ``_is_own_cache_entry``); anything else in the directory is left alone.
    Returns the number of entries removed; a filesystem error on an individual
    entry is skipped (a half-pruned cache is still a valid cache).
    """
    own_dir = transcripts_cache_dir(cache_dir)
    if not own_dir.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400.0
    removed = 0
    for entry in own_dir.glob("*.json"):
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if not _is_own_cache_entry(entry):
                logger.debug("Cache prune: skipping unrecognised file %s", entry)
                continue
            entry.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Pruned %d cached transcript(s) older than %g day(s)", removed, max_age_days)
    return removed


class WhisperEngine:
    def __init__(
        self,
        config: WhisperSyncConfig,
        on_model_loading: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self._model: Any = None
        self._device: str = resolve_device(config.device)
        self._compute_type: str = select_compute_type(self._device, config.compute_type)
        # What WhisperModel actually loads: the local snapshot/dir path when
        # the model is already on disk (fully offline, no hub round-trips),
        # or the model NAME when a real download is needed. Resolved in
        # _ensure_model right before the first load.
        self._model_source: str = config.model
        # Fired once, right before the (potentially slow) model load actually
        # happens, with a message saying WHICH slow thing is going on —
        # loading from the local cache vs. a genuine first-run download from
        # Hugging Face. See PROJECT_ANALYSIS.md §Stage 7.5.
        self._on_model_loading = on_model_loading
        if config.use_cache and config.cache_max_age_days > 0:
            _prune_cache(config.resolved_cache_dir, config.cache_max_age_days)

    @property
    def device(self) -> str:
        return self._device

    @property
    def compute_type(self) -> str:
        return self._compute_type

    def _load(self, device: str, compute_type: str) -> Any:
        from faster_whisper import WhisperModel

        logger.info(
            "Loading whisper model=%s (source=%s) device=%s compute_type=%s",
            self.config.model,
            self._model_source,
            device,
            compute_type,
        )
        return WhisperModel(self._model_source, device=device, compute_type=compute_type)

    def _cleanup_cuda(self) -> None:
        gc.collect()
        if self._device == "cuda" and torch is not None:
            with contextlib.suppress(AttributeError, RuntimeError):
                torch.cuda.empty_cache()

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        # Check the disk FIRST, then say which slow thing is about to happen:
        # a cached model loads via its local path (no network, no misleading
        # "Fetching N files" bars); a missing one honestly announces the
        # one-time download before it starts.
        local_path = _local_model_path(self.config.model)
        if local_path is not None:
            self._model_source = local_path
        if self._on_model_loading is not None:
            if local_path is not None:
                self._on_model_loading(
                    f"Whisper model '{self.config.model}' found on disk — " "loading into memory..."
                )
            else:
                self._on_model_loading(
                    f"Whisper model '{self.config.model}' not found locally — "
                    "downloading from Hugging Face (one-time; large models are "
                    "several GB, this can take a while)..."
                )
        try:
            self._model = self._load(self._device, self._compute_type)
            return
        except RuntimeError as exc:
            if not (self._device == "cuda" and _is_cuda_oom(exc)):
                raise
            logger.warning("CUDA OOM at model init; trying smaller compute types")
            self._cleanup_cuda()

        for ct in ("float16", "int8_float16", "int8"):
            try:
                self._model = self._load("cuda", ct)
                self._compute_type = ct
                return
            except Exception:
                self._cleanup_cuda()
        logger.warning("CUDA OOM persists; switching to CPU")
        self._device, self._compute_type = "cpu", "float32"
        self._model = self._load(self._device, self._compute_type)

    def _decode_kwargs(self) -> dict[str, Any]:
        cfg = self.config
        kwargs: dict[str, Any] = {
            "language": cfg.language,
            "word_timestamps": True,
            "vad_filter": cfg.vad_filter,
            "vad_parameters": {"min_silence_duration_ms": 500, "speech_pad_ms": 400},
            "best_of": max(1, cfg.best_of),
            "patience": max(1.0, cfg.patience),
            "temperature": list(WHISPER_TEMPERATURE_LADDER),
            "compression_ratio_threshold": 2.4,
            "log_prob_threshold": -1.0,
            "no_speech_threshold": 0.6,
            "repetition_penalty": max(1.0, cfg.repetition_penalty),
            "no_repeat_ngram_size": max(0, cfg.no_repeat_ngram_size),
        }
        if cfg.initial_prompt:
            kwargs["initial_prompt"] = cfg.initial_prompt
        return kwargs

    def _run(self, audio_path: Path, batch_size: int) -> Any:
        """Start a transcription run (returns a lazy segment generator + info)."""
        kwargs = self._decode_kwargs()
        if self.config.transcribe_mode.strip().lower() == "quality":
            kwargs["beam_size"] = max(1, self.config.quality_beam_size)
            kwargs["condition_on_previous_text"] = True
            return self._model.transcribe(str(audio_path), **kwargs)

        from faster_whisper import BatchedInferencePipeline

        kwargs["beam_size"] = self.config.beam_size
        kwargs["condition_on_previous_text"] = self.config.condition_on_previous_text
        batched = BatchedInferencePipeline(model=self._model)
        try:
            return batched.transcribe(str(audio_path), batch_size=max(1, batch_size), **kwargs)
        except TypeError:
            # Older faster-whisper without the batched API: degrade gracefully.
            return self._model.transcribe(
                str(audio_path),
                beam_size=self.config.beam_size,
                **self._decode_kwargs(),
            )

    def _materialize(
        self, audio_path: Path, batch_size: int, progress_callback: Callable[[float], None] | None
    ) -> tuple[list[Segment], str, float]:
        segments_gen, info = self._run(audio_path, batch_size)
        total = float(getattr(info, "duration", 0.0) or 0.0)
        result: list[Segment] = []
        for seg in segments_gen:
            words = [
                Word(
                    text=w.word.strip(),
                    start=w.start,
                    end=w.end,
                    probability=w.probability,
                )
                for w in (seg.words or [])
            ]
            result.append(Segment(start=seg.start, end=seg.end, words=words))
            if progress_callback and total > 0:
                progress_callback(min(seg.end / total, 1.0))
        language = str(getattr(info, "language", self.config.language or "") or "")
        return result, language, total

    def transcribe(
        self,
        audio_path: Path,
        progress_callback: Callable[[float], None] | None = None,
        identity: Path | None = None,
        stream_index: int | None = None,
    ) -> Transcript:
        """Transcribe ``audio_path``, using (and filling) the on-disk cache.

        ``identity`` is the file the transcript really BELONGS to, when
        ``audio_path`` is a throw-away decode of it. Camera clips are always
        transcribed from a scratch WAV extracted into a fresh tempfile, and the
        cache key was built from that scratch file's path, size and mtime — all
        three different on every run. The key therefore never repeated: a
        second run over unchanged footage re-did the entire (expensive)
        transcription and left another single-use entry behind, so the cache
        grew without ever being read. Keying on the ORIGINAL clip plus the
        chosen ``stream_index`` makes a re-run with different render settings
        a cache hit, which is the whole point of having one.
        """
        # Cache lookup uses the RESOLVED device/compute_type (self._device /
        # self._compute_type, resolved in __init__ from config.device/
        # compute_type — cheap, no model load), not config.compute_type
        # verbatim, which is often the literal string "auto". Two runs on
        # different hardware that both had compute_type="auto" used to collide
        # on the same cache key despite producing different transcripts. This
        # lookup intentionally happens BEFORE _ensure_model() so a cache hit
        # still avoids loading the model at all. See PROJECT_ANALYSIS.md §2.7.
        key_source = identity or audio_path
        key = self._cache_key(
            key_source, self.config, self._device, self._compute_type, stream_index
        )
        cache_file = self._cache_path(self.config.resolved_cache_dir, key)
        if self.config.use_cache:
            cached = self._load_cache(cache_file)
            if cached is not None:
                logger.info("Loaded transcript from cache for %s", key_source)
                return cached

        self._ensure_model()

        # OOM degradation ladder: GPU(batch) -> GPU(batch/2) -> ... -> CPU.
        cur_batch = (
            max(1, self.config.batch_size)
            if self.config.transcribe_mode.strip().lower() != "quality"
            else 1
        )
        logger.info("Transcribing %s (mode=%s)", audio_path, self.config.transcribe_mode)
        while True:
            try:
                segments, language, total = self._materialize(
                    audio_path, cur_batch, progress_callback
                )
                break
            except RuntimeError as exc:
                if not (self._device == "cuda" and _is_cuda_oom(exc)):
                    raise
                if cur_batch > 1:
                    self._cleanup_cuda()
                    cur_batch = max(1, cur_batch // 2)
                    logger.warning("CUDA OOM; retrying on GPU with batch_size=%d", cur_batch)
                    continue
                logger.warning("CUDA OOM at batch_size=1; switching to CPU")
                # Release the GPU model BEFORE building the CPU one. Loading
                # the replacement while `self._model` still referenced the old
                # one kept both alive simultaneously — on a machine that just
                # ran out of memory, which is the least affordable moment to
                # hold two copies. The traceback of the OOM we are handling can
                # also pin inference tensors, so it is dropped too.
                self._model = None
                exc.__traceback__ = None
                self._cleanup_cuda()
                self._device, self._compute_type = "cpu", "float32"
                self._model = self._load(self._device, self._compute_type)

        transcript = Transcript(
            source_path=key_source.resolve(),
            language=language,
            duration=total,
            segments=segments,
        )
        if self.config.use_cache:
            # Re-derive the key in case an in-flight OOM fallback changed
            # device/compute_type after the lookup above, so the saved cache
            # entry is keyed by what actually produced this transcript.
            final_key = self._cache_key(
                key_source, self.config, self._device, self._compute_type, stream_index
            )
            self._save_cache(
                self._cache_path(self.config.resolved_cache_dir, final_key), transcript
            )
        return transcript

    @staticmethod
    def _cache_key(
        audio_path: Path,
        config: WhisperSyncConfig,
        device: str,
        compute_type: str,
        stream_index: int | None = None,
    ) -> str:
        stat = audio_path.stat()
        parts = "|".join(
            [
                CACHE_SCHEMA,
                str(audio_path.resolve()),
                str(stat.st_size),
                str(stat.st_mtime),
                # Which audio stream was decoded: the same container read on a
                # different track is different audio and must not share a key.
                str(stream_index),
                config.model,
                device,
                compute_type,
                config.language or "",
                str(config.vad_filter),
                # decoding params that change the transcript
                config.transcribe_mode,
                str(config.beam_size),
                str(config.quality_beam_size),
                str(config.best_of),
                str(config.patience),
                str(config.condition_on_previous_text),
                str(config.repetition_penalty),
                str(config.no_repeat_ngram_size),
                config.initial_prompt,
            ]
        )
        return hashlib.sha256(parts.encode()).hexdigest()

    @staticmethod
    def _cache_path(cache_dir: Path, key: str) -> Path:
        return transcripts_cache_dir(cache_dir) / f"{key}.json"

    def _load_cache(self, cache_file: Path) -> Transcript | None:
        """A cached transcript, or None to re-transcribe.

        EVERY failure here is a cache miss, never an exception: a cache exists
        to save work, so a corrupt or unreadable entry must cost one
        re-transcription, not the run. Invalid UTF-8 in an entry used to raise
        ``UnicodeDecodeError`` straight out of the pipeline, and an
        unreadable-but-present file did the same — the cache could destroy the
        very work it was meant to preserve. Contents are validated too: a
        structurally valid JSON file with nonsense timestamps would otherwise
        be handed to the matcher as fact.
        """
        try:
            raw = cache_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("Unreadable cache file %s (%s) — will re-transcribe", cache_file, e)
            return None
        try:
            data = json.loads(raw)
            if data.get("schema") != CACHE_SCHEMA:
                logger.info("Cache file %s has a different schema — re-transcribing", cache_file)
                return None
            duration = _finite(data["duration"])
            segments = []
            for seg in data["segments"]:
                words = [
                    Word(
                        text=str(w["text"]),
                        start=_finite(w["start"]),
                        end=_finite(w["end"]),
                        probability=_finite(w["probability"]),
                    )
                    for w in seg["words"]
                ]
                if any(w.end < w.start for w in words):
                    raise ValueError("word end before start")
                segments.append(
                    Segment(start=_finite(seg["start"]), end=_finite(seg["end"]), words=words)
                )
            return Transcript(
                source_path=Path(data["source_path"]),
                language=str(data["language"]),
                duration=duration,
                segments=segments,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning("Corrupt cache file %s (%s) — will re-transcribe", cache_file, e)
            return None

    def _save_cache(self, cache_file: Path, transcript: Transcript) -> None:
        """Write a transcript to the cache. A failure is a warning, never a
        raise: the transcript is already computed and the caller needs it —
        losing an hour of inference because the cache directory is full or
        read-only would be the worst possible trade.

        The write is atomic (unique temp file in the same directory, then
        ``os.replace``), so a crash or a concurrent run can never leave a
        half-written entry that a later run would read back as a valid one.
        """
        data = {
            "schema": CACHE_SCHEMA,
            "source_path": str(transcript.source_path),
            "language": transcript.language,
            "duration": transcript.duration,
            "segments": [
                {
                    "start": seg.start,
                    "end": seg.end,
                    "words": [
                        {
                            "text": w.text,
                            "start": w.start,
                            "end": w.end,
                            "probability": w.probability,
                        }
                        for w in seg.words
                    ],
                }
                for seg in transcript.segments
            ],
        }
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=cache_file.parent, prefix=f".{cache_file.stem}.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
                os.replace(tmp_name, cache_file)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raise
        except OSError as e:
            logger.warning("Could not cache transcript to %s (%s)", cache_file, e)
            return
        logger.info("Transcript cached to %s", cache_file)

    def unload(self) -> None:
        # getattr, not attribute access: __del__ can run on an instance whose
        # __init__ raised part-way (a bad cache dir, an unresolvable device),
        # and an AttributeError raised from a destructor becomes an unraisable
        # exception printed to stderr — noise that hides the real failure.
        if getattr(self, "_model", None) is not None:
            del self._model
            self._model = None
            gc.collect()
            if self._device == "cuda" and torch is not None:
                with contextlib.suppress(AttributeError, RuntimeError):
                    torch.cuda.empty_cache()
            logger.info("Whisper model unloaded, VRAM freed")

    def __del__(self) -> None:
        self.unload()
