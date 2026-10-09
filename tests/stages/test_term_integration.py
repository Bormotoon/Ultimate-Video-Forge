from studio.core.transcript import Segment, Transcript, Word
from studio.stages.text import _text_settings, check_transcript_terms


def test_manual_terms_never_require_network_and_realign(tmp_path, monkeypatch):
    import studio.stages.term_check as terms

    def forbidden(*args, **kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr(terms.WikiTermVerifier, "hits", forbidden)
    transcript = Transcript(
        tmp_path / "a.wav", "en", 2, [Segment(0, 2, (Word("Oldword", 0, 1), Word("works", 1, 2)))]
    )
    settings = _text_settings({"text": {"term_fixes": {"Oldword": "Newword"}}})
    result, report = check_transcript_terms(transcript, settings, tmp_path / "terms.json")
    assert result.segments[0].text == "Newword works"
    assert result.words[0].text == "Newword"
    assert report["applied"] == 1 and not report["network_enabled"]
