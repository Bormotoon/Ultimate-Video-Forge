"""Media probing and audio extraction via ffmpeg/ffprobe."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


@dataclass
class MediaInfo:
    path: Path
    duration: float
    fps: Fraction | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    audio_channels: int | None
    audio_sample_rate: int | None
    # Sample format as reported by ffprobe (e.g. "s16", "s32", "fltp") and its bit
    # depth, when known. Used to pick a lossless PCM codec for rendered audio that
    # matches (or safely exceeds) the source, instead of hard-coding 16-bit.
    audio_sample_fmt: str | None = None
    audio_bits_per_sample: int | None = None
    # Index of the chosen audio stream AMONG THE AUDIO STREAMS of the container
    # (the N in ffmpeg's ``-map 0:a:N``), or None when the file has no audio.
    # ffprobe's "first audio stream" and ffmpeg's automatic stream selection are
    # NOT the same rule (ffmpeg prefers the stream with the most channels, and
    # honours dispositions), so a container holding a mono lav plus a silent
    # stereo scratch track would be probed on one stream and decoded from the
    # other. Everything downstream maps this index explicitly.
    audio_stream_index: int | None = None
    # True only when the container reports a fixed frame rate we can trust for
    # a frame-count duration; VFR material must use timestamps instead.
    is_cfr: bool = True
    # Length of the CONTAINER, as ffprobe reports it for the file as a whole.
    # ``duration`` above is the PICTURE length (frame-accurate), which is what
    # planning and timeline placement must use — but it is commonly a few
    # frames shorter than the file, because the audio track runs past the last
    # video frame. An NLE describes an asset by the file, so the FCPXML must
    # declare this value: declaring the picture length instead makes the
    # declared and the actual media disagree, and Final Cut then refuses to
    # relink the file ("the media differs from the original").
    container_duration: float | None = None


# Muxer options for every WAV this project writes, placed immediately before
# the output path (they are OUTPUT options; after "-y" ffmpeg parses them as
# input options and refuses the command).
#
# Plain RIFF/WAV stores sizes in 32-bit fields, so it cannot describe a file
# larger than 4 GiB — which stereo 48 kHz/24-bit reaches in about 4.14 hours,
# and a multichannel recorder reaches sooner. Past that limit ffmpeg emits a
# header that misreports its own length: readers see a truncated file or refuse
# it outright. "-rf64 auto" writes an ordinary RIFF header when the file fits
# and transparently promotes to RF64 when it does not, so long-form projects
# come out intact while short ones stay byte-identical. Applied everywhere,
# because "this output is always short" is precisely the assumption that fails
# on someone's four-hour lecture.
WAV_MUX_ARGS = ["-rf64", "auto"]


def _select_audio_stream(audio_streams: list[dict]) -> int | None:
    """Index (among audio streams) of the track this run will use, or None.

    ffprobe's caller reads "the first audio stream"; ffmpeg, given no ``-map``,
    picks the *best* one — most channels wins, then stream order — and honours
    a ``default`` disposition. A camera file holding a mono on-board mic plus a
    silent stereo timecode/scratch track therefore got probed on one stream and
    decoded from the other: real speech in the metadata, silence in the audio.

    This is the single place that decision is made. The policy is explicit:
    prefer a stream flagged ``default``, otherwise the first stream — and
    whatever comes out is recorded on ``MediaInfo.audio_stream_index`` and
    mapped explicitly by every decode, so probing and decoding can never
    disagree again.
    """
    if not audio_streams:
        return None
    for i, stream in enumerate(audio_streams):
        disposition = stream.get("disposition") or {}
        if disposition.get("default"):
            return i
    return 0


def probe(path: Path, timeout: float = 30.0) -> MediaInfo:
    """Read duration/fps/codecs/etc via ffprobe. ``timeout`` (seconds) is
    configurable — the default is generous for local files, but a clip on
    network/NAS storage can legitimately take longer to respond. See
    PROJECT_ANALYSIS.md §6.6.
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        # A file that's still being copied onto disk (e.g. an in-progress
        # transfer from an SD card/camera) has no finalized MOV/MP4 index yet,
        # so ffprobe fails here — often with an empty stderr, which is
        # otherwise indistinguishable from a genuinely corrupt file. Detect
        # growth over a short window and say so explicitly.
        size_before: int | None
        size_after: int | None
        try:
            size_before = path.stat().st_size
            time.sleep(0.5)
            size_after = path.stat().st_size
        except OSError:
            size_before = size_after = None
        if size_before is not None and size_after != size_before:
            raise RuntimeError(
                f"ffprobe failed for {path}: file size is still changing "
                f"({size_before} -> {size_after} bytes) — it looks like this "
                "file is still being copied/written. Wait for the copy to "
                "finish before running sync."
            )
        detail = result.stderr.strip() or f"exit code {result.returncode}, no stderr output"
        raise RuntimeError(f"ffprobe failed for {path}: {detail}")

    data = json.loads(result.stdout)

    video_stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio_streams = [s for s in data["streams"] if s["codec_type"] == "audio"]
    audio_index = _select_audio_stream(audio_streams)
    audio_stream = audio_streams[audio_index] if audio_index is not None else None

    # Some professional containers (MXF, certain MOV) omit format.duration;
    # fall back to the video, then audio, stream duration.
    duration_str = data.get("format", {}).get("duration")
    if duration_str is None and video_stream is not None:
        duration_str = video_stream.get("duration")
    if duration_str is None and audio_stream is not None:
        duration_str = audio_stream.get("duration")
    if duration_str is None:
        raise RuntimeError(f"Could not determine duration for {path}")
    duration = float(duration_str)
    container_duration = duration

    fps: Fraction | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    is_cfr = True

    if video_stream:
        r_frame_rate = video_stream.get("r_frame_rate", "")
        if "/" in r_frame_rate:
            num, den = r_frame_rate.split("/")
            fps = Fraction(int(num), int(den))
        elif r_frame_rate:
            fps = Fraction(r_frame_rate)
        width = int(video_stream.get("width", 0)) or None
        height = int(video_stream.get("height", 0)) or None
        video_codec = video_stream.get("codec_name")

        # Prefer a frame-accurate video duration: the container duration often runs
        # a few frames longer than the picture (longer audio track / muxing slack),
        # and that overshoot grows with length, so the FCPXML would claim more
        # frames than the file holds and Final Cut would refuse to relink.
        # r_frame_rate is only NOMINAL. When avg_frame_rate disagrees with it the
        # material is variable-frame-rate, and nb_frames / r_frame_rate then
        # reports a duration the file does not have (240 frames at a nominal 30
        # fps but an average 24 fps is 10 s of media, not 8). Trust the frame
        # count only for confirmed CFR; otherwise fall back to real timestamps.
        avg_fps: Fraction | None = None
        avg_rate = video_stream.get("avg_frame_rate", "")
        with contextlib.suppress(ValueError, ZeroDivisionError):
            if "/" in avg_rate:
                a_num, a_den = avg_rate.split("/")
                if int(a_den) != 0:
                    avg_fps = Fraction(int(a_num), int(a_den))
            elif avg_rate:
                avg_fps = Fraction(avg_rate)
        is_cfr = fps is not None and (avg_fps is None or abs(float(avg_fps) - float(fps)) <= 1e-3)

        nb_frames = video_stream.get("nb_frames")
        stream_dur = video_stream.get("duration")
        if nb_frames and str(nb_frames).isdigit() and int(nb_frames) > 0 and fps and is_cfr:
            duration = int(nb_frames) / float(fps)
        elif stream_dur:
            with contextlib.suppress(ValueError):
                duration = float(stream_dur)
        elif nb_frames and str(nb_frames).isdigit() and int(nb_frames) > 0 and avg_fps is not None:
            duration = int(nb_frames) / float(avg_fps)

    audio_codec: str | None = None
    audio_channels: int | None = None
    audio_sample_rate: int | None = None
    audio_sample_fmt: str | None = None
    audio_bits_per_sample: int | None = None

    if audio_stream:
        audio_codec = audio_stream.get("codec_name")
        audio_channels = int(audio_stream.get("channels", 0)) or None
        sr = audio_stream.get("sample_rate")
        audio_sample_rate = int(sr) if sr else None
        audio_sample_fmt = audio_stream.get("sample_fmt") or None
        # bits_per_raw_sample is the true source depth (e.g. 24-bit in a 32-bit
        # container); bits_per_sample is the container's storage width. Prefer the
        # raw value when ffprobe reports it.
        bps = audio_stream.get("bits_per_raw_sample") or audio_stream.get("bits_per_sample")
        audio_bits_per_sample = int(bps) if bps and str(bps).isdigit() and int(bps) > 0 else None

    return MediaInfo(
        path=path,
        duration=duration,
        fps=fps,
        width=width,
        height=height,
        video_codec=video_codec,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
        audio_sample_rate=audio_sample_rate,
        audio_sample_fmt=audio_sample_fmt,
        audio_bits_per_sample=audio_bits_per_sample,
        audio_stream_index=audio_index,
        is_cfr=is_cfr,
        container_duration=container_duration,
    )


def extract_audio_to_wav(
    input_path: Path,
    output_path: Path | None = None,
    sample_rate: int = 16000,
    mono: bool = True,
    start_s: float | None = None,
    duration_s: float | None = None,
    stream_index: int | None = 0,
) -> Path:
    """Decode a file's audio to PCM WAV; ``start_s``/``duration_s`` decode
    only that window (fast input seek) instead of the whole file — a
    multi-hour recorder shouldn't be fully decoded when the caller needs a
    few seconds of it for one local measurement.

    ``stream_index`` is the audio stream to read (the N in ``-map 0:a:N``),
    normally ``MediaInfo.audio_stream_index``. It is mapped EXPLICITLY: left to
    its own devices ffmpeg picks the stream with the most channels, which is
    not the stream ``probe`` reported on. Pass ``None`` only to accept
    ffmpeg's own choice.
    """
    if output_path is None:
        fd, tmp_name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        output_path = Path(tmp_name)

    cmd = ["ffmpeg", "-y"]
    if start_s is not None and start_s > 0:
        cmd.extend(["-ss", f"{start_s:.6f}"])
    if duration_s is not None:
        cmd.extend(["-t", f"{duration_s:.6f}"])
    cmd.extend(["-i", str(input_path), "-vn"])
    if stream_index is not None:
        cmd.extend(["-map", f"0:a:{stream_index}"])
    cmd.extend(["-acodec", "pcm_s16le", "-ar", str(sample_rate)])
    if mono:
        cmd.extend(["-ac", "1"])
    cmd.extend([*WAV_MUX_ARGS, str(output_path)])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr}")

    return output_path


