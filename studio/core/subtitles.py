"""Project subtitle edits shared by desktop preview and rendering."""

from __future__ import annotations

import json
import math
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from studio.core.project import stable_fingerprint
from studio.core.publication import publish_files, recover_publications
from studio.core.transcript import Segment, Transcript, to_srt
from studio.core.workspace import project_lock


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SubtitleStyle:
    font: str = "DejaVu Sans"
    size: int = 42
    color: str = "#FFFFFF"
    outline: int = 2
    margin: int = 40
    alignment: int = 2


PRESETS = {
    "Classic": SubtitleStyle(),
    "Large": SubtitleStyle(size=60, outline=3, margin=60),
    "Top": SubtitleStyle(alignment=8),
}


def transcript_identity(transcript: Transcript) -> str:
    normalized = Transcript.from_dict(transcript.to_dict())
    return stable_fingerprint(
        float(normalized.duration),
        normalized.to_dict()["segments"],
        normalized.time_domain.value,
        normalized.map_id,
        normalized.metadata.get("reel_source_start"),
        normalized.metadata.get("reel_source_time_domain"),
    )


def edit_path(work_dir: Path, identity: str) -> Path:
    return work_dir / "subtitles" / f"{stable_fingerprint(identity)}.json"


def validate(cues: list[Cue], style: SubtitleStyle, duration: float) -> None:
    previous = 0.0
    for cue in cues:
        if not (
            math.isfinite(cue.start)
            and math.isfinite(cue.end)
            and previous <= cue.start < cue.end <= duration + 0.001
        ):
            raise ValueError("Cue times must be ordered, non-overlapping and inside the video.")
        if not cue.text.strip():
            raise ValueError("Subtitle text must not be empty.")
        previous = cue.end
    if (
        not style.font.strip()
        or any(char in style.font for char in ",\n\r")
        or not 12 <= style.size <= 160
        or not 0 <= style.outline <= 10
        or not 0 <= style.margin <= 500
        or style.alignment not in {2, 8}
        or not re.fullmatch(r"#[0-9a-fA-F]{6}", style.color)
    ):
        raise ValueError("Invalid subtitle style.")


def load_edits(
    work_dir: Path, identity: str, transcript: Transcript
) -> tuple[list[Cue], SubtitleStyle]:
    path = edit_path(work_dir, identity)
    if not path.is_file():
        return (
            [
                Cue(segment.start, segment.end, segment.text)
                for segment in transcript.segments
                if segment.text.strip()
            ],
            SubtitleStyle(),
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("transcript") != transcript_identity(transcript):
        raise ValueError(
            "Saved subtitles belong to an older transcript. Review them before replacing."
        )
    cues = [Cue(**cue) for cue in data["cues"]]
    style = SubtitleStyle(**data["style"])
    validate(cues, style, transcript.duration)
    return cues, style


def save_edits(
    work_dir: Path, identity: str, transcript: Transcript, cues: list[Cue], style: SubtitleStyle
) -> None:
    validate(cues, style, transcript.duration)
    payload = {
        "schema_version": 1,
        "transcript": transcript_identity(transcript),
        "cues": [asdict(cue) for cue in cues],
        "style": asdict(style),
    }
    path = edit_path(work_dir, identity)
    with project_lock(work_dir):
        recover_publications(work_dir)
        with tempfile.TemporaryDirectory(prefix=".subtitle-save-", dir=work_dir) as directory:
            temporary = Path(directory)
            json_path, srt_path, ass_path = (
                temporary / name for name in ("edit.json", "edit.srt", "edit.ass")
            )
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            local = Transcript(
                transcript.source_audio,
                transcript.language,
                transcript.duration,
                [Segment(cue.start, cue.end, text_override=cue.text) for cue in cues],
            )
            srt_path.write_text(to_srt(local), encoding="utf-8")
            ass_path.write_text(to_ass(cues, style), encoding="utf-8")
            publish_files(
                work_dir,
                [
                    (json_path, path),
                    (srt_path, path.with_suffix(".srt")),
                    (ass_path, path.with_suffix(".ass")),
                ],
            )


def to_ass(cues: list[Cue], style: SubtitleStyle, *, portrait: bool = False) -> str:
    color = "&H00" + style.color[5:7] + style.color[3:5] + style.color[1:3]
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        + ("PlayResX: 1080\nPlayResY: 1920\n" if portrait else "PlayResX: 1920\nPlayResY: 1080\n")
        + "WrapStyle: 0\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{style.font},{style.size},{color},{color},&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,{style.outline},0,{style.alignment},40,40,{style.margin},1\n"
        "\n[Events]\nFormat: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def clock(seconds: float) -> str:
        ticks = round(seconds * 100)
        hours, ticks = divmod(ticks, 360000)
        minutes, ticks = divmod(ticks, 6000)
        secs, ticks = divmod(ticks, 100)
        return f"{hours}:{minutes:02}:{secs:02}.{ticks:02}"

    events = []
    for cue in cues:
        text = cue.text.replace("\\", "").replace("{", "").replace("}", "")
        text = text.replace("\r", "").replace("\n", r"\N")
        events.append(f"Dialogue: 0,{clock(cue.start)},{clock(cue.end)},Default,,0,0,0,,{text}")
    return header + "\n".join(events) + "\n"
