import json
import subprocess
from pathlib import Path

from studio.core.audio_spans import Span, detect_silence
from studio.core.project import ArtifactStatus, Project, StageManifest, describe_artifact
from studio.core.transcript import Segment, Transcript, Word
from studio.stages.retake_models import RetakeSettings
from studio.stages.roughcut import build_edit_list, review_cut


def test_roughcut_trims_head_tail_and_long_pause_but_marks_fillers() -> None:
    transcript = Transcript(
        Path("audio.wav"),
        "en",
        10,
        [
            Segment(1, 2, (Word("hello", 1, 2),)),
            Segment(5, 6, (Word("um", 5, 5.2), Word("world", 5.3, 6))),
        ],
    )
    edit = build_edit_list(
        transcript,
        mode="cut",
        minimum_pause_s=1,
        keep_pause_s=0.4,
        edge_pad_s=0.2,
    )
    assert [cut.reason for cut in edit.cuts] == ["head", "pause", "tail"]
    assert edit.markers[0].kind == "filler"
    assert all(end > start for start, end in edit.keep)


def test_audible_word_gap_is_not_cut() -> None:
    transcript = Transcript(Path("audio.wav"), "en", 5, [
        Segment(0, 5, (Word("before", 0, 1), Word("after", 4, 5))),
    ])
    options = dict(mode="cut", minimum_pause_s=1, keep_pause_s=0.4, edge_pad_s=0.2)
    assert not build_edit_list(transcript, silence=[], **options).cuts
    partial = build_edit_list(transcript, silence=[Span(1, 2)], **options)
    assert not partial.cuts
    quiet = build_edit_list(transcript, silence=[Span(1, 4)], **options)
    assert [cut.reason for cut in quiet.cuts] == ["pause"]


def test_silence_gate_preserves_audible_untranscribed_audio(tmp_path: Path) -> None:
    source = tmp_path / "sound.wav"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=4", "-af", "volume=enable='between(t,1,3)':volume=0",
        str(source),
    ], check=True)
    spans = detect_silence(source, duration_s=4)
    assert len(spans) == 1
    assert 0.9 < spans[0].start_s < 1.1
    assert 2.9 < spans[0].end_s < 3.1


def test_retake_removes_failed_attempts_without_cutting_words() -> None:
    words = [Word(token, start + index * 0.2, start + index * 0.2 + 0.15)
             for start in (0, 3, 6)
             for index, token in enumerate("one two three four five".split())]
    transcript = Transcript(Path("audio.wav"), "en", 7, [Segment(0, 7, tuple(words))])
    edit = build_edit_list(
        transcript, mode="cut", minimum_pause_s=1, keep_pause_s=0.4, edge_pad_s=0.2,
        silence=[], retakes=RetakeSettings(detect_retakes=True),
    )
    assert [(cut.start, cut.end) for cut in edit.cuts if cut.reason == "retake"] == [(0, 6)]
    assert edit.keep == ((6, 7),)
    assert not any(word.start < cut.end < word.end for word in words for cut in edit.cuts)


def test_review_rejects_cut_without_invalidating_detector_manifest(tmp_path: Path) -> None:
    project = Project(tmp_path, tmp_path / "_studio")
    transcript = Transcript(Path("audio.wav"), "en", 5, [
        Segment(0, 5, (Word("before", 0, 1), Word("after", 4, 5))),
    ])
    transcript_path = project.work_dir / "transcript.json"
    transcript.save(transcript_path)
    project.transcripts["timeline"] = transcript_path
    edit = build_edit_list(
        transcript, mode="cut", minimum_pause_s=1, keep_pause_s=0.4, edge_pad_s=0.2,
        silence=[Span(1, 4)],
    )
    edit_path = project.work_dir / "stages" / "roughcut" / "edit.json"
    edit.save(edit_path)
    project.outputs["roughcut"] = [edit_path]
    manifest_path = project.work_dir / "manifests" / "roughcut.json"
    StageManifest("roughcut", "original-fingerprint", {},
                  [describe_artifact(project.work_dir, edit_path)],
                  ArtifactStatus.OK, "test").save(manifest_path)
    review_cut(project, edit.cuts[0].id, accepted=False)
    data = json.loads(edit_path.read_text())
    assert data["keep"] == [{"start": 0, "end": 5, "camera_id": None}]
    assert not data["cuts"][0]["accepted"]
    assert StageManifest.load(manifest_path).reusable(project.work_dir, "original-fingerprint")
    assert json.loads((project.work_dir / "edit-overrides.json").read_text()) == {
        edit.cuts[0].id: False,
    }
    review_cut(project, edit.cuts[0].id, accepted=True)
    assert json.loads(edit_path.read_text())["cuts"][0]["accepted"]
