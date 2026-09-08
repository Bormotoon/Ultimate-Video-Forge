"""Tests for the transcript cache key (pure logic, no GPU/model load)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from whispersync.config import WhisperSyncConfig
from whispersync.engine.transcriber import WhisperEngine


def test_cache_key_differs_by_resolved_device(tmp_path: Path) -> None:
    audio = tmp_path / "rec.wav"
    audio.write_bytes(b"fake audio bytes")
    cfg = WhisperSyncConfig(compute_type="auto")

    # Two runs with the literal config compute_type="auto" but different
    # RESOLVED devices (e.g. one machine has a GPU, one doesn't, or a run fell
    # back CUDA->CPU mid-transcription) must not collide on the same cache key
    # despite producing different transcripts. See PROJECT_ANALYSIS.md §2.7.
    key_cuda = WhisperEngine._cache_key(audio, cfg, "cuda", "float16")
    key_cpu = WhisperEngine._cache_key(audio, cfg, "cpu", "float32")
    assert key_cuda != key_cpu


def test_cache_key_stable_for_same_resolved_device(tmp_path: Path) -> None:
    audio = tmp_path / "rec.wav"
    audio.write_bytes(b"fake audio bytes")
    cfg = WhisperSyncConfig(compute_type="auto")

    key1 = WhisperEngine._cache_key(audio, cfg, "cuda", "float16")
    key2 = WhisperEngine._cache_key(audio, cfg, "cuda", "float16")
    assert key1 == key2


def test_cache_key_changes_with_decoding_params(tmp_path: Path) -> None:
    audio = tmp_path / "rec.wav"
    audio.write_bytes(b"fake audio bytes")
    cfg_a = WhisperSyncConfig(beam_size=5)
    cfg_b = WhisperSyncConfig(beam_size=10)

    key_a = WhisperEngine._cache_key(audio, cfg_a, "cpu", "float32")
    key_b = WhisperEngine._cache_key(audio, cfg_b, "cpu", "float32")
    assert key_a != key_b


def test_on_model_loading_fires_once_before_first_load() -> None:
    calls: list[str] = []
    cfg = WhisperSyncConfig()
    engine = WhisperEngine(cfg, on_model_loading=calls.append)

    with (
        patch.object(WhisperEngine, "_load", return_value=object()),
        patch("whispersync.engine.transcriber._local_model_path", return_value=None),
    ):
        engine._ensure_model()
        engine._ensure_model()  # already loaded -> must not fire again

    assert len(calls) == 1


def test_on_model_loading_reports_cached_model_and_uses_local_path() -> None:
    calls: list[str] = []
    cfg = WhisperSyncConfig()
    engine = WhisperEngine(cfg, on_model_loading=calls.append)

    with (
        patch.object(WhisperEngine, "_load", return_value=object()),
        patch(
            "whispersync.engine.transcriber._local_model_path",
            return_value="/fake/hub/snapshots/abc",
        ),
    ):
        engine._ensure_model()

    assert len(calls) == 1
    assert "found on disk" in calls[0]
    assert "download" not in calls[0].lower()
    # The actual load must go through the LOCAL path (fully offline), not the
    # model name (which would re-check the hub online on every start).
    assert engine._model_source == "/fake/hub/snapshots/abc"


def test_on_model_loading_reports_download_when_not_cached() -> None:
    calls: list[str] = []
    cfg = WhisperSyncConfig()
    engine = WhisperEngine(cfg, on_model_loading=calls.append)

    with (
        patch.object(WhisperEngine, "_load", return_value=object()),
        patch("whispersync.engine.transcriber._local_model_path", return_value=None),
    ):
        engine._ensure_model()

    assert len(calls) == 1
    assert "downloading" in calls[0].lower()
    assert engine._model_source == cfg.model  # name -> faster-whisper downloads


def test_on_model_loading_not_required() -> None:
    cfg = WhisperSyncConfig()
    engine = WhisperEngine(cfg)  # no callback passed
    with (
        patch.object(WhisperEngine, "_load", return_value=object()),
        patch("whispersync.engine.transcriber._local_model_path", return_value=None),
    ):
        engine._ensure_model()  # must not raise


def test_local_model_path_accepts_ct2_directory(tmp_path: Path) -> None:
    from whispersync.engine.transcriber import _local_model_path

    model_dir = tmp_path / "my-ct2-model"
    model_dir.mkdir()
    assert _local_model_path(str(model_dir)) == str(model_dir)


def test_local_model_path_none_for_unknown_model() -> None:
    from whispersync.engine.transcriber import _local_model_path

    # A model that certainly isn't in the local HF cache -> needs a download.
    assert _local_model_path("definitely-not-a-real-model-xyz") is None


def _write_entry(dir_: Path, key: str, age_days: float = 0.0) -> Path:
    """A file shaped exactly like a real cache entry: 64-hex name, schema marker."""
    import os
    import time

    from whispersync.engine.transcriber import CACHE_SCHEMA

    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / f"{key}.json"
    path.write_text(f'{{"schema": "{CACHE_SCHEMA}", "segments": []}}')
    if age_days:
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
    return path


def test_prune_cache_removes_only_stale_entries(tmp_path: Path) -> None:
    from whispersync.engine.transcriber import _prune_cache, transcripts_cache_dir

    own = transcripts_cache_dir(tmp_path)
    old = _write_entry(own, "a" * 64, age_days=10)
    fresh = _write_entry(own, "b" * 64)
    other = own / "not_cache.txt"  # non-.json files are never touched
    other.write_text("{}")

    removed = _prune_cache(tmp_path, max_age_days=7)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
    assert other.exists()


def test_prune_cache_never_deletes_files_it_did_not_write(tmp_path: Path) -> None:
    """Retention must not treat a user-chosen cache directory as its own.

    With ``cache_dir`` pointing at a shared folder and ``cache_max_age_days``
    set, pruning deleted every old ``*.json`` in it — a mock filesystem
    confirmed ``/shared/report.json`` being removed. Two things now protect
    other people's files: a subdirectory of our own, and a per-file check of
    the name shape and schema marker before any unlink.
    """
    import os
    import time

    from whispersync.engine.transcriber import _prune_cache, transcripts_cache_dir

    stale = time.time() - 30 * 86400

    # Somebody else's data, in the cache root the user pointed us at.
    outsider = tmp_path / "report.json"
    outsider.write_text('{"quarterly": true}')
    os.utime(outsider, (stale, stale))

    own = transcripts_cache_dir(tmp_path)
    # And a stray .json inside OUR directory that we did not write.
    intruder = own / "notes.json"
    own.mkdir(parents=True, exist_ok=True)
    intruder.write_text("{}")
    os.utime(intruder, (stale, stale))

    ours = _write_entry(own, "c" * 64, age_days=30)

    removed = _prune_cache(tmp_path, max_age_days=7)
    assert removed == 1
    assert not ours.exists()
    assert outsider.exists(), "pruning deleted a file outside its own directory"
    assert intruder.exists(), "pruning deleted an unrecognised file"


def test_prune_cache_missing_dir_is_noop(tmp_path: Path) -> None:
    from whispersync.engine.transcriber import _prune_cache

    assert _prune_cache(tmp_path / "does_not_exist", max_age_days=7) == 0


def test_engine_init_prunes_when_configured(tmp_path: Path) -> None:
    from whispersync.engine.transcriber import transcripts_cache_dir

    old = _write_entry(transcripts_cache_dir(tmp_path), "d" * 64, age_days=30)
    cfg = WhisperSyncConfig(cache_dir=str(tmp_path), cache_max_age_days=7)
    WhisperEngine(cfg)
    assert not old.exists()


def test_engine_init_keeps_cache_forever_by_default(tmp_path: Path) -> None:
    from whispersync.engine.transcriber import transcripts_cache_dir

    old = _write_entry(transcripts_cache_dir(tmp_path), "e" * 64, age_days=365)
    cfg = WhisperSyncConfig(cache_dir=str(tmp_path))  # cache_max_age_days=0
    WhisperEngine(cfg)
    assert old.exists()


# --- cache robustness: a bad entry costs one re-transcription, not the run ---


def test_invalid_utf8_cache_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    """A cache exists to save work; it must never destroy it.

    Invalid UTF-8 in an entry used to raise ``UnicodeDecodeError`` straight out
    of the pipeline, so a single corrupt byte in the cache aborted the run.
    """
    from whispersync.engine.transcriber import WhisperEngine

    cfg = WhisperSyncConfig(cache_dir=str(tmp_path))
    engine = WhisperEngine.__new__(WhisperEngine)
    engine.config = cfg
    bad = tmp_path / "bad.json"
    bad.write_bytes(b'{"schema": "x", "seg\xffments": []}')
    assert engine._load_cache(bad) is None


def test_cache_entry_with_nonfinite_times_is_rejected(tmp_path: Path) -> None:
    """JSON round-trips NaN and Infinity. A transcript carrying either would
    poison every downstream time calculation instead of failing visibly."""
    from whispersync.engine.transcriber import CACHE_SCHEMA, WhisperEngine

    cfg = WhisperSyncConfig(cache_dir=str(tmp_path))
    engine = WhisperEngine.__new__(WhisperEngine)
    engine.config = cfg
    entry = tmp_path / "nan.json"
    entry.write_text(
        json.dumps(
            {
                "schema": CACHE_SCHEMA,
                "source_path": "/x.wav",
                "language": "ru",
                "duration": 10.0,
                "segments": [
                    {
                        "start": 0.0,
                        "end": float("inf"),
                        "words": [
                            {"text": "a", "start": 0.0, "end": float("nan"), "probability": 1.0}
                        ],
                    }
                ],
            }
        )
    )
    assert engine._load_cache(entry) is None


def test_unwritable_cache_does_not_lose_the_transcript(tmp_path: Path, caplog) -> None:
    """The transcript is already computed and the caller needs it: a failed
    cache write is a warning, not an exception."""
    from whispersync.engine.transcriber import WhisperEngine
    from whispersync.models import Transcript

    cfg = WhisperSyncConfig(cache_dir=str(tmp_path))
    engine = WhisperEngine.__new__(WhisperEngine)
    engine.config = cfg
    transcript = Transcript(source_path=Path("/x.wav"), language="ru", duration=1.0, segments=[])
    # A path whose parent cannot be created (a file stands where a dir must go).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    engine._save_cache(blocker / "sub" / "k.json", transcript)  # must not raise


def test_cache_write_is_atomic(tmp_path: Path) -> None:
    """No half-written entry may ever be visible under the final name: a later
    run would read it back as a valid cached transcript."""
    from whispersync.engine.transcriber import WhisperEngine
    from whispersync.models import Transcript

    cfg = WhisperSyncConfig(cache_dir=str(tmp_path))
    engine = WhisperEngine.__new__(WhisperEngine)
    engine.config = cfg
    target = tmp_path / "entry.json"
    transcript = Transcript(source_path=Path("/x.wav"), language="ru", duration=1.0, segments=[])
    engine._save_cache(target, transcript)
    assert json.loads(target.read_text())["duration"] == 1.0
    # Nothing left behind but the entry itself.
    assert [p.name for p in tmp_path.iterdir() if p.is_file()] == ["entry.json"]


def test_cache_key_follows_the_original_not_the_scratch_decode(tmp_path: Path) -> None:
    """Camera clips are transcribed from a fresh tempfile every run.

    Keying on that scratch file meant a new key each time — the expensive
    transcription was always redone and every entry was written once and never
    read. The key must follow the identity the caller passes in.
    """
    from whispersync.engine.transcriber import WhisperEngine

    original = tmp_path / "DJI_0830.MOV"
    original.write_bytes(b"x" * 100)
    cfg = WhisperSyncConfig()
    k1 = WhisperEngine._cache_key(original, cfg, "cuda", "float16", 0)
    k2 = WhisperEngine._cache_key(original, cfg, "cuda", "float16", 0)
    assert k1 == k2
    # A different audio stream of the same container is different audio.
    assert WhisperEngine._cache_key(original, cfg, "cuda", "float16", 1) != k1
