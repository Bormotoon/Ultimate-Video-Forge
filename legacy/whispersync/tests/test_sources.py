"""Source identity: two inputs must never share an artifact name.

The defects these lock down all had the same shape — a display name used as an
identifier. ``/A/take.wav`` and ``/B/take.wav`` both produced
``.master/take_master.wav``, so the second extraction overwrote the first and
both "recorders" then rendered from one file; ``clip.mov`` and ``clip.mp4``
from one camera collided the same way; two cameras' identically named clips
overwrote each other's transcripts and ambience; and ``startswith`` matching
paired ``A1`` with ``A10``.
"""

from __future__ import annotations

from pathlib import Path

from whispersync.engine.sources import assign_source_ids, slugify, unique_name


def _sids(entries):
    return [ref.sid for ref in assign_source_ids(entries)]


def test_same_stem_in_different_folders_gets_distinct_ids() -> None:
    sids = _sids(
        [
            ("recorder", "", Path("/A/take.wav")),
            ("recorder", "", Path("/B/take.wav")),
        ]
    )
    assert len(set(sids)) == 2, sids


def test_same_stem_different_extensions_gets_distinct_ids() -> None:
    """One camera holding clip.mov and clip.mp4 is a real shoot, not an edge case."""
    sids = _sids(
        [
            ("camera", "camA", Path("/v/camA/clip.mov")),
            ("camera", "camA", Path("/v/camA/clip.mp4")),
        ]
    )
    assert len(set(sids)) == 2, sids
    assert any("mov" in s for s in sids) and any("mp4" in s for s in sids)


def test_same_clip_name_on_two_cameras_gets_distinct_ids() -> None:
    sids = _sids(
        [
            ("camera", "camA", Path("/v/camA/DJI_0001.MOV")),
            ("camera", "camB", Path("/v/camB/DJI_0001.MOV")),
        ]
    )
    assert len(set(sids)) == 2, sids


def test_cameras_and_recorders_share_one_namespace() -> None:
    """Artifacts from both kinds land in the same folders, so their ids must
    be unique across kinds too, not just within a kind."""
    used: set[str] = set()
    cams = assign_source_ids([("camera", "", Path("/v/take.mov"))], used)
    recs = assign_source_ids([("recorder", "", Path("/a/take.wav"))], used)
    assert cams[0].sid != recs[0].sid


def test_artifact_names_are_unique_for_every_source() -> None:
    """The property that actually matters: no two sources may produce the same
    artifact path, for any artifact kind."""
    entries = [
        ("camera", "camA", Path("/v/camA/clip.mov")),
        ("camera", "camA", Path("/v/camA/clip.mp4")),
        ("camera", "camB", Path("/v/camB/clip.mov")),
        ("recorder", "", Path("/A/take.wav")),
        ("recorder", "", Path("/B/take.wav")),
    ]
    refs = assign_source_ids(entries)
    for suffix in ("master.wav", "voice.wav", "ambience.wav", ""):
        names = [r.artifact_name(suffix) for r in refs]
        assert len(set(names)) == len(names), f"collision for {suffix!r}: {names}"


def test_ids_are_stable_for_the_same_input_set() -> None:
    entries = [
        ("camera", "camA", Path("/v/camA/a.mov")),
        ("recorder", "", Path("/a/r.wav")),
    ]
    assert _sids(entries) == _sids(entries)


def test_ids_are_filesystem_safe() -> None:
    """Ids are handed to ffmpeg and to third-party CLIs that derive their own
    output names from the input's."""
    refs = assign_source_ids(
        [
            ("camera", "Cam A/B", Path("/v/my clip (2).mov")),
            ("recorder", "", Path("/a/запись №1.wav")),
        ]
    )
    for ref in refs:
        assert "/" not in ref.sid
        assert " " not in ref.sid
        assert ref.sid == ref.sid.strip()
        assert ref.sid


def test_unique_name_never_collides_and_is_not_prefix_matched() -> None:
    used: set[str] = set()
    a1 = unique_name("A1", used)
    a10 = unique_name("A10", used)
    assert a1 != a10
    # The point of using equality (never startswith) downstream: A1 IS a
    # prefix of A10, and prefix matching is what mis-paired clips in --verify.
    assert a10.startswith(a1)


def test_slugify_never_returns_empty() -> None:
    assert slugify("") == "source"
    assert slugify("...") == "source"
    assert slugify("///") == "source"
