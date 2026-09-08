"""Tests for FCPXML generation and validation."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from whispersync.engine.export import (
    check_fcpxml,
    fcpxml_intervals,
    fps_to_frame_duration,
    generate_fcpxml,
    parse_rational,
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


def test_retakes_are_exported_as_markers(tmp_path: Path) -> None:
    """Retakes must be reported WITHOUT restructuring the timeline.

    They used to become an ``<audition>`` over the alternate takes' audio: the
    audition began at the group's start but played the keeper take's audio,
    which comes from later in the clip, and the picture did not switch at all.
    Two takes at [2,4] and [5,8] put the voice 3 s ahead of the picture and left
    a hole in the clean track. A marker says the same thing and moves nothing.
    """
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
    assert root.find(".//audition") is None

    video_clip = root.find(".//asset-clip")
    connected = video_clip.findall("asset-clip")
    # Exactly ONE audio clip, spanning the whole thing — not a chopped-up
    # sequence of plain clips and auditions.
    assert len(connected) == 1
    audio_el = connected[0]

    markers = audio_el.findall("marker")
    assert len(markers) == 2
    labels = [m.get("value") for m in markers]
    assert any("keep" in v for v in labels)
    assert all("Retake 1" in v for v in labels)
    for m in markers:
        assert m.get("start") is not None
        assert m.get("duration") is not None


def test_retake_markers_do_not_move_the_audio(tmp_path: Path) -> None:
    """The audition regression, stated as an interval check: an audio clip with
    retakes must occupy exactly the same timeline span as one without."""
    from whispersync.models import RetakeGroup, Take

    plain_plan, infos = _retake_plan(tmp_path, None)
    plain_out = tmp_path / "plain.fcpxml"
    generate_fcpxml(plain_plan, infos, plain_out)

    groups = [
        RetakeGroup(takes=[Take(2.0, 4.0, "a a a a"), Take(5.0, 8.0, "a a a a")], keeper_index=-1)
    ]
    retake_plan, infos2 = _retake_plan(tmp_path, groups)
    retake_out = tmp_path / "retake_intervals.fcpxml"
    generate_fcpxml(retake_plan, infos2, retake_out)

    plain_iv = fcpxml_intervals(plain_out)
    retake_iv = fcpxml_intervals(retake_out)
    assert plain_iv["clip_voice"] == retake_iv["clip_voice"]


def test_multiple_retake_groups_produce_markers_for_each(tmp_path: Path) -> None:
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
    markers = root.findall(".//marker")
    assert len(markers) == 4  # two attempts in each of two groups
    assert {v.get("value").split(" — ")[0] for v in markers} == {"Retake 1", "Retake 2"}


# --- multicam, source audio and document validity --------------------------


def test_simultaneous_cameras_stay_simultaneous(tmp_path: Path) -> None:
    """Two cameras rolling at once must be STACKED, not queued.

    The spine is one sequential track, so putting every camera in it turned two
    cameras both covering [0,10] into A at [0,10] and B at [10,20] — twenty
    seconds of footage from ten seconds of shoot, with the audio still at its
    true positions and the sequence still claiming 10 s. Camera B belongs on
    its own lane as a connected clip.
    """
    infos = [_vinfo("/v/A/a.mov", 10.0), _vinfo("/v/B/b.mov", 10.0)]
    clips = [
        MediaClip(
            path=Path("/v/A/a.mov"),
            kind="video",
            offset=0.0,
            in_point=0.0,
            duration=10.0,
            lane=1,
            display_name="camA",
        ),
        MediaClip(
            path=Path("/v/B/b.mov"),
            kind="video",
            offset=0.0,
            in_point=0.0,
            duration=10.0,
            lane=2,
            display_name="camB",
        ),
    ]
    plan = SyncPlan(strategy_id=3, clips=clips, total_duration=10.0)
    out = tmp_path / "multicam.fcpxml"
    generate_fcpxml(plan, infos, out)

    intervals = fcpxml_intervals(out)
    a_start, a_end = intervals["camA"]
    b_start, b_end = intervals["camB"]
    assert abs(a_start - b_start) < 0.05, f"cameras not simultaneous: {a_start} vs {b_start}"
    assert abs(a_end - b_end) < 0.05

    root = ET.parse(out).getroot()
    spine = root.find(".//spine")
    # Only ONE camera sits directly in the spine; the other is connected.
    assert len(spine.findall("asset-clip")) == 1
    connected = spine.find("asset-clip").findall("asset-clip")
    assert [c.get("name") for c in connected] == ["camB"]
    assert connected[0].get("lane") == "2"


def test_multicam_roundtrip_matches_the_plan(tmp_path: Path) -> None:
    """Every clip's absolute interval in the document must match the plan."""
    infos = [_vinfo("/v/A/a.mov", 10.0), _vinfo("/v/B/b.mov", 6.0)]
    clips = [
        MediaClip(
            path=Path("/v/A/a.mov"),
            kind="video",
            offset=0.0,
            in_point=0.0,
            duration=10.0,
            lane=1,
            display_name="camA",
        ),
        MediaClip(
            path=Path("/v/B/b.mov"),
            kind="video",
            offset=3.0,
            in_point=0.0,
            duration=6.0,
            lane=2,
            display_name="camB",
        ),
        MediaClip(
            path=Path("/a/voice.wav"),
            kind="audio",
            offset=1.0,
            in_point=0.0,
            duration=8.0,
            lane=-1,
            display_name="voice",
            role="Dialogue",
        ),
    ]
    plan = SyncPlan(strategy_id=3, clips=clips, total_duration=10.0)
    out = tmp_path / "roundtrip.fcpxml"
    generate_fcpxml(plan, infos, out)

    intervals = fcpxml_intervals(out)
    for clip in clips:
        name = clip.display_name
        start, end = intervals[name]
        assert abs(start - clip.offset) < 0.05, f"{name}: {start} != {clip.offset}"
        assert abs((end - start) - clip.duration) < 0.05, f"{name}: length {end - start}"

    # The sequence must cover every clip, not just the primary storyline.
    assert not check_fcpxml(out, check_media=False)


