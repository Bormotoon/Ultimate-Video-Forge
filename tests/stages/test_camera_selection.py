from pathlib import Path

import pytest

from studio.core.project import Asset, AssetRole, Project
from studio.core.timeline import EditMap, KeepRange, SourcePlacement
from studio.stages.camera_selection import speaker_camera_edit
from studio.stages.program import program_pieces


def project() -> Project:
    result = Project(Path("/media"), Path("/work"))
    result.assets = [Asset("a", Path("a.mp4"), "video", AssetRole.CAMERA),
                     Asset("b", Path("b.mp4"), "video", AssetRole.CAMERA)]
    result.assets[1].group_id = "Bob-camera"
    result.placements = [SourcePlacement("a", 0, 0, 10), SourcePlacement("b", 0, 0, 10)]
    return result


def test_speaker_switches_preserve_timeline_and_drive_program_pieces() -> None:
    source = project()
    edit = speaker_camera_edit(source, EditMap("cut", (KeepRange(0, 4),)), [
        {"start": 0, "end": 2, "speaker": "Alice"},
        {"start": 2, "end": 4, "speaker": "Bob"},
    ], {"Alice": "a", "Bob": "Bob-camera"})
    assert edit.keep == (KeepRange(0, 2, "a"), KeepRange(2, 4, "b"))
    assert [piece.video.name for piece in program_pieces(source, edit)] == ["a.mp4", "b.mp4"]


def test_uncertain_and_short_turns_hold_previous_camera() -> None:
    edit = speaker_camera_edit(project(), EditMap("cut", (KeepRange(0, 4),)), [
        {"start": 0, "end": 0.2, "speaker": "Alice"},
        {"start": 0.2, "end": 0.4, "speaker": "Bob"},
        {"start": 0.4, "end": 2, "speaker": "overlap"},
        {"start": 2, "end": 3, "speaker": "unknown"},
    ], {"Alice": "a", "Bob": "b"}, min_shot_s=1)
    assert edit.keep == (KeepRange(0, 4, "a"),)


def test_uncovered_preferred_camera_falls_back_and_manual_selection_wins() -> None:
    source = project()
    source.placements[1] = SourcePlacement("b", 2, 0, 2)
    turns = [{"start": 0, "end": 4, "speaker": "Bob"}]
    edit = speaker_camera_edit(source, EditMap("cut", (KeepRange(0, 4),)),
                               turns, {"Bob": "b"}, min_shot_s=0)
    assert edit.keep == (KeepRange(0, 2, "a"), KeepRange(2, 4, "b"))
    manual = speaker_camera_edit(source, EditMap("cut", (KeepRange(0, 4, "a"),)),
                                 turns, {"Bob": "b"}, min_shot_s=0)
    assert manual.keep == (KeepRange(0, 4, "a"),)


def test_removed_ranges_are_not_reintroduced() -> None:
    edit = speaker_camera_edit(project(), EditMap("cut", (KeepRange(0, 1), KeepRange(3, 4))),
                               [], {"Alice": "a"})
    assert edit.keep == (KeepRange(0, 1, "a"), KeepRange(3, 4, "a"))


def test_invalid_camera_mapping_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown speaker camera"):
        speaker_camera_edit(project(), EditMap("cut", (KeepRange(0, 1),)), [], {"X": "missing"})
