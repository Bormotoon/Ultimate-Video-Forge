"""Carry ASR word timings onto conservatively corrected segment text."""

from __future__ import annotations

import difflib
import re
from dataclasses import replace

from studio.core.transcript import Segment, Transcript, Word

_WORD_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)


def realign_transcript_words(transcript: Transcript) -> tuple[Transcript, int]:
    """Return a transcript whose changed segments carry corrected timed words."""
    changed = 0
    segments: list[Segment] = []
    for segment in transcript.segments:
        words = realign_words(segment.words, segment.text)
        if words is None or words == segment.words:
            segments.append(segment)
            continue
        segments.append(replace(segment, words=words, raw_words=segment.words))
        changed += 1
    return replace(transcript, segments=segments), changed


def realign_words(raw_words: tuple[Word, ...], text: str) -> tuple[Word, ...] | None:
    """Return one timed word per corrected text token, or None without timings."""
    tokens = text.split()
    if not raw_words:
        return None
    if not tokens:
        return ()
    raw_norm = [_normalized(word.text) for word in raw_words]
    new_norm = [_normalized(token) for token in tokens]
    matcher = difflib.SequenceMatcher(None, raw_norm, new_norm, autojunk=False)
    result: list[Word | None] = [None] * len(tokens)
    for tag, first_raw, last_raw, first_new, last_new in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(last_new - first_new):
                source = raw_words[first_raw + offset]
                result[first_new + offset] = Word(
                    tokens[first_new + offset], source.start, source.end, source.probability
                )
        elif tag == "replace":
            result[first_new:last_new] = _spread(
                tokens[first_new:last_new], raw_words[first_raw].start, raw_words[last_raw - 1].end
            )
        elif tag == "insert":
            before = raw_words[first_raw - 1].end if first_raw else raw_words[0].start
            after = raw_words[first_raw].start if first_raw < len(raw_words) else raw_words[-1].end
            result[first_new:last_new] = _spread(
                tokens[first_new:last_new], min(before, after), max(before, after)
            )
    words = tuple(word for word in result if word is not None)
    if len(words) != len(tokens):
        return None
    cursor = words[0].start
    monotonic: list[Word] = []
    for word in words:
        start = max(word.start, cursor)
        end = max(word.end, start)
        monotonic.append(Word(word.text, round(start, 3), round(end, 3), word.probability))
        cursor = start
    return tuple(monotonic)


def _spread(tokens: list[str], start: float, end: float) -> list[Word]:
    weights = [max(1, len(token)) for token in tokens]
    total = float(sum(weights))
    cursor = start
    output: list[Word] = []
    for token, weight in zip(tokens, weights, strict=True):
        next_cursor = cursor + (end - start) * weight / total
        output.append(Word(token, round(cursor, 3), round(next_cursor, 3)))
        cursor = next_cursor
    return output


def _normalized(text: str) -> str:
    return "".join(_WORD_RE.findall(text.lower().replace("ё", "е")))