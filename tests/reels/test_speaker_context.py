import json

import pytest

from studio.core.project import Project
from studio.core.timeline import TimeDomain
from studio.core.transcript import Transcript
from studio.core.transcript_index import TimedSentence, TranscriptIndex
from studio.reels.analysis.context import build_transcript_digest
from studio.reels.analysis.speaker_context import speaker_change_starts, speaker_context_identity


def fixture(tmp_path):
    project = Project(tmp_path, tmp_path / "work")
    turns = tmp_path / "diarization.json"
    report = tmp_path / "report.json"
    project.outputs["speakers"] = [turns, report]
    report.write_text('{"time_domain": "timeline"}')
    turns.write_text(
        json.dumps(
            [
                {"start": 0, "end": 5, "speaker": "A"},
                {"start": 5, "end": 10, "speaker": "A"},
                {"start": 10, "end": 15, "speaker": "B"},
            ]
        )
    )
    transcript = Transcript(tmp_path / "audio.wav", "en", 30, [], time_domain=TimeDomain.TIMELINE)
    return project, transcript, turns, report


def test_changes_ignore_repeated_speaker_and_affect_identity(tmp_path):
    project, transcript, turns, _ = fixture(tmp_path)
    assert speaker_change_starts(project, transcript) == ([10.0], "ok")
    before = speaker_context_identity(project)
    turns.write_text("[]")
    assert speaker_context_identity(project) != before


def test_edited_transcript_requires_mapping(tmp_path):
    project, transcript, _, report = fixture(tmp_path)
    transcript.time_domain = TimeDomain.EDITED
    assert speaker_change_starts(project, transcript) == ([], "missing_edit_mapping")
    transcript.time_domain = TimeDomain.TIMELINE
    report.write_text('{"time_domain": "source"}')
    assert speaker_change_starts(project, transcript) == ([], "incompatible_time_domain")


def test_invalid_turns_and_missing_report_are_rejected(tmp_path):
    project, transcript, turns, report = fixture(tmp_path)
    turns.write_text('[{"start": "NaN", "end": 10, "speaker": "A"}]')
    with pytest.raises(ValueError):
        speaker_change_starts(project, transcript)
    project.outputs["speakers"].remove(report)
    with pytest.raises(ValueError, match="report"):
        speaker_change_starts(project, transcript)


def test_speaker_change_adds_otherwise_unsampled_sentence():
    index = TranscriptIndex(
        sentences=[
            TimedSentence(0, 5, "Opening statement."),
            TimedSentence(10, 15, "A plain reply."),
            TimedSentence(60, 65, "Closing statement."),
        ]
    )
    assert "A plain reply" not in build_transcript_digest(index)
    assert "A plain reply" in build_transcript_digest(index, speaker_turns=[10])


def test_rendered_mapping_handles_cuts_and_frame_padding(tmp_path):
    from studio.core.timeline import EditMap, KeepRange
    from studio.stages.program import rendered_timeline_map

    project, transcript, _, _ = fixture(tmp_path)
    edit = EditMap("edit", (KeepRange(0, 5), KeepRange(9, 15)))
    report = {"fps": "25", "piece_durations_s": [5, 6], "frame_counts": [126, 150]}
    mapping = rendered_timeline_map(edit, report)
    assert mapping[1]["edited_start"] == pytest.approx(5.04)
    path = tmp_path / "render.json"
    project.outputs["program_report"] = [path]
    transcript.time_domain = TimeDomain.EDITED
    transcript.duration = 11.04
    from studio.core.project import stable_fingerprint

    timing = stable_fingerprint(transcript.duration, [])
    path.write_text(
        json.dumps({"timeline_to_rendered": mapping, "edited_timing_fingerprint": timing})
    )
    starts, status = speaker_change_starts(project, transcript)
    assert status == "mapped_edited"
    assert starts == pytest.approx([6.04])
    edit = EditMap("removed", (KeepRange(0, 5), KeepRange(11, 15)))
    path.write_text(
        json.dumps(
            {
                "edited_timing_fingerprint": timing,
                "timeline_to_rendered": rendered_timeline_map(
                    edit, {"fps": "25", "piece_durations_s": [5, 4], "frame_counts": [125, 100]}
                ),
            }
        )
    )
    assert speaker_change_starts(project, transcript) == ([5.0], "mapped_edited")
    transcript.duration += 1
    assert speaker_change_starts(project, transcript) == ([], "stale_edit_mapping")


def test_mapping_excludes_frame_trimmed_tail():
    from studio.core.timeline import EditMap, KeepRange
    from studio.stages.program import rendered_timeline_map

    mapping = rendered_timeline_map(
        EditMap("trim", (KeepRange(10, 11.01),)),
        {"fps": "25", "piece_durations_s": [1.01], "frame_counts": [25]},
    )
    assert mapping == [
        {"timeline_start": 10, "timeline_end": 11, "edited_start": 0, "edited_end": 1}
    ]


@pytest.mark.parametrize(
    "right_speaker,overlap,expected",
    [
        ("B", False, [5.0]),
        ("A", False, []),
        ("unknown", False, []),
        ("B", True, []),
    ],
)
def test_cut_changes_require_unambiguous_distinct_speakers(
    tmp_path, right_speaker, overlap, expected
):
    from studio.core.project import stable_fingerprint

    project, transcript, turns, _ = fixture(tmp_path)
    data = [
        {"start": 0, "end": 5, "speaker": "A"},
        {"start": 10, "end": 15, "speaker": right_speaker},
    ]
    if overlap:
        data.append({"start": 10, "end": 15, "speaker": "C"})
    turns.write_text(json.dumps(data))
    transcript.time_domain = TimeDomain.EDITED
    transcript.duration = 10
    path = tmp_path / "render.json"
    path.write_text(
        json.dumps(
            {
                "edited_timing_fingerprint": stable_fingerprint(10, []),
                "timeline_to_rendered": [
                    {"timeline_start": 0, "timeline_end": 5, "edited_start": 0, "edited_end": 5},
                    {"timeline_start": 11, "timeline_end": 16, "edited_start": 5, "edited_end": 10},
                ],
            }
        )
    )
    project.outputs["program_report"] = [path]
    assert speaker_change_starts(project, transcript) == (expected, "mapped_edited")
