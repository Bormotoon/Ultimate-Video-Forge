import json
import subprocess
from pathlib import Path

from studio.core.project import Project
from studio.core.settings import Settings
from studio.core.subtitles import Cue, SubtitleStyle, save_edits
from studio.core.timeline import TimeDomain
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.program_subtitles import ProgramSubtitlesStage
from studio.stages.runner import run_stage_process


def test_program_captions_worker_publication_and_reuse(tmp_path: Path) -> None:
    video = tmp_path / "program.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:r=25:d=1",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
    )
    original = video.read_bytes()
    path = tmp_path / "edited.json"
    transcript = Transcript(
        video, "en", 1, [Segment(0, 1, (Word("hello", 0, 1),))], time_domain=TimeDomain.EDITED
    )
    transcript.save(path)
    work = tmp_path / "_studio"
    project = Project(tmp_path, work, transcripts={"edited": path}, outputs={"program": [video]})
    project_path = work / "project.json"
    project.save(project_path)
    save_edits(
        work,
        "program",
        transcript,
        [Cue(0.1, 0.9, "Manual program caption")],
        SubtitleStyle(size=60),
    )
    settings = Settings().to_dict()
    settings["program"]["burn_subtitles"] = True
    stage = ProgramSubtitlesStage()
    fingerprint = stage.fingerprint(project, settings)
    result = run_stage_process(
        stage.id, project_path, work / "settings.yaml", fingerprint, effective_settings=settings
    )
    assert result.manifest.reusable(work, fingerprint)
    updated = Project.load(project_path)
    assert stage.fingerprint(updated, settings) == fingerprint
    artifacts = updated.outputs[stage.id]
    assert "Manual program caption" in next(p for p in artifacts if p.suffix == ".srt").read_text()
    assert video.read_bytes() == original
    rendered = next(p for p in artifacts if p.suffix == ".mp4")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(rendered)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert abs(float(json.loads(probe.stdout)["format"]["duration"]) - 1) < 0.05
