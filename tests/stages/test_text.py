import json
from pathlib import Path

import studio.stages.text as text
from studio.core.project import Project
from studio.core.timeline import TimeDomain
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.base import StageContext
from studio.stages.text import TextStage, faithfulness_report, is_correction_safe


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses

    def complete(self, prompt: str, *, grammar: str | None = None) -> str:
        return self.responses.pop(0)


def _transcript(path: Path) -> Transcript:
    return Transcript(
        path,
        "ru",
        3.0,
        [Segment(0.0, 3.0, (Word("privet", 0.0, 1.0), Word("mir", 1.0, 3.0)))],
        time_domain=TimeDomain.TIMELINE,
    )


def test_text_stage_proofreads_and_writes_article(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    project = Project(tmp_path, tmp_path / "_studio")
    transcript_path = project.work_dir / "stages" / "timeline" / "transcript.json"
    transcript_path.parent.mkdir(parents=True)
    _transcript(tmp_path / "audio.wav").save(transcript_path)
    project.transcripts["timeline"] = transcript_path
    provider = FakeProvider([
        json.dumps({"segments": [{"id": 0, "text": "Privet, mir."}]}),
        "## Greeting\n\nPrivet, mir.",
    ])
    monkeypatch.setattr(text, "LlamaProvider", lambda *args: provider)
    settings = {"text": {"proofread": True, "article": True, "model": "fake"}}

    output = TextStage().run(StageContext(project, settings, project.work_dir))

    proofread = Transcript.load(project.work_dir / "stages" / "text" / "proofread.json")
    assert proofread.segments[0].text == "Privet, mir."
    assert proofread.segments[0].words[0].text == "Privet,"
    assert proofread.segments[0].raw_words is not None
    assert "## Greeting" in (project.work_dir / "stages" / "text" / "article.md").read_text()
    assert len(output.artifacts) == 5


def test_text_guardrails_reject_content_changes() -> None:
    assert is_correction_safe("hello world", "Hello, world.")
    assert not is_correction_safe("hello world", "Hello world and goodbye everyone.")
    assert not faithfulness_report("source words", "unrelated invented vocabulary") ["ok"]