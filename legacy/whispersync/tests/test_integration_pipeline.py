"""End-to-end orchestration with real ffmpeg and a stubbed transcriber.

Every other test here covers one function. This one covers the thing the user
actually runs: scan -> transcribe -> align -> gate -> plan -> render -> export,
with real media on disk. It is where the audit's *output* criteria live — that
no source's artifact overwrites another's, that a second run cannot damage the
first's results, and that the exported document describes the plan.

Whisper itself is stubbed (a model load is not what is being tested); the word
lists it returns encode a known ground-truth offset, so the alignment,
rendering and export all have a right answer to be checked against.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from whispersync.config import WhisperSyncConfig
from whispersync.engine import pipeline as pipeline_mod
from whispersync.engine.export import check_fcpxml, fcpxml_intervals
from whispersync.engine.pipeline import run_pipeline
from whispersync.models import Segment, Transcript, Word

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"),
]

# The camera clip covers recorder seconds [REC_OFFSET, REC_OFFSET + CLIP_S].
CLIP_S = 6.0
REC_S = 20.0
REC_OFFSET = 4.0
# One "word" every WORD_DT seconds, so both transcripts have plenty of anchors
# spread across the whole clip (the gate requires coverage, not just count).
WORD_DT = 0.25
VOCAB = [f"w{i:03d}" for i in range(int(CLIP_S / WORD_DT))]


def _make_video(path: Path, duration: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x240:rate=25:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration}:sample_rate=48000",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )


def _make_wav(path: Path, duration: float, freq: int = 330) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={duration}:sample_rate=48000",
            "-ac",
            "1",
            "-acodec",
            "pcm_s24le",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def _transcript(path: Path, base: float) -> Transcript:
    """Words at `base + i*WORD_DT`, same vocabulary on both sides.

    Camera words start at 0, recorder words at REC_OFFSET, so the true map is
    t_cam = -REC_OFFSET + 1.0 * t_rec.
    """
    words = [
        Word(text=tok, start=base + i * WORD_DT, end=base + i * WORD_DT + 0.15, probability=0.95)
        for i, tok in enumerate(VOCAB)
    ]
    return Transcript(
        source_path=path,
        language="ru",
        duration=REC_S,
        segments=[Segment(start=words[0].start, end=words[-1].end, words=words)],
    )


class _StubEngine:
    """Stands in for WhisperEngine: returns the ground-truth word lists."""

    device = "cpu"
    compute_type = "float32"

    def __init__(self, config, on_model_loading=None) -> None:
        self.config = config

    def transcribe(self, audio_path, progress_callback=None, identity=None, stream_index=None):
        # Recorder files keep their .wav extension and live under "rec/";
        # camera audio arrives as an extracted scratch WAV, identified by the
        # `identity` the pipeline passes for exactly this reason.
        source = Path(identity) if identity is not None else Path(audio_path)
        is_recorder = source.suffix.lower() == ".wav" and "rec" in source.parts
        return _transcript(source, REC_OFFSET if is_recorder else 0.0)

    def unload(self) -> None:
        pass


def _dominant_hz(path: Path) -> float:
    """The strongest frequency in a WAV — enough to tell two test tones apart."""
    import numpy as np

    from whispersync.engine.acoustic import load_mono16k_track

    track = load_mono16k_track(path)
    # Skip the very start, where a lead-silence/fade can dominate.
    segment = track[16000 : 16000 * 4]
    if segment.size < 1024:
        segment = track
    spectrum = np.abs(np.fft.rfft(segment.astype(np.float64)))
    freqs = np.fft.rfftfreq(segment.size, d=1.0 / 16000)
    return float(freqs[int(np.argmax(spectrum))])


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> dict:
    monkeypatch.setattr(pipeline_mod, "WhisperEngine", _StubEngine)
    video_dir = tmp_path / "video"
    _make_video(video_dir / "clip1.mp4", CLIP_S)
    rec = tmp_path / "rec" / "take.wav"
    _make_wav(rec, REC_S)
    out = tmp_path / "out" / "sync.fcpxml"
    return {"video_dir": video_dir, "recorders": [rec], "output": out, "root": tmp_path}


def _config(**overrides) -> WhisperSyncConfig:
    base = {
        "use_cache": False,
        "save_transcripts": True,
        "boundary_flex": False,
        "ambience_track": False,
        "voice_enhance": "off",
        "self_check_mode": "off",
        "acoustic_fallback": False,
        "render_workers": 1,
        # The synthetic clip is short; keep the coverage bar meaningful but
        # reachable for a 6-second clip.
        "min_anchors": 5,
    }
    base.update(overrides)
    return WhisperSyncConfig(**base).validate()


def test_pipeline_produces_a_valid_project(project: dict) -> None:
    result = run_pipeline(
        config=_config(),
        video_dir=project["video_dir"],
        audio_files=project["recorders"],
        strategy_id=3,
        output_path=project["output"],
    )

    assert result.fcpxml_path.exists()
    assert not check_fcpxml(result.fcpxml_path, check_media=True), "exported document is unusable"

    # The alignment recovered the ground truth.
    assert abs(result.alignment.offset - (-REC_OFFSET)) < 0.3
    assert abs(result.alignment.k - 1.0) < 0.01

    # A voice WAV was rendered, is real audio, and matches the clip's length.
    voice = list((project["output"].parent / "audio_synced").glob("*.wav"))
    assert len(voice) == 1, voice
    from whispersync.engine.media import probe

    assert abs(probe(voice[0]).duration - CLIP_S) < 0.05


def test_run_scratch_does_not_survive_the_run(project: dict) -> None:
    """The run's private scratch is its own to clean up — and it must."""
    run_pipeline(
        config=_config(),
        video_dir=project["video_dir"],
        audio_files=project["recorders"],
        strategy_id=3,
        output_path=project["output"],
    )
    leftovers = list(project["output"].parent.glob(".whispersync-run-*"))
    assert leftovers == [], f"scratch left behind: {leftovers}"
    assert not (project["output"].parent / ".whispersync-run.lock").exists()


