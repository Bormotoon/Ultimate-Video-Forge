from fractions import Fraction
from pathlib import Path

from studio.stages.export import (
    Sequence,
    SequenceClip,
    fcpxml_intervals,
    write_fcpxml,
    write_xmeml,
    xmeml_intervals,
)


def test_fcpxml_round_trip_preserves_sequence_intervals(tmp_path: Path) -> None:
    source = tmp_path / "camera.mov"
    source.write_bytes(b"placeholder")
    sequence = Sequence(
        "episode",
        Fraction(25, 1),
        1920,
        1080,
        (
            SequenceClip("a", source, 2.0, 5.0, 1.0, 0, True),
            SequenceClip("b", source, 0.0, 3.0, 7.0, 1, True),
        ),
    )
    output = write_fcpxml(sequence, tmp_path / "episode.fcpxml", media_base=tmp_path)
    assert fcpxml_intervals(output) == [("camera", 1.0, 5.0), ("camera", 7.0, 3.0)]
    assert "camera.mov" in output.read_text(encoding="utf-8")


def test_xmeml_round_trip_preserves_sequence_intervals(tmp_path: Path) -> None:
    source = tmp_path / "camera clip.mov"
    source.write_bytes(b"placeholder")
    sequence = Sequence(
        "episode",
        Fraction(25, 1),
        1920,
        1080,
        (SequenceClip("a", source, 2.0, 5.0, 1.0, 0, True),),
    )
    output = write_xmeml(sequence, tmp_path / "episode.xml")
    assert xmeml_intervals(output) == [("camera clip", 1.0, 5.0)]
    text = output.read_text(encoding="utf-8")
    assert "<!DOCTYPE xmeml>" in text
    assert "file://localhost/" in text
