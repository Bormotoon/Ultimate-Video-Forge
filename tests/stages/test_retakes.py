"""Tests for retake detection (pure logic, no media)."""

from __future__ import annotations

from studio.core.transcript import Word
from studio.stages.retake_models import RetakeSettings
from studio.stages.retakes import Take, detect_retakes


def _words(text: str, start: float, dt: float = 0.35) -> list[Word]:
    """Build word spans from a phrase, one word every ``dt`` seconds from ``start``."""
    out = []
    t = start
    for tok in text.split():
        out.append(Word(text=tok, start=t, end=t + dt * 0.8, probability=0.95))
        t += dt
    return out


def _cfg(**kw) -> RetakeSettings:
    return RetakeSettings(detect_retakes=True, **kw)


def test_detects_a_restart_mid_sentence() -> None:
    # The real-world shape: a phrase begins, the speaker stumbles and restarts
    # the SAME words moments later, then continues past them (no pause at all
    # between the two attempts, and none needed by the detector).
    words = _words("кто ещё учится в школе а не выпустился", 450.0)
    words += _words("кто ещё учится в школе а не выпустился ну и так далее", 458.0)
    groups = detect_retakes(words, _cfg())
    assert len(groups) == 1
    g = groups[0]
    assert len(g.takes) == 2
    assert g.keeper is g.takes[-1]
    assert "так далее" in g.keeper.text
    s, e = g.span
    assert s == 450.0 and e > 460.0  # extends past the first attempt (452.73)


def test_three_attempts_chain_into_one_group() -> None:
    words = _words("это потрясающе важно и это экономит", 0.0)
    words += _words("это потрясающе важно и это экономит силы", 4.0)
    words += _words("это потрясающе важно и это экономит силы нервы время", 8.5)
    groups = detect_retakes(words, _cfg())
    assert len(groups) == 1
    assert len(groups[0].takes) == 3
    assert groups[0].keeper_index == -1
    assert "нервы время" in groups[0].keeper.text


def test_distinct_sentences_are_not_grouped() -> None:
    words = _words("сегодня поговорим о синхронизации звука и видео", 0.0)
    words += _words("это бич всех людей которые учатся новому", 4.0)
    groups = detect_retakes(words, _cfg())
    assert groups == []


def test_short_run_below_min_words_is_ignored() -> None:
    # Repeats of a run shorter than retake_min_words never trigger a group.
    words = _words("нет нет нет", 0.0)
    words += _words("нет нет нет", 1.0)
    groups = detect_retakes(words, _cfg(retake_min_words=4))
    assert groups == []


def test_far_apart_repeat_is_a_callback_not_a_retake() -> None:
    words = _words("включить проекцию с экрана на доску", 0.0)
    # same line again but 20s later — a callback, not a retake
    words += _words("совершенно другой текст здесь чтобы разделить", 5.0)
    words += _words("включить проекцию с экрана на доску", 20.0)
    groups = detect_retakes(words, _cfg(retake_max_gap_s=6.0))
    assert groups == []


def test_disabled_by_default() -> None:
    words = _words("нажать виндовс пи справа выезжает", 10.0)
    words += _words("нажать виндовс пи справа выезжает панелька", 13.0)
    assert detect_retakes(words, RetakeSettings()) == []  # detect_retakes defaults False


def test_unrelated_speech_never_produces_groups() -> None:
    words = _words("вам нужно нажать кнопку старт внизу экрана", 0.0)
    words += _words("а потом открыть меню сверху справа кликнуть", 3.0)
    assert detect_retakes(words, _cfg()) == []


def test_keeper_and_span_helpers() -> None:
    g = detect_retakes(
        _words("один два три четыре пять", 0.0) + _words("один два три четыре пять шесть", 3.0),
        _cfg(),
    )[0]
    assert isinstance(g.keeper, Take)
    assert g.span[0] == 0.0


def test_real_recorder_transcript_finds_known_retakes() -> None:
    """Regression fixture distilled from a real 93-minute lecture recording
    (POS-vyp26 dataset) where the speaker audibly restarts two lines — the
    second restart begins mid-sentence, after an unrelated "а ты" preamble
    that is NOT part of the repeated material (the detector must find the
    true start of the repeat, not just whole-phrase-aligned repeats).
    Confirms the detector finds real-world retakes, not just synthetic ones.
    """
    # "кто ещё учится в школе а не выпустился" restarted ~5s later, after a
    # short unrelated lead-in ("а ты") that only appears in the first attempt.
    words = _words("а ты кто ещё учится в школе а не выпустился", 456.0)
    words += _words("кто ещё учится в школе а не выпустился ну для тебя", 464.5)
    # unrelated filler in between groups
    words += _words("потому что я буду сейчас говорить о будущем", 480.0)
    # "потому что я буду сейчас говорить о будущем" restarted, extended further
    words += _words("потому что я буду сейчас говорить о будущем этого предмета", 486.0)

    groups = detect_retakes(words, _cfg())
    assert len(groups) == 2
    assert all(len(g.takes) == 2 for g in groups)
    # first group's repeated material starts at "кто", not the "а ты" preamble
    assert groups[0].takes[0].text.startswith("кто")
    assert "предмета" in groups[1].keeper.text


def test_three_identical_takes_form_one_group_of_three() -> None:
    """All attempts belong to the group, and the LAST one is kept.

    With the same phrase at 0 s, 3 s and 6 s, extending the 3->6 match
    backwards found that the tokens before 3 s matched too — they are the same
    phrase, at 0 s — and walked the span into the first attempt. The two
    occurrences then overlapped, their gap came out negative, the third attempt
    was rejected as implausible, and the group stopped at two with the MIDDLE
    take kept as the keeper.
    """
    phrase = ["кто", "ещё", "учится", "в", "школе"]
    words: list[Word] = []
    for attempt_start in (0.0, 3.0, 6.0):
        for i, text in enumerate(phrase):
            t = attempt_start + i * 0.2
            words.append(Word(text=text, start=t, end=t + 0.15, probability=0.9))

    cfg = RetakeSettings(detect_retakes=True, retake_min_words=4, retake_max_gap_s=6.0)
    groups = detect_retakes(words, cfg)

    assert len(groups) == 1
    group = groups[0]
    assert len(group.takes) == 3, [t.text for t in group.takes]
    # The keeper is the final attempt — the version the speaker completed.
    assert group.keeper is group.takes[-1]
    assert group.keeper.start >= 6.0 - 1e-6


def test_retake_takes_never_overlap() -> None:
    """Overlapping takes are one span counted twice, not two attempts."""
    phrase = ["раз", "два", "три", "четыре"]
    words: list[Word] = []
    for attempt_start in (0.0, 2.5, 5.0, 7.5):
        for i, text in enumerate(phrase):
            t = attempt_start + i * 0.2
            words.append(Word(text=text, start=t, end=t + 0.15, probability=0.9))

    cfg = RetakeSettings(detect_retakes=True, retake_min_words=4, retake_max_gap_s=6.0)
    groups = detect_retakes(words, cfg)
    assert groups
    for group in groups:
        for earlier, later in zip(group.takes, group.takes[1:], strict=False):
            assert (
                earlier.end <= later.start + 1e-9
            ), f"takes overlap: {earlier.start}-{earlier.end} then {later.start}-{later.end}"
            assert earlier.end > earlier.start