def test_recorders_with_the_same_filename_do_not_share_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    """`/A/take.wav` and `/B/take.wav` both produced `.master/take_master.wav`:
    the second extraction overwrote the first, so BOTH recorder ids pointed at
    one file and one recorder's audio was rendered as the other's."""
    monkeypatch.setattr(pipeline_mod, "WhisperEngine", _StubEngine)
    video_dir = tmp_path / "video"
    _make_video(video_dir / "clip1.mp4", CLIP_S)
    a = tmp_path / "rec" / "A" / "take.wav"
    b = tmp_path / "rec" / "B" / "take.wav"
    _make_wav(a, REC_S, freq=330)
    _make_wav(b, REC_S, freq=550)
    out = tmp_path / "out" / "sync.fcpxml"

    result = run_pipeline(
        config=_config(recorder_mode="all"),
        video_dir=video_dir,
        audio_files=[a, b],
        strategy_id=3,
        output_path=out,
    )

    voices = sorted((out.parent / "audio_synced").glob("*.wav"))
    assert len(voices) == 2, [v.name for v in voices]
    assert voices[0].name != voices[1].name

    # Distinct names are not the point — distinct AUDIO is. The two recorders
    # hold different tones, so if one master overwrote the other both voice
    # tracks would carry the same tone, which is the actual damage: one
    # recorder's audio delivered as another's.
    tones = sorted(_dominant_hz(v) for v in voices)
    assert abs(tones[0] - 330) < 15 and abs(tones[1] - 550) < 15, (
        f"voice tracks carry {tones} Hz — expected one 330 Hz and one 550 Hz; "
        "the two recorders' audio was not kept separate"
    )

    # Both are referenced by the document, at distinct paths.
    names = {c.path.name for c in result.plan.clips if c.kind == "audio"}
    assert len(names) == 2

    # Transcripts, too: one stem must not overwrite the other's JSON.
    transcripts = sorted((out.parent / "transcripts").glob("*.json"))
    assert len(transcripts) == 3, [t.name for t in transcripts]  # 2 recorders + 1 camera


def test_a_failed_rerun_leaves_the_previous_project_intact(project: dict, monkeypatch) -> None:
    """A run that dies mid-render must not degrade the result already on disk.

    Runs shared `audio_synced/` under ffmpeg's `-y` and wrote each voice track
    straight to its final name, so a failure part-way through replaced a
    previous run's good render with a truncated file — and the old project's
    FCPXML went on pointing at it. Every published artifact is now written to a
    temporary beside its destination and moved into place only when complete.
    """
    out = project["output"]
    run_pipeline(
        config=_config(),
        video_dir=project["video_dir"],
        audio_files=project["recorders"],
        strategy_id=3,
        output_path=out,
    )
    before = {p: p.read_bytes() for p in sorted(out.parent.rglob("*")) if p.is_file()}
    assert before, "first run produced nothing to protect"

    # Second run to the SAME output, failing during assembly — AFTER it has
    # begun writing. That ordering is the whole point: ffmpeg truncates its
    # output file as it opens it, so a crash part-way leaves a fragment at
    # whatever path it was given. A mock that raises before writing anything
    # would pass whether or not the write is atomic.
    def boom(seg_paths, lead, duration, sample_rate, output_path, **kwargs):
        Path(output_path).write_bytes(b"RIFF-partial-garbage")
        raise RuntimeError("ffmpeg died mid-assembly")

    monkeypatch.setattr(pipeline_mod, "assemble_continuous", boom)
    with pytest.raises(RuntimeError, match="mid-assembly"):
        run_pipeline(
            config=_config(),
            video_dir=project["video_dir"],
            audio_files=project["recorders"],
            strategy_id=3,
            output_path=out,
        )

    after = {p: p.read_bytes() for p in sorted(out.parent.rglob("*")) if p.is_file()}
    for path, content in before.items():
        assert path in after, f"the failed run deleted {path}"
        assert after[path] == content, f"the failed run rewrote {path}"
    # And it left no partial files behind either.
    stray = [p.name for p in after if p not in before]
    assert stray == [], f"failed run left {stray}"


