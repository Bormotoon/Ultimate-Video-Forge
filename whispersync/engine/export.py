"""FCPXML generator for Final Cut Pro / DaVinci Resolve."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote, unquote

from whispersync.engine.media import MediaInfo, path_to_file_uri, probe
from whispersync.models import MediaClip, SyncPlan

logger = logging.getLogger(__name__)


def to_rational(seconds: float, timebase: int) -> str:
    ticks = round(seconds * timebase)
    return f"{ticks}/{timebase}s"


def _media_src(path: Path, base_dir: Path) -> str:
    """media-rep ``src`` for a clip's file.

    Media that lives under the FCPXML's own folder (the rendered synced audio) is
    referenced by a path relative to the document, so the project stays portable
    and Final Cut resolves it right next to the XML. Anything outside (the source
    videos) keeps an absolute ``file://`` URL.
    """
    try:
        rel = path.resolve().relative_to(base_dir)
        # Percent-encode each path component (a clip name with spaces or
        # parentheses — e.g. "My Clip (2)_voice.wav" — must be encoded here
        # exactly as the absolute-URL branch already is; an un-encoded relative
        # src is not a well-formed URI reference and some FCPXML consumers
        # reject it). "/" stays a separator, never encoded within a component.
        return "/".join(quote(part, safe="") for part in rel.parts)
    except ValueError:
        return path_to_file_uri(path)


def fps_to_frame_duration(fps: Fraction) -> str:
    return f"{fps.denominator}/{fps.numerator}s"


def _frame_rational(seconds: float, fps: Fraction, mode: str = "round") -> str:
    """Express ``seconds`` on the sequence timebase, snapped to a whole frame.

    Final Cut requires spine offsets/durations to land on an edit-frame boundary
    (a multiple of the frame duration), otherwise it warns and re-quantises. We
    round offsets, floor clip durations (never claim more media than exists) and
    ceil the sequence/gap duration (so it covers every clip).
    """
    frames_f = seconds * float(fps)
    if mode == "floor":
        frames = int(frames_f)
    elif mode == "ceil":
        frames = -int(-frames_f // 1)
    else:
        frames = round(frames_f)
    ticks = frames * fps.denominator
    return f"{ticks}/{fps.numerator}s"


def generate_fcpxml(
    plan: SyncPlan,
    video_infos: list[MediaInfo],
    output_path: Path,
    fcpxml_version: str = "1.9",
    project_name: str = "WhisperSync",
    audio_sample_rate: int | None = None,
) -> Path:
    ref_video = video_infos[0] if video_infos else None
    # Sequence (timeline) rate — every spine position is snapped to this grid.
    seq_fps: Fraction = (ref_video.fps or Fraction(25, 1)) if ref_video else Fraction(25, 1)
    seq_w: int = (ref_video.width or 1920) if ref_video else 1920
    seq_h: int = (ref_video.height or 1080) if ref_video else 1080
    # Audio timebase: caller override (e.g. recorder rate) wins, else camera's.
    if audio_sample_rate:
        sample_rate: int = audio_sample_rate
    else:
        sample_rate = (ref_video.audio_sample_rate or 48000) if ref_video else 48000

    info_by_path = {str(i.path.resolve()): i for i in video_infos}

    root = ET.Element("fcpxml", version=fcpxml_version)
    resources = ET.SubElement(root, "resources")

    # One resource-id counter shared by formats and assets.
    _rid = [1]

    def next_rid() -> str:
        rid = f"r{_rid[0]}"
        _rid[0] += 1
        return rid

    # A distinct <format> per (fps, width, height) so cameras at different rates
    # (e.g. 29.97 vs 30) are each declared honestly and Final Cut conforms them.
    formats: dict[tuple[int, int, int, int], str] = {}

    def _format_for(f: Fraction, w: int, h: int) -> str:
        key = (f.numerator, f.denominator, w or 1920, h or 1080)
        fid = formats.get(key)
        if fid is None:
            fid = next_rid()
            ET.SubElement(
                resources,
                "format",
                id=fid,
                frameDuration=fps_to_frame_duration(f),
                width=str(w or 1920),
                height=str(h or 1080),
                colorSpace="1-1-1 (Rec. 709)",
            )
            formats[key] = fid
        return fid

    seq_fmt = _format_for(seq_fps, seq_w, seq_h)  # r1

    asset_map: dict[str, str] = {}
    base_dir = output_path.parent.resolve()

    seen_paths: set[str] = set()
    for clip in plan.clips:
        path_str = str(clip.path.resolve())
        if path_str in seen_paths:
            continue
        seen_paths.add(path_str)

        asset_id = next_rid()
        asset_map[path_str] = asset_id
        file_uri = _media_src(clip.path, base_dir)
        # NOTE: in FCPXML 1.9+ the file reference lives on the <media-rep> child,
        # NOT as a `src` attribute on <asset>. Durations use each asset's own grid:
        # the video's native fps, or the audio sample rate.
        asset_name = clip.display_name or clip.path.stem
        if clip.kind == "video":
            info = info_by_path.get(path_str)
            cfps = info.fps if info and info.fps else seq_fps
            cw = info.width if info and info.width else seq_w
            ch = info.height if info and info.height else seq_h
            # hasAudio must describe the FILE, not a wish: declaring audio on a
            # silent clip makes an NLE offer channels that do not exist, and
            # declaring none on a clip that has some hides the scratch track an
            # editor may still want. It is `srcEnable` on the timeline clip —
            # not this flag, and not the role — that decides whether the
            # camera's own microphone is heard (see _spine_clip).
            has_audio = "1" if (info is None or info.audio_codec is not None) else "0"
            asset_attrs = {
                "id": asset_id,
                "name": asset_name,
                "start": "0s",
                "duration": _frame_rational(clip.duration + clip.in_point, cfps, "round"),
                "hasVideo": "1",
                "hasAudio": has_audio,
                "format": _format_for(cfps, cw, ch),
            }
        else:
            # Report the rendered file's real channel count/rate (it now preserves
            # the recorder's native channels — see PROJECT_ANALYSIS.md §2.0) rather
            # than a hard-coded mono assumption; falls back to the sequence default
            # if the file can't be probed (e.g. in unit tests with fake paths).
            audio_channels = 1
            audio_rate = sample_rate
            try:
                audio_info = probe(clip.path)
                audio_channels = audio_info.audio_channels or 1
                audio_rate = audio_info.audio_sample_rate or sample_rate
            except (RuntimeError, OSError):
                pass
            asset_attrs = {
                "id": asset_id,
                "name": asset_name,
                "start": "0s",
                "duration": to_rational(clip.duration + clip.in_point, audio_rate),
                "hasVideo": "0",
                "hasAudio": "1",
                "audioSources": "1",
                "audioChannels": str(audio_channels),
                "audioRate": str(audio_rate),
            }

        asset_el = ET.SubElement(resources, "asset", asset_attrs)
        ET.SubElement(asset_el, "media-rep", kind="original-media", src=file_uri)

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="WhisperSync")
    project = ET.SubElement(event, "project", name=project_name)

    seq_dur = _frame_rational(plan.total_duration, seq_fps, "ceil")
    seq = ET.SubElement(
        project,
        "sequence",
        format=seq_fmt,
        tcStart="0s",
        tcFormat="NDF",  # linear timecode (TC == real elapsed); avoids drop-frame ambiguity
        duration=seq_dur,
    )

    spine = ET.SubElement(seq, "spine")

    # LAYOUT
    # ------
    # Cameras that were rolling AT THE SAME TIME must be stacked, not queued.
    # The spine is a single sequential track: everything placed in it plays one
    # after another, so putting every camera there turned two cameras both
    # covering 0-10 s into camera A at 0-10 s and camera B at 10-20 s. The
    # exported timeline claimed twenty seconds of footage that never existed,
    # while the audio kept its true positions — so nothing lined up with
    # anything.
    #
    # So: ONE camera forms the primary storyline (the lowest lane, i.e. the
    # first camera folder), and every other camera becomes a CONNECTED clip on
    # its own positive lane, attached to whichever spine clip covers its start.
    # Audio clips are connected on their own negative lanes, as before.
    video_clips = sorted((c for c in plan.clips if c.kind == "video"), key=lambda c: c.offset)
    audio_clips = sorted((c for c in plan.clips if c.kind != "video"), key=lambda c: c.offset)

    primary_lane = min((c.lane for c in video_clips), default=1)
    spine_videos = [c for c in video_clips if c.lane == primary_lane]
    connected_videos = [c for c in video_clips if c.lane != primary_lane]

    # Frame duration (seconds) of the sequence grid, used to align spine offsets.
    frame_s = float(seq_fps.denominator) / float(seq_fps.numerator)

    def _clip_name(clip: MediaClip) -> str:
        return clip.display_name or clip.path.stem

    def _set_role(el: ET.Element, clip: MediaClip) -> None:
        # FCPX colours/groups clips by role: videoRole on video, audioRole on audio.
        if clip.role:
            el.set("videoRole" if clip.kind == "video" else "audioRole", clip.role)

    def _set_source_enable(el: ET.Element, clip: MediaClip) -> None:
        """Silence a camera clip's own microphone when its dialogue was replaced.

        `videoRole` does NOT disable audio — it only labels it. A camera clip
        left at the default `srcEnable="all"` therefore played its built-in mic
        underneath the clean synced voice: two copies of the same speech tens
        of milliseconds apart, comb-filtering into the doubled/echoed voice the
        ambience feature exists to prevent. `srcEnable="video"` is the actual
        switch. Clips with no replacement audio keep their sound, so unresolved
        footage is still usable.
        """
        if clip.kind == "video" and clip.source_audio_enabled is False:
            el.set("srcEnable", "video")

    def _emit_retake_markers(el: ET.Element, clip: MediaClip) -> None:
        """Mark each detected retake on the clip instead of restructuring it.

        Retakes used to be exported as an ``<audition>`` holding the alternate
        takes' AUDIO ranges. That desynchronised the result: the audition began
        at the group's start but played the keeper take's audio, which comes
        from later in the clip, while the picture did not switch at all — a
        3 s advance on the voice plus a hole in the clean track. Markers convey
        exactly the same finding (here are N attempts at this line, this is the
        one to keep) with zero risk to the A/V relationship; a real audition has
        to switch the linked video range too, which is a separate feature with
        its own timing model.
        """
        for gi, g in enumerate(sorted(clip.retake_groups or [], key=lambda g: g.span[0])):
            if not g.takes:
                continue
            keeper_pos = (len(g.takes) + g.keeper_index) % len(g.takes)
            for take_idx, take in enumerate(g.takes):
                t_start = max(0.0, min(take.start, clip.duration))
                t_end = max(t_start, min(take.end, clip.duration))
                label = "keep" if take_idx == keeper_pos else f"take {take_idx + 1}"
                ET.SubElement(
                    el,
                    "marker",
                    start=_frame_rational(clip.in_point + t_start, seq_fps, "round"),
                    duration=_frame_rational(max(t_end - t_start, frame_s), seq_fps, "ceil"),
                    value=f"Retake {gi + 1} — {label} ({len(g.takes)} attempts)",
                )

    def _emit_audio_story(parent_el: ET.Element, clip: MediaClip, base_offset: float) -> None:
        """Emit ``clip``'s local ``[0, clip.duration)`` span as one connected
        story element under ``parent_el``. ``base_offset`` is the parent-clock
        position corresponding to this clip's local time 0 (i.e. where
        ``clip.in_point`` starts playing). Retakes ride along as markers.
        """
        ref = asset_map.get(str(clip.path.resolve()), "r2")
        el = ET.SubElement(
            parent_el,
            "asset-clip",
            ref=ref,
            lane=str(clip.lane),
            name=_clip_name(clip),
            offset=_frame_rational(base_offset, seq_fps, "round"),
            start=_frame_rational(clip.in_point, seq_fps, "round"),
            duration=_frame_rational(clip.duration, seq_fps, "floor"),
        )
        _set_role(el, clip)
        _emit_retake_markers(el, clip)

    def _spine_clip(clip: MediaClip, offset_str: str) -> ET.Element:
        el = ET.SubElement(
            spine,
            "asset-clip",
            ref=asset_map.get(str(clip.path.resolve()), "r2"),
            name=_clip_name(clip),
            offset=offset_str,
            start=_frame_rational(clip.in_point, seq_fps, "round"),
            duration=_frame_rational(clip.duration, seq_fps, "floor"),
        )
        _set_role(el, clip)
        _set_source_enable(el, clip)
        return el

    def _connected_video(parent_el: ET.Element, clip: MediaClip, base_offset: float) -> None:
        el = ET.SubElement(
            parent_el,
            "asset-clip",
            ref=asset_map.get(str(clip.path.resolve()), "r2"),
            lane=str(clip.lane),
            name=_clip_name(clip),
            offset=_frame_rational(base_offset, seq_fps, "round"),
            start=_frame_rational(clip.in_point, seq_fps, "round"),
            duration=_frame_rational(clip.duration, seq_fps, "floor"),
        )
        _set_role(el, clip)
        _set_source_enable(el, clip)

    # Lay the primary camera's clips end-to-end with gaps for the holes. The
    # spine's own clock ("offset") is contiguous; each element's offset is where
    # it begins on it. Track (timeline_start, timeline_end, parent_in_point,
    # element) so connected clips can be positioned on the PARENT's local clock
    # (which starts at its in_point).
    spine_elems: list[tuple[float, float, float, ET.Element]] = []
    cursor = 0.0
    for clip in spine_videos:
        if clip.offset > cursor + frame_s / 2:
            gap_dur = clip.offset - cursor
            ET.SubElement(
                spine,
                "gap",
                name="Gap",
                offset=_frame_rational(cursor, seq_fps, "round"),
                start="0s",
                duration=_frame_rational(gap_dur, seq_fps, "round"),
            )
            cursor = clip.offset
        el = _spine_clip(clip, _frame_rational(cursor, seq_fps, "round"))
        end = cursor + clip.duration
        spine_elems.append((cursor, end, clip.in_point, el))
        cursor = end

    if not spine_elems:
        # No primary-camera video (audio-only, or every camera on a higher
        # lane) — a single gap holds everything so the document stays valid.
        # The gap's own start=0s, so a clip's local time 0 lands at
        # parent-clock position clip.offset directly.
        gap = ET.SubElement(spine, "gap", name="Gap", offset="0s", start="0s", duration=seq_dur)
        for clip in connected_videos:
            _connected_video(gap, clip, clip.offset)
        for clip in audio_clips:
            _emit_audio_story(gap, clip, clip.offset)
    else:

        def _attach_point(offset: float) -> tuple[float, float, ET.Element]:
            for start_s, end_s, in_pt, el in spine_elems:
                if start_s - frame_s <= offset < end_s:
                    return start_s, in_pt, el
            # Before the first / after the last spine clip — clamp to the nearest.
            s0, _e0, ip0, el0 = spine_elems[0]
            return s0, ip0, el0

        for clip in connected_videos + audio_clips:
            parent_tl_start, parent_in_pt, parent_el = _attach_point(clip.offset)
            if clip.offset < parent_tl_start - frame_s:
                # As in timestretch.mix_clips_on_timeline: the plan's origin is
                # normalised upstream, so a clip landing before its parent is a
                # caller bug rather than something to absorb quietly — that
                # silent absorption is how a negative calibration was reported
                # as applied while having no effect.
                logger.warning(
                    "Clip %s starts %.3fs before its parent clip; clamping to the "
                    "parent's start.",
                    _clip_name(clip),
                    parent_tl_start - clip.offset,
                )
            rel = max(0.0, clip.offset - parent_tl_start)
            # A connected clip's `offset` is on the PARENT's OWN clock, which
            # starts at the parent's `start` value (its in_point), NOT at the
            # parent's spine position.
            base = parent_in_pt + rel
            if clip.kind == "video":
                _connected_video(parent_el, clip, base)
            else:
                _emit_audio_story(parent_el, clip, base)

    tree = ET.ElementTree(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ET.indent(tree, space="  ")

    with open(output_path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(b"<!DOCTYPE fcpxml>\n")
        tree.write(f, xml_declaration=False, encoding="UTF-8")

    logger.info("FCPXML written to %s", output_path)
    return output_path


_RATIONAL_RE = re.compile(r"^-?\d+(?:/\d+)?s$")


def parse_rational(value: str) -> float | None:
    """Seconds from an FCPXML time value (``"1001/30000s"``, ``"5s"``), or None."""
    if not value or not _RATIONAL_RE.match(value.strip()):
        return None
    body = value.strip()[:-1]
    try:
        if "/" in body:
            num, den = body.split("/")
            if int(den) == 0:
                return None
            return int(num) / int(den)
        return float(body)
    except ValueError:
        return None


def _element_time_problems(el: ET.Element, where: str) -> list[str]:
    problems: list[str] = []
    for attr in ("offset", "start", "duration", "tcStart"):
        raw = el.get(attr)
        if raw is None:
            continue
        seconds = parse_rational(raw)
        if seconds is None:
            problems.append(f"{where}: {attr}={raw!r} is not a valid FCPXML time")
        elif attr == "duration" and seconds <= 0:
            problems.append(f"{where}: duration {raw!r} is not positive")
    return problems


def check_fcpxml(path: Path, *, check_media: bool = True) -> list[str]:
    """Everything structurally wrong with an FCPXML document, as messages.

    The previous check accepted ``<fcpxml><spine><asset-clip ref="missing"
    duration="nonsense"/></spine></fcpxml>`` — it looked for a root tag, a
    spine and at least one asset-clip, which a document can have while being
    entirely unusable. Since the export is the deliverable, the checks now
    cover what actually makes Final Cut reject or mis-import a project:

    * every ``ref`` resolves to a declared resource id (no dangling references);
    * every declared asset carries a ``media-rep`` with a ``src``;
    * every time attribute parses as a rational and every duration is positive;
    * the sequence is long enough to contain every element placed in the spine;
    * referenced media exists on disk (``check_media``; relative ``src`` values
      are resolved against the document, as Final Cut does).

    This is a strict structural + reference check, not DTD validation: the DTD
    is version-specific and not redistributable with this project, and fetching
    one at validation time would mean resolving external entities from the
    network — which is exactly what an XML parser must not do. Returns an empty
    list for a sound document.
    """
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        return [f"XML parse error: {e}"]
    root = tree.getroot()
    problems: list[str] = []

    if root.tag != "fcpxml":
        return [f"root tag is {root.tag!r}, expected 'fcpxml'"]

    resources = root.find("resources")
    declared: set[str] = set()
    if resources is None:
        problems.append("no <resources> section")
    else:
        for res in resources:
            rid = res.get("id")
            if not rid:
                problems.append(f"<{res.tag}> in <resources> has no id")
                continue
            if rid in declared:
                problems.append(f"duplicate resource id {rid!r}")
            declared.add(rid)
            if res.tag == "asset":
                rep = res.find("media-rep")
                src = rep.get("src") if rep is not None else None
                if not src:
                    problems.append(f"asset {rid!r} has no <media-rep src=...>")
                elif check_media:
                    resolved = (
                        Path(unquote(src[len("file://") :]))
                        if src.startswith("file://")
                        else path.parent / unquote(src)
                    )
                    if not resolved.exists():
                        problems.append(f"asset {rid!r} references missing media {src!r}")
            problems.extend(_element_time_problems(res, f"resource {rid!r}"))

    spine = root.find(".//spine")
    if spine is None:
        return [*problems, "no <spine> found"]

    clips = spine.findall(".//asset-clip")
    if not clips:
        problems.append("no <asset-clip> found in spine")

    for el in spine.iter():
        if el is spine:
            continue
        name = el.get("name") or el.get("ref") or el.tag
        problems.extend(_element_time_problems(el, f"<{el.tag}> {name!r}"))
        ref = el.get("ref")
        if ref is not None and ref not in declared:
            problems.append(f"<{el.tag}> {name!r} references undeclared resource {ref!r}")

    sequence = root.find(".//sequence")
    if sequence is not None:
        seq_dur = parse_rational(sequence.get("duration", ""))
        if seq_dur is not None:
            spine_end = 0.0
            for el in spine:
                off = parse_rational(el.get("offset", "0s")) or 0.0
                dur = parse_rational(el.get("duration", "0s")) or 0.0
                spine_end = max(spine_end, off + dur)
            if spine_end > seq_dur + 1e-6:
                problems.append(
                    f"sequence duration {seq_dur:.3f}s is shorter than its spine "
                    f"content ({spine_end:.3f}s) — clips past the end are dropped on import"
                )
    return problems


def fcpxml_intervals(path: Path) -> dict[str, tuple[float, float]]:
    """``{clip name: (timeline_start, timeline_end)}`` recovered from a document.

    Reconstructs each element's ABSOLUTE timeline position the way an NLE does
    — spine elements on the spine's own contiguous clock, connected clips on
    their parent's clock (which starts at the parent's ``start``) — so a test
    can assert that what the file says matches what the plan intended. Checking
    that the XML parses says nothing about whether two simultaneous cameras
    ended up simultaneous.
    """
    out: dict[str, tuple[float, float]] = {}
    tree = ET.parse(path)
    spine = tree.getroot().find(".//spine")
    if spine is None:
        return out

    def walk(el: ET.Element, parent_tl_start: float, parent_in_point: float) -> None:
        for child in el:
            if child.tag not in ("asset-clip", "gap", "audition", "clip"):
                continue
            off = parse_rational(child.get("offset", "0s")) or 0.0
            dur = parse_rational(child.get("duration", "0s")) or 0.0
            start = parse_rational(child.get("start", "0s")) or 0.0
            # A connected clip's offset is on the parent's own clock.
            tl_start = parent_tl_start + (off - parent_in_point)
            name = child.get("name")
            if name and child.tag == "asset-clip":
                out[name] = (tl_start, tl_start + dur)
            walk(child, tl_start, start)

    cursor_children = list(spine)
    for child in cursor_children:
        off = parse_rational(child.get("offset", "0s")) or 0.0
        dur = parse_rational(child.get("duration", "0s")) or 0.0
        start = parse_rational(child.get("start", "0s")) or 0.0
        name = child.get("name")
        if name and child.tag == "asset-clip":
            out[name] = (off, off + dur)
        walk(child, off, start)
    return out


def validate_fcpxml(path: Path, *, check_media: bool = False) -> bool:
    """True when ``check_fcpxml`` finds nothing wrong; logs each problem.

    ``check_media`` defaults to False here so the pipeline's own safety net
    stays a pure document check (media existence is verified where the files
    are actually written).
    """
    problems = check_fcpxml(path, check_media=check_media)
    for problem in problems:
        logger.error("FCPXML: %s", problem)
    if problems:
        return False
    logger.info("FCPXML structural check passed: %s", path)
    return True
