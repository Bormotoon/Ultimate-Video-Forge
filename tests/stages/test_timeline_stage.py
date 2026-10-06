from pathlib import Path

from studio.core.project import Asset, AssetKind, AssetRole, Project
from studio.core.timeline import SourcePlacement, TimeDomain
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.base import StageContext
from studio.stages.timeline import TimelineStage


def test_timeline_stage_maps_words_without_retranscription(tmp_path: Path) -> None:
    work = tmp_path / "_studio"
    source_path = work / "stages" / "transcribe" / "rec.json"
    source = Transcript(
        Path("rec.wav"),
        "en",
        10,
        [Segment(2, 3, (Word("mapped", 2.25, 2.75),))],
    )
    source.save(source_path)
    project = Project(
        tmp_path,
        work,
        assets=[Asset("rec", Path("rec.wav"), AssetKind.AUDIO, AssetRole.RECORDER)],
        placements=[SourcePlacement("rec", -1.0, 0.0, 10.0, 1.01, "text")],
        transcripts={"rec": source_path},
    )
    result = TimelineStage().run(StageContext(project, {}, work))
    timeline = Transcript.load(result.artifacts[0])
    assert timeline.time_domain is TimeDomain.TIMELINE
    assert timeline.words[0].start == 1.272
    assert timeline.words[0].end == 1.777
    assert timeline.map_id == "placement:rec"