def extract_audio_master(
    input_path: Path,
    output_path: Path,
    sample_rate: int,
    channels: int,
    codec: str,
    stream_index: int | None = 0,
) -> Path:
    """Transcode a recorder file to lossless PCM WAV, once, for the render path.

    Cutting pieces directly from a lossy source (mp3/m4a) with ``-ss`` before
    ``-i`` is not sample-accurate (seek lands on a frame boundary, ~26ms for
    mp3), and re-decoding a lossy file on every cut compounds artifacts. This
    produces a single PCM master at the render's target sample rate and native
    channel count so every downstream cut/concat operates on identical,
    sample-accurate, uncompressed audio. Uses the highest-quality resampler
    ffmpeg ships (soxr) when available, falling back to swr.
    """
    resamplers = ("soxr", "swr")
    last_stderr = ""
    for i, resampler in enumerate(resamplers):
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vn",
        ]
        if stream_index is not None:
            cmd.extend(["-map", f"0:a:{stream_index}"])
        cmd += [
            "-af",
            f"aresample=resampler={resampler}",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-acodec",
            codec,
            *WAV_MUX_ARGS,
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode == 0:
            return output_path
        last_stderr = result.stderr
        if i < len(resamplers) - 1:
            # This ffmpeg build may lack libsoxr (or some other resampler-specific
            # issue); retry with the next resampler rather than parsing stderr text,
            # which varies across ffmpeg versions.
            continue
    raise RuntimeError(f"ffmpeg master extraction failed: {last_stderr}")


def pcm_codec_for_bit_depth(bits_per_sample: int | None, sample_fmt: str | None = None) -> str:
    """Lossless PCM codec that matches (or safely covers) a source's format.

    Rendering everything through 16-bit PCM regardless of source depth throws
    away real resolution from 24-/32-bit recorders and adds a fresh quantization
    step at every intermediate render stage. ``None`` (unknown depth, or a lossy
    source codec ffprobe can't report PCM depth for) defaults to 24-bit, which
    covers the vast majority of professional recorders without truncation.

    ``sample_fmt`` (ffprobe's ``flt``/``fltp``/``dbl``/``dblp``/``s16``/…) is
    decisive when present. A 32-bit FLOAT recorder — the whole point of which
    is that samples above ±1.0 stay recoverable — must not be conformed to an
    INTEGER codec: ``pcm_s32le`` hard-clips at full scale, so a take peaking at
    +2.0 comes back clipped and no later gain reduction can undo it. Float
    sources therefore stay float end to end; converting to integer is a
    delivery decision, made explicitly, after peaks are known.
    """
    fmt = (sample_fmt or "").lower().rstrip("p")
    if fmt == "flt":
        return "pcm_f32le"
    if fmt == "dbl":
        return "pcm_f64le"
    if fmt.startswith(("s", "u")) and bits_per_sample is None:
        # An integer format whose depth ffprobe didn't spell out: fall through
        # to the depth heuristic below rather than guessing from the name.
        pass
    if bits_per_sample is not None and bits_per_sample <= 16:
        return "pcm_s16le"
    if bits_per_sample is not None and bits_per_sample >= 32:
        return "pcm_s32le"
    return "pcm_s24le"


def pcm_codec_for(info: MediaInfo) -> str:
    """``pcm_codec_for_bit_depth`` for a probed source (depth + sample format).

    ``getattr`` rather than attribute access so any object shaped like a probe
    result works — callers hand this duck-typed stand-ins, and a missing
    optional field should mean "unknown format", not an AttributeError.
    """
    return pcm_codec_for_bit_depth(
        getattr(info, "audio_bits_per_sample", None), getattr(info, "audio_sample_fmt", None)
    )


def is_float_codec(codec: str) -> bool:
    return codec in ("pcm_f32le", "pcm_f64le")


def path_to_file_uri(path: Path) -> str:
    """A ``file://`` URI for ``path``, percent-encoded and platform-correct.

    ``Path.as_uri()`` handles this properly cross-platform — notably on
    Windows, where a hand-rolled ``f"file://{quote(str(path))}"`` would encode
    backslashes as ``%5C`` instead of converting them to the forward slashes a
    URI requires, producing a URI FCPXML/other tools can't resolve. See
    PROJECT_ANALYSIS.md §3.1.
    """
    return path.resolve().as_uri()


def build_atempo_chain(factor: float) -> list[str]:
    filters: list[str] = []
    remaining = factor
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.6f}")
    return filters