def test_replaced_camera_audio_is_disabled(tmp_path: Path) -> None:
    """A camera clip whose dialogue was replaced must be video-only.

    `videoRole` does not disable audio; without `srcEnable="video"` the camera's
    own microphone plays underneath the clean synced voice — two copies of the
    same speech tens of milliseconds apart.
    """
    infos = [_vinfo("/v/clip.mov", 10.0)]
    clips = [
        MediaClip(
            path=Path("/v/clip.mov"),
            kind="video",
            offset=0.0,
            in_point=0.0,
            duration=10.0,
            lane=1,
            role="Video",
            source_audio_enabled=False,
        ),
        MediaClip(
            path=Path("/a/clip_voice.wav"),
            kind="audio",
            offset=0.0,
            in_point=0.0,
            duration=10.0,
            lane=-1,
            role="Dialogue",
        ),
    ]
    plan = SyncPlan(strategy_id=3, clips=clips, total_duration=10.0)
    out = tmp_path / "muted.fcpxml"
    generate_fcpxml(plan, infos, out)
    root = ET.parse(out).getroot()
    video_el = root.find(".//spine/asset-clip")
    assert video_el.get("srcEnable") == "video"


def test_unresolved_camera_keeps_its_own_audio(tmp_path: Path) -> None:
    """A clip with no replacement dialogue must stay audible — muting it would
    leave the editor with silent footage and no way back."""
    infos = [_vinfo("/v/clip.mov", 10.0)]
    clips = [
        MediaClip(
            path=Path("/v/clip.mov"),
            kind="video",
            offset=0.0,
            in_point=0.0,
            duration=10.0,
            lane=1,
            source_audio_enabled=True,
        )
    ]
    plan = SyncPlan(strategy_id=3, clips=clips, total_duration=10.0)
    out = tmp_path / "unresolved.fcpxml"
    generate_fcpxml(plan, infos, out)
    root = ET.parse(out).getroot()
    assert root.find(".//spine/asset-clip").get("srcEnable") is None


def test_silent_camera_asset_declares_no_audio(tmp_path: Path) -> None:
    info = _vinfo("/v/silent.mov", 10.0)
    info.audio_codec = None
    clips = [
        MediaClip(
            path=Path("/v/silent.mov"),
            kind="video",
            offset=0.0,
            in_point=0.0,
            duration=10.0,
            lane=1,
        )
    ]
    plan = SyncPlan(strategy_id=3, clips=clips, total_duration=10.0)
    out = tmp_path / "silent.fcpxml"
    generate_fcpxml(plan, [info], out)
    root = ET.parse(out).getroot()
    asset = root.find(".//asset")
    assert asset.get("hasAudio") == "0"


def test_validation_rejects_dangling_refs_and_bad_times(tmp_path: Path) -> None:
    """The old check passed this document; it is unusable.

    It looked for a root tag, a spine and an asset-clip — all present here —
    and never asked whether the reference resolved or the times parsed.
    """
    bad = tmp_path / "bad.fcpxml"
    bad.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<fcpxml><resources/><spine>"
        '<asset-clip ref="missing" duration="nonsense"/>'
        "</spine></fcpxml>"
    )
    problems = check_fcpxml(bad, check_media=False)
    assert problems
    assert any("missing" in p for p in problems)
    assert any("nonsense" in p for p in problems)
    assert validate_fcpxml(bad) is False


def test_validation_rejects_a_sequence_shorter_than_its_spine(tmp_path: Path) -> None:
    doc = tmp_path / "short.fcpxml"
    doc.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<fcpxml><resources><asset id="r1"><media-rep src="x.wav"/></asset></resources>'
        '<library><event><project><sequence duration="10s"><spine>'
        '<asset-clip ref="r1" offset="0s" duration="20s"/>'
        "</spine></sequence></project></event></library></fcpxml>"
    )
    problems = check_fcpxml(doc, check_media=False)
    assert any("shorter than its spine" in p for p in problems)


def test_parse_rational_handles_ntsc_and_rejects_nonsense() -> None:
    assert abs(parse_rational("1001/30000s") - 1001 / 30000) < 1e-12
    assert parse_rational("5s") == 5.0
    assert parse_rational("nonsense") is None
    assert parse_rational("1/0s") is None
    assert parse_rational("") is None