def test_exported_intervals_match_the_plan(project: dict) -> None:
    """Parsing successfully says nothing about whether the clips landed where
    the plan put them."""
    result = run_pipeline(
        config=_config(),
        video_dir=project["video_dir"],
        audio_files=project["recorders"],
        strategy_id=3,
        output_path=project["output"],
    )
    intervals = fcpxml_intervals(result.fcpxml_path)
    for clip in result.plan.clips:
        name = clip.display_name or clip.path.stem
        assert name in intervals, f"{name} missing from the exported document"
        start, end = intervals[name]
        assert abs(start - clip.offset) < 0.1, f"{name}: {start} vs planned {clip.offset}"
        assert abs((end - start) - clip.duration) < 0.1


def test_camera_audio_is_muted_where_dialogue_was_replaced(project: dict) -> None:
    """Without `srcEnable="video"` the camera's own microphone plays under the
    clean synced voice — two copies of the same speech, comb-filtering."""
    import xml.etree.ElementTree as ET

    result = run_pipeline(
        config=_config(),
        video_dir=project["video_dir"],
        audio_files=project["recorders"],
        strategy_id=3,
        output_path=project["output"],
    )
    root = ET.parse(result.fcpxml_path).getroot()
    video_el = root.find(".//spine/asset-clip")
    assert video_el is not None
    assert video_el.get("srcEnable") == "video"


def test_negative_calibration_is_delivered_not_clamped(project: dict) -> None:
    """A negative lip-sync calibration asks the voice to run AHEAD of the
    picture. Both serializers used to clamp the resulting negative offset to
    zero, producing exactly the uncalibrated result while reporting that the
    calibration had been applied."""
    result = run_pipeline(
        config=_config(camera_av_offset_ms=-200.0),
        video_dir=project["video_dir"],
        audio_files=project["recorders"],
        strategy_id=3,
        output_path=project["output"],
    )
    video = next(c for c in result.plan.clips if c.kind == "video")
    audio = next(c for c in result.plan.clips if c.kind == "audio")
    # The voice leads the picture by the requested 200 ms.
    assert abs((audio.offset - video.offset) - (-0.2)) < 0.01
    assert min(c.offset for c in result.plan.clips) >= 0.0


def test_a_concurrent_run_on_the_same_output_is_refused(project: dict) -> None:
    from whispersync.engine.workspace import OutputLockedError, output_lock

    project["output"].parent.mkdir(parents=True, exist_ok=True)
    with output_lock(project["output"].parent), pytest.raises(OutputLockedError):
        run_pipeline(
            config=_config(),
            video_dir=project["video_dir"],
            audio_files=project["recorders"],
            strategy_id=3,
            output_path=project["output"],
        )


def test_voice_segments_carry_their_own_source_range(project: dict) -> None:
    """A segment must be verified against ITS OWN minutes of the source.

    Voice clips were matched back to their video by name, so a segment covering
    minutes 5-10 was compared against the opening seconds of the video and the
    resulting five-minute content difference was reported as lip-sync lag.
    Each segment now carries an explicit (source, start, duration) link.
    """
    # ~2-second segments out of a 6-second clip.
    result = run_pipeline(
        config=_config(voice_segment_minutes=0),
        video_dir=project["video_dir"],
        audio_files=project["recorders"],
        strategy_id=3,
        output_path=project["output"],
    )
    whole = [c for c in result.plan.clips if c.kind == "audio" and c.role == "Dialogue"]
    assert len(whole) == 1
    src_path, src_start, src_dur = whole[0].source_ref
    assert src_path == project["video_dir"] / "clip1.mp4"
    assert src_start == 0.0
    assert abs(src_dur - CLIP_S) < 0.05


def test_every_dialogue_clip_has_a_source_link(project: dict) -> None:
    """Verification skips clips with no link, so a missing one is a silent gap
    in coverage rather than a visible failure."""
    result = run_pipeline(
        config=_config(),
        video_dir=project["video_dir"],
        audio_files=project["recorders"],
        strategy_id=3,
        output_path=project["output"],
    )
    dialogue = [c for c in result.plan.clips if c.kind == "audio" and c.role == "Dialogue"]
    assert dialogue
    for clip in dialogue:
        assert clip.source_ref is not None, f"{clip.display_name} has no source link"
        path, start, duration = clip.source_ref
        assert path.exists()
        assert start >= 0.0 and duration > 0.0
