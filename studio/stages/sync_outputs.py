"""Sample-accurate voice segmentation and nonduplicating timeline master."""

import json
import math
import subprocess
from pathlib import Path

from studio.core.timeline import SourcePlacement
from studio.stages.sync_render import cut_wav_segment, mix_clips_on_timeline


def segment_voice(source: Path, directory: Path, minutes: float) -> tuple[Path, ...]:
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError("segment duration must be finite and positive")
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_streams",
        "-of", "json", str(source),
    ], capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    rate = int(stream["sample_rate"])
    from fractions import Fraction

    frames = round(int(stream["duration_ts"]) * Fraction(stream["time_base"]) * rate)
    codec = stream["codec_name"]
    if codec not in {"pcm_s16le", "pcm_s24le", "pcm_s32le"}:
        raise ValueError("segmentation requires integer PCM WAV")
    step = max(1, round(minutes * 60 * rate))
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = []
    entries = []
    for index, start in enumerate(range(0, frames, step), 1):
        end = min(frames, start + step)
        output = directory / f"segment-{index:04d}.wav"
        cut_wav_segment(source, output, start / rate, end / rate, codec=codec)
        artifacts.append(output)
        entries.append({"path": output.name, "source_start_frame": start,
                        "frame_count": end - start, "rendered_start_s": start / rate,
                        "duration_s": (end - start) / rate})
    manifest = directory / "segments.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "time_domain": "rendered_audio", "sample_rate": rate,
        "source_name": source.name, "segments": entries,
    }, indent=2) + "\n", encoding="utf-8")
    return (*artifacts, manifest)


def render_voice_master(
    placements: list[SourcePlacement], voices: dict[str, Path], output: Path,
) -> Path:
    candidates = [item for item in placements if item.asset_id in voices]
    if not candidates:
        raise ValueError("no synchronized voice is available for the master")
    boundaries = sorted({value for item in candidates
                         for value in (item.offset_s, item.offset_s + item.duration_s * item.k)})
    import tempfile

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".master-", dir=output.parent) as temporary:
        clips = []
        for index, (start, end) in enumerate(zip(boundaries, boundaries[1:], strict=False)):
            available = [item for item in candidates
                         if item.offset_s <= start
                         and item.offset_s + item.duration_s * item.k >= end - 1e-9]
            if not available:
                continue
            # Overlapping cameras carry the same dialogue. Select one voice,
            # never sum all camera replacements into a louder duplicate.
            selected = available[0]
            cut = Path(temporary) / f"part-{index}.wav"
            source_start = (start - selected.offset_s) / selected.k
            source_end = (end - selected.offset_s) / selected.k
            if abs(selected.k - 1) < 1e-9:
                cut_wav_segment(voices[selected.asset_id], cut, source_start, source_end)
            else:
                factor = 1 / selected.k
                tempos = []
                while factor < 0.5:
                    tempos.append("atempo=0.5")
                    factor /= 0.5
                while factor > 2:
                    tempos.append("atempo=2")
                    factor /= 2
                tempos.append(f"atempo={factor}")
                result = subprocess.run([
                    "ffmpeg", "-nostdin", "-v", "error", "-y", "-i",
                    str(voices[selected.asset_id]), "-af",
                    f"atrim=start={source_start}:end={source_end},asetpts=PTS-STARTPTS,"
                    + ",".join(tempos) + f",apad,atrim=duration={end - start}",
                    "-c:a", "pcm_s24le", str(cut),
                ], capture_output=True, text=True)
                if result.returncode:
                    raise RuntimeError(f"master retiming failed: {result.stderr.strip()}")
            clips.append((cut, start))
        return mix_clips_on_timeline(clips, boundaries[-1], 48000, output, channels=2)
