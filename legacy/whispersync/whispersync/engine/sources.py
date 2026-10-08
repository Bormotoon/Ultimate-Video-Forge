"""Stable identity for every media source in a run.

A display name (the file stem, or a camera sub-folder name) is *not* an
identifier. Two recorders can both hold ``take.wav``; one camera can hold both
``clip.mov`` and ``clip.mp4``; ``A1`` is a prefix of ``A10``. Everything the
pipeline derives from a name — the extracted PCM master, the rendered voice
WAV, the exported transcript, the ambience stem, the ``--verify`` pairing —
used to collide whenever two sources' stems agreed, silently handing one
source's audio to another source's slot.

``SourceRef`` is the single answer: one object per physical input, carrying

* ``sid`` — a short, unique, filesystem-safe id derived from the source's
  role, group and stem, disambiguated deterministically on collision. Every
  artifact belonging to this source is named from ``sid``, so two sources can
  never write the same path.
* ``stream_index`` — WHICH audio stream of the container was chosen, so
  probing, transcription, master extraction, ambience and verification all
  read the same one (ffmpeg's automatic stream selection does not match
  ffprobe's "first audio stream" — see ``media.probe``).
* ``display_name`` — for the UI and FCPXML only. Never for identity.

The rule this module enforces: *display names are for humans, ``sid`` is for
files.*
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

__all__ = ["SourceRef", "assign_source_ids", "slugify", "unique_name"]

SourceKind = Literal["camera", "recorder"]

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(text: str) -> str:
    """A filesystem-safe token: unsafe runs collapse to ``_``, edges trimmed.

    Deliberately conservative (ASCII-ish, no spaces, no path separators) —
    these tokens end up in file names handed to ffmpeg, to third-party
    separator CLIs that re-derive their output name from the input's, and into
    FCPXML ``src`` attributes.
    """
    cleaned = _SLUG_UNSAFE.sub("_", text).strip("._-")
    return cleaned or "source"


def unique_name(base: str, used: set[str]) -> str:
    """``base`` if free, else ``base_2``, ``base_3``, … Registers the result.

    The suffix is appended (not inserted), so the returned name keeps ``base``
    as a prefix — but callers must compare names for EQUALITY, never with
    ``startswith``: ``A1`` is a prefix of ``A10`` and that mistake is exactly
    what mis-paired clips in ``--verify``.
    """
    name = base
    n = 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name


@dataclass
class SourceRef:
    """One physical input file (plus the audio stream chosen inside it)."""

    sid: str
    path: Path
    kind: SourceKind
    # Camera sub-folder name for cameras; "" for recorders.
    group: str = ""
    # Human-facing label (file stem, or "<camera>/<stem>"). Never an identity.
    display_name: str = ""
    # Index of the chosen audio stream WITHIN the audio streams of the
    # container (i.e. the N in ffmpeg's ``-map 0:a:N``), or None when the file
    # has no audio at all.
    stream_index: int | None = 0
    # Index back into the caller's own parallel lists (clip index, recorder
    # index) so existing positional code can keep working during the
    # transition to fully id-addressed data.
    index: int = 0
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def is_silent(self) -> bool:
        return self.stream_index is None

    def artifact_name(self, suffix: str) -> str:
        """``<sid>_<suffix>`` — the only way an artifact should be named."""
        return f"{self.sid}_{suffix}" if suffix else self.sid


def assign_source_ids(
    entries: list[tuple[SourceKind, str, Path]],
    used: set[str] | None = None,
) -> list[SourceRef]:
    """Build ``SourceRef``s with unique ``sid``s for ``(kind, group, path)``.

    The id starts as ``<kind-prefix>_<group>_<stem>``; a collision (same stem
    in two recorder folders, ``clip.mov`` next to ``clip.mp4``, two cameras
    with identically named clips) appends the file extension and then a
    numeric suffix, so the result is unique, stable for a given input set, and
    still recognisable in a file listing.

    ``used`` lets a caller keep several batches disjoint (cameras first, then
    recorders) — pass the same set to both calls.
    """
    taken = used if used is not None else set()
    # Pre-compute which bases collide so the disambiguation is applied to ALL
    # members of a colliding set (not just the second one onwards) — otherwise
    # "take" and "take_2" would be an odd pair for two equally-named sources.
    bases: list[str] = []
    for kind, group, path in entries:
        prefix = "cam" if kind == "camera" else "rec"
        parts = [prefix]
        if group:
            parts.append(slugify(group))
        parts.append(slugify(path.stem))
        bases.append("_".join(parts))

    counts: dict[str, int] = {}
    for b in bases:
        counts[b] = counts.get(b, 0) + 1

    refs: list[SourceRef] = []
    for i, ((kind, group, path), base) in enumerate(zip(entries, bases, strict=True)):
        candidate = base
        if counts[base] > 1 and path.suffix:
            candidate = f"{base}_{slugify(path.suffix.lstrip('.'))}"
        sid = unique_name(candidate, taken)
        display = f"{group}/{path.stem}" if group else path.stem
        refs.append(
            SourceRef(
                sid=sid,
                path=path,
                kind=kind,
                group=group,
                display_name=display,
                index=i,
            )
        )
    return refs
