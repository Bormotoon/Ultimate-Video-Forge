"""Tests for FCPXML generation and validation."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from whispersync.engine.export import (
    fps_to_frame_duration,
    generate_fcpxml,
    to_rational,
    validate_fcpxml,
)
from whispersync.engine.media import MediaInfo
from whispersync.models import MediaClip, SyncPlan


def test_to_rational() -> None:
    assert to_rational(1.0, 30000) == "30000/30000s"
    assert to_rational(0.0, 30000) == "0/30000s"
    assert to_rational(2.0, 48000) == "96000/48000s"


def test_fps_to_frame_duration() -> None:
    assert fps_to_frame_duration(Fraction(30000, 1001)) == "1001/30000s"
    assert fps_to_frame_duration(Fraction(25, 1)) == "1/25s"


def test_generate_and_validate_fcpxml(tmp_path: Path) -> None:
    video_info = MediaInfo(
        path=Path("/videos/clip1.mp4"),
        duration=60.0,
        fps=Fraction(30000, 1001),
        width=1920,
        height=1080,
        video_codec="h264",
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
    )

    plan = SyncPlan(
        strategy_id=1,
        clips=[
            MediaClip(
                path=Path("/videos/clip1.mp4"),
                kind="video",
                offset=0.0,
                in_point=0.0,
                duration=60.0,
                lane=1,
            ),
            MediaClip(
                path=Path("/audio/synced.wav"),
                kind="audio",
                offset=2.0,
                in_point=0.0,
                duration=58.0,
                lane=-1,
            ),
        ],
        total_duration=60.0,
    )

    output = tmp_path / "test_output.fcpxml"
    result = generate_fcpxml(plan, [video_info], output)

    assert result.exists()
    assert validate_fcpxml(result)

    tree = ET.parse(result)
    root = tree.getroot()
    assert root.tag == "fcpxml"
    assert root.get("version") == "1.9"

    spine = root.find(".//spine")
    assert spine is not None

    # New layout: the video clip is the primary-storyline element (directly in the
    # spine, no lane), and the audio is a connected clip nested under it on lane -1.
    spine_video = spine.findall("asset-clip")
    assert len(spine_video) == 1, "the video clip should sit directly in the spine"
    video_clip = spine_video[0]
    assert video_clip.get("lane") is None, "primary-storyline clip carries no lane"

    connected = video_clip.findall("asset-clip")
    assert len(connected) == 1, "the audio should be connected to the video clip"
    assert connected[0].get("lane") == "-1"

    # the audio's offset is relative to the parent's start (2s here)
    clips = [video_clip, connected[0]]

    # every asset-clip must reference a declared asset
    asset_ids = {a.get("id") for a in root.findall(".//asset")}
    for c in clips:
        assert c.get("ref") in asset_ids

    # FCPXML 1.9+ DTD: the file reference must live on <media-rep src=...>, never
    # as a `src` attribute on <asset> (Final Cut rejects the latter on import).
    for asset in root.findall(".//asset"):
        assert asset.get("src") is None, "asset must not carry a src attribute"
        rep = asset.find("media-rep")
        assert rep is not None and rep.get("src"), "asset needs a <media-rep src=...>"


def test_fcpxml_roundtrip_times(tmp_path: Path) -> None:
    video_info = MediaInfo(
        path=Path("/v/a.mp4"),
        duration=120.0,
        fps=Fraction(25, 1),
        width=1920,
        height=1080,
        video_codec="h264",
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
    )

    plan = SyncPlan(
        strategy_id=1,
        clips=[
            MediaClip(Path("/v/a.mp4"), "video", 0.0, 0.0, 120.0, 1),
            MediaClip(Path("/a/r.wav"), "audio", 5.0, 0.0, 115.0, -1),
        ],
        total_duration=120.0,
    )

    out = tmp_path / "rt.fcpxml"
    generate_fcpxml(plan, [video_info], out)

    tree = ET.parse(out)
    clips = tree.findall(".//asset-clip")
    assert len(clips) == 2


def test_spine_times_are_frame_aligned(tmp_path: Path) -> None:
    """FCP rejects spine offsets/durations that are not on a frame boundary, and
    warns about a custom format name. Both must be avoided."""
    video_info = MediaInfo(
        path=Path("/v/DJI_0829.mp4"),
        duration=60.04,
        fps=Fraction(30000, 1001),  # 29.97 fps -> one frame = 1001/30000 s
        width=1920,
        height=1080,
        video_codec="h264",
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
    )
    # deliberately non-frame-aligned offsets/durations
    plan = SyncPlan(
        strategy_id=1,
        clips=[
            MediaClip(Path("/v/DJI_0829.mp4"), "video", 2.9106329, 0.0, 60.04, 1),
            MediaClip(Path("/a/synced_000.wav"), "audio", 2.9106329, 0.0, 60.04, -1),
        ],
        total_duration=62.95,
    )
    out = tmp_path / "fa.fcpxml"
    generate_fcpxml(plan, [video_info], out, audio_sample_rate=48000)

    root = ET.parse(out).getroot()

    fmt = root.find(".//format")
    assert fmt is not None and "name" not in fmt.attrib  # custom name -> FCP warns

    for ac in root.findall(".//asset-clip"):
        for attr in ("offset", "duration", "start"):
            ticks_s, den_s = ac.get(attr, "0/30000s")[:-1].split("/")
            assert int(den_s) == 30000
            assert int(ticks_s) % 1001 == 0, f"{attr} not on a frame boundary: {ac.get(attr)}"


def test_mixed_fps_and_relative_src(tmp_path: Path) -> None:
    """Cameras at different rates each get a format at their native fps, and media
    living next to the FCPXML is referenced by a relative path."""
    (tmp_path / "audio_synced").mkdir()
    synced = tmp_path / "audio_synced" / "synced_000.wav"
    synced.write_bytes(b"RIFF")

    a = MediaInfo(
        Path("/v/A.mp4"), 10.0, Fraction(30000, 1001), 1920, 1080, "h264", "aac", 2, 48000
    )
    b = MediaInfo(Path("/v/B.mp4"), 10.0, Fraction(30, 1), 3840, 2160, "h264", "aac", 2, 48000)
    plan = SyncPlan(
        strategy_id=1,
        clips=[
            MediaClip(Path("/v/A.mp4"), "video", 0.0, 0.0, 10.0, 1),
            MediaClip(Path("/v/B.mp4"), "video", 12.0, 0.0, 10.0, 2),
            MediaClip(synced, "audio", 0.0, 0.0, 10.0, -1),
        ],
        total_duration=22.0,
    )
    out = tmp_path / "sync_output.fcpxml"
    generate_fcpxml(plan, [a, b], out, audio_sample_rate=48000)
    root = ET.parse(out).getroot()

    frame_durs = {f.get("frameDuration") for f in root.findall(".//format")}
    assert "1001/30000s" in frame_durs and "1/30s" in frame_durs  # both native rates

    # co-located synced audio -> relative; source videos -> absolute file://
    srcs = {a.get("name"): a.find("media-rep").get("src") for a in root.findall(".//asset")}
    assert srcs["synced_000"] == "audio_synced/synced_000.wav"
    assert srcs["A"].startswith("file://")


def _vinfo(path: str, dur: float) -> MediaInfo:
    return MediaInfo(
        path=Path(path),
        duration=dur,
        fps=Fraction(30000, 1001),
        width=1920,
        height=1080,
        video_codec="h264",
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
    )


def test_display_names_and_roles(tmp_path: Path) -> None:
    clips = [
        MediaClip(
            path=Path("/v/DJI_0830.MOV"),
            kind="video",
            offset=0.0,
            in_point=0.0,
            duration=10.0,
            lane=1,
            role="Video",
        ),
        MediaClip(
            path=Path("/a/synced_001.wav"),
            kind="audio",
            offset=0.0,
            in_point=0.0,
            duration=10.0,
            lane=-1,
            display_name="DJI_0830_voice",
            role="Dialogue",
        ),
        MediaClip(
            path=Path("/a/blah_(Instrumental)_melband.wav"),
            kind="audio",
            offset=0.0,
            in_point=0.0,
            duration=10.0,
            lane=-2,
            display_name="DJI_0830_ambience",
            role="Effects",
        ),
    ]
    plan = SyncPlan(strategy_id=3, clips=clips, total_duration=10.0)
    out = tmp_path / "roles.fcpxml"
    generate_fcpxml(plan, [_vinfo("/v/DJI_0830.MOV", 10.0)], out, audio_sample_rate=48000)
    root = ET.parse(out).getroot()

    by_name = {c.get("name"): c for c in root.findall(".//asset-clip")}
    assert set(by_name) == {"DJI_0830", "DJI_0830_voice", "DJI_0830_ambience"}
    # roles land on the right attribute (videoRole vs audioRole)
    assert by_name["DJI_0830"].get("videoRole") == "Video"
    assert by_name["DJI_0830_voice"].get("audioRole") == "Dialogue"
    assert by_name["DJI_0830_ambience"].get("audioRole") == "Effects"
    # friendly names also propagate to the <asset> entries
    asset_names = {a.get("name") for a in root.findall(".//asset")}
    assert "DJI_0830_voice" in asset_names and "DJI_0830_ambience" in asset_names


def test_relative_media_src_is_percent_encoded(tmp_path: Path) -> None:
    # A rendered audio file living next to the FCPXML (relative src branch) must
    # be percent-encoded exactly like the absolute file:// branch — an
    # un-encoded space/parenthesis in a relative src is not a well-formed URI
    # reference. See PROJECT_ANALYSIS.md §3.2.
    audio_dir = tmp_path / "audio_synced"
    audio_dir.mkdir()
    weird_name = "My Clip (2)_voice.wav"
    (audio_dir / weird_name).write_bytes(b"")

    clips = [
        MediaClip(
            path=Path("/v/a.mov"),
            kind="video",
            offset=0.0,
            in_point=0.0,
            duration=5.0,
            lane=1,
        ),
        MediaClip(
            path=audio_dir / weird_name,
            kind="audio",
            offset=0.0,
            in_point=0.0,
            duration=5.0,
            lane=-1,
        ),
    ]
    plan = SyncPlan(strategy_id=1, clips=clips, total_duration=5.0)
    out = tmp_path / "encoded.fcpxml"
    generate_fcpxml(plan, [_vinfo("/v/a.mov", 5.0)], out, audio_sample_rate=48000)

    media_reps = ET.parse(out).getroot().findall(".//media-rep")
    srcs = [m.get("src") for m in media_reps]
    audio_src = next(s for s in srcs if s and "voice" in s)
    assert " " not in audio_src and "(" not in audio_src
    assert audio_src == "audio_synced/My%20Clip%20%282%29_voice.wav"


def test_no_role_omits_attribute(tmp_path: Path) -> None:
    clips = [
        MediaClip(
            path=Path("/v/a.mov"),
            kind="video",
            offset=0.0,
            in_point=0.0,
            duration=5.0,
            lane=1,
        )
    ]
    plan = SyncPlan(strategy_id=1, clips=clips, total_duration=5.0)
    out = tmp_path / "norole.fcpxml"
    generate_fcpxml(plan, [_vinfo("/v/a.mov", 5.0)], out)
    clip = ET.parse(out).getroot().find(".//asset-clip")
    assert clip is not None
    assert clip.get("videoRole") is None and clip.get("audioRole") is None
    assert clip.get("name") == "a"  # falls back to stem


# --- retake groups -> <audition> --------------------------------------------


def _retake_plan(tmp_path: Path, groups: list) -> tuple[SyncPlan, list[MediaInfo]]:
    video_info = _vinfo("/v/clip.mov", 30.0)
    clips = [
        MediaClip(
            path=Path("/v/clip.mov"), kind="video", offset=0.0, in_point=0.0, duration=30.0, lane=1
        ),
        MediaClip(
            path=Path("/audio/clip_voice.wav"),
            kind="audio",
            offset=0.0,
            in_point=0.0,
            duration=30.0,
            lane=-1,
            retake_groups=groups,
        ),
    ]
    return SyncPlan(strategy_id=3, clips=clips, total_duration=30.0), [video_info]


def test_no_retakes_emits_plain_asset_clip_not_audition(tmp_path: Path) -> None:
    plan, infos = _retake_plan(tmp_path, None)
    out = tmp_path / "no_retake.fcpxml"
    generate_fcpxml(plan, infos, out)
    root = ET.parse(out).getroot()
    assert root.find(".//audition") is None
    connected = root.find(".//asset-clip").findall("asset-clip")
    assert len(connected) == 1


def test_single_retake_group_wraps_in_audition(tmp_path: Path) -> None:
    from whispersync.models import RetakeGroup, Take

    group = RetakeGroup(
        takes=[
            Take(start=10.0, end=12.0, text="кто ещё учится в школе а не выпустился"),
            Take(
                start=12.0,
                end=15.0,
                text="кто ещё учится в школе а не выпустился ну и так далее",
            ),
        ],
        keeper_index=-1,
    )
    plan, infos = _retake_plan(tmp_path, [group])
    out = tmp_path / "retake.fcpxml"
    result = generate_fcpxml(plan, infos, out)
    assert validate_fcpxml(result)

    root = ET.parse(out).getroot()
    video_clip = root.find(".//asset-clip")
    stories = list(video_clip)
    # plain [0,10) clip, then <audition>, then plain [15,30) clip
    tags = [el.tag for el in stories]
    assert tags == ["asset-clip", "audition", "asset-clip"]

    lead, audition, tail = stories
    assert float(lead.get("duration").split("/")[0]) > 0  # non-zero lead-in

    takes = audition.findall("asset-clip")
    assert len(takes) == 2
    # keeper (last take, per keeper_index=-1) is FIRST/active in the audition
    assert "Keep" in takes[0].get("name")
    assert "так далее" not in takes[0].get("name")  # name is a label, not full text
    assert takes[1].get("name") != takes[0].get("name")
    # keeper's own duration is 3s (12..15), the discarded take's is 2s (10..12)
    assert takes[0].get("duration") is not None
    assert takes[1].get("duration") is not None

    # audition children carry no offset/lane of their own (they're alternates,
    # not independently positioned) — only the audition wrapper is positioned.
    assert takes[0].get("offset") is None
    assert audition.get("offset") is not None
    assert audition.get("lane") == "-1"


def test_multiple_retake_groups_produce_multiple_auditions(tmp_path: Path) -> None:
    from whispersync.models import RetakeGroup, Take

    groups = [
        RetakeGroup(
            takes=[Take(1.0, 2.0, "a a a a"), Take(2.0, 3.0, "a a a a a")], keeper_index=-1
        ),
        RetakeGroup(
            takes=[Take(20.0, 21.0, "b b b b"), Take(21.0, 22.0, "b b b b b")], keeper_index=-1
        ),
    ]
    plan, infos = _retake_plan(tmp_path, groups)
    out = tmp_path / "two_retakes.fcpxml"
    generate_fcpxml(plan, infos, out)
    root = ET.parse(out).getroot()
    auditions = root.findall(".//audition")
    assert len(auditions) == 2


def test_retake_group_at_clip_start_has_no_leading_plain_clip(tmp_path: Path) -> None:
    from whispersync.models import RetakeGroup, Take

    group = RetakeGroup(
        takes=[Take(0.0, 1.0, "a a a a"), Take(1.0, 2.5, "a a a a a")], keeper_index=-1
    )
    plan, infos = _retake_plan(tmp_path, [group])
    out = tmp_path / "retake_at_start.fcpxml"
    generate_fcpxml(plan, infos, out)
    root = ET.parse(out).getroot()
    stories = list(root.find(".//asset-clip"))
    tags = [el.tag for el in stories]
    assert tags == ["audition", "asset-clip"]  # no zero-length lead-in clip


def test_audition_is_valid_per_dtd_anchor_item(tmp_path: Path) -> None:
    # audition must be nested under the video asset-clip (a valid anchor_item
    # position), each of its own children must be an asset-clip referencing a
    # declared asset, and none of them may carry their own offset/lane.
    from whispersync.models import RetakeGroup, Take

    group = RetakeGroup(
        takes=[Take(5.0, 6.0, "x x x x"), Take(6.0, 7.5, "x x x x x")], keeper_index=-1
    )
    plan, infos = _retake_plan(tmp_path, [group])
    out = tmp_path / "dtd_check.fcpxml"
    generate_fcpxml(plan, infos, out)
    root = ET.parse(out).getroot()
    asset_ids = {a.get("id") for a in root.findall(".//asset")}
    audition = root.find(".//audition")
    assert audition is not None
    for child in audition:
        assert child.tag == "asset-clip"
        assert child.get("ref") in asset_ids
        assert child.get("offset") is None
        assert child.get("lane") is None
        assert child.get("duration") is not None
