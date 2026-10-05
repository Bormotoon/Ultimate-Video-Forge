"""Generate a small deterministic multi-camera synchronization fixture."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import wave
from array import array
from collections.abc import Sequence
from pathlib import Path

SAMPLE_RATE = 48_000
DRIFT_FACTOR = 1.00075
CAMERA_B_OFFSET_S = 1.25
RECORDER_OFFSET_S = 2.0
SCRIPT = (
    "Studio fixture begins. "
    "This phrase will be repeated after a pause. "
    "This phrase will be repeated after a pause. "
    "Studio fixture ends."
)


def _run(command: Sequence[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


def _tool(name: str, alternatives: Sequence[str] = ()) -> str:
    for candidate in (name, *alternatives):
        executable = shutil.which(candidate)
        if executable:
            return executable
    tried = ", ".join((name, *alternatives))
    raise RuntimeError(f"required executable not found: {tried}")


def _synthesize_speech(path: Path) -> None:
    espeak = _tool("espeak-ng", ("espeak",))
    _run([espeak, "-v", "en", "-s", "145", "-w", str(path), SCRIPT])


def _insert_silence_and_repeat(source: Path, output: Path) -> None:
    with wave.open(str(source), "rb") as reader:
        channels = reader.getnchannels()
        sample_width = reader.getsampwidth()
        rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())
    if sample_width != 2:
        raise RuntimeError(f"expected 16-bit TTS WAV, got {sample_width * 8}-bit")

    samples = array("h")
    samples.frombytes(frames)
    midpoint = len(samples) // 2
    pause = array("h", [0]) * (rate * channels * 2)
    repeated = samples[:midpoint] + pause + samples[midpoint:] + pause + samples[midpoint:]

    with wave.open(str(output), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(rate)
        writer.writeframes(repeated.tobytes())


def _duration(ffprobe: str, path: Path) -> float:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _camera_video(
    ffmpeg: str,
    audio: Path,
    output: Path,
    *,
    color: str,
    label: str,
    offset_s: float = 0.0,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    audio_filter = f"adelay={int(offset_s * 1000)}:all=1" if offset_s else "anull"
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=640x360:r=25",
            "-i",
            str(audio),
            "-filter_complex",
            f"[1:a]{audio_filter}[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-metadata",
            f"comment={label}",
            "-c:v",
            "mpeg4",
            "-q:v",
            "5",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ]
    )


def _split_gopro_chapters(ffmpeg: str, source: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = _duration(_tool("ffprobe"), source)
    boundary = duration / 2
    chapters = [output_dir / "GX010024.MP4", output_dir / "GX020024.MP4"]
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-t",
            f"{boundary:.6f}",
            "-c",
            "copy",
            str(chapters[0]),
        ]
    )
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{boundary:.6f}",
            "-i",
            str(source),
            "-c",
            "copy",
            str(chapters[1]),
        ]
    )
    return chapters


def generate(output_dir: Path, *, force: bool = False) -> Path:
    ffmpeg = _tool("ffmpeg")
    ffprobe = _tool("ffprobe")
    if output_dir.exists():
        if not force:
            raise FileExistsError(f"output already exists: {output_dir} (use --force)")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="studio-fixture-") as temporary:
        temp = Path(temporary)
        raw = temp / "speech.wav"
        program = temp / "program.wav"
        _synthesize_speech(raw)
        _insert_silence_and_repeat(raw, program)

        recorder = output_dir / "recorder" / "ZOOM0001_Tr1.WAV"
        recorder.parent.mkdir(parents=True)
        _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(program),
                "-af",
                (
                    f"asetrate={SAMPLE_RATE}*{DRIFT_FACTOR},aresample={SAMPLE_RATE},"
                    f"adelay={int(RECORDER_OFFSET_S * 1000)}:all=1"
                ),
                "-ar",
                str(SAMPLE_RATE),
                "-ac",
                "1",
                str(recorder),
            ]
        )

        _camera_video(
            ffmpeg,
            program,
            output_dir / "camera-a" / "DJI_0001.MP4",
            color="0x224466",
            label="Camera A",
        )
        camera_b = temp / "camera-b.mp4"
        _camera_video(
            ffmpeg,
            program,
            camera_b,
            color="0x6b3f2a",
            label="Camera B GoPro chapters",
            offset_s=CAMERA_B_OFFSET_S,
        )
        chapters = _split_gopro_chapters(ffmpeg, camera_b, output_dir / "camera-b")

    manifest = {
        "schema_version": 1,
        "generator": "tools/make_fixtures.py",
        "sample_rate": SAMPLE_RATE,
        "drift_factor": DRIFT_FACTOR,
        "camera_b_offset_s": CAMERA_B_OFFSET_S,
        "recorder_offset_s": RECORDER_OFFSET_S,
        "features": ["two-cameras", "recorder", "drift", "pause", "retake", "gopro-chapters"],
        "files": [
            "camera-a/DJI_0001.MP4",
            *(str(path.relative_to(output_dir)) for path in chapters),
            "recorder/ZOOM0001_Tr1.WAV",
        ],
    }
    (output_dir / "fixture.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _duration(ffprobe, output_dir / "camera-a" / "DJI_0001.MP4")
    return output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=Path("fixtures/generated/sync-project"),
    )
    parser.add_argument("--force", action="store_true", help="replace an existing fixture")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated = generate(args.output.resolve(), force=args.force)
    print(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
