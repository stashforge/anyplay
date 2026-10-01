import pytest
from media_cli.media.languages import (
    normalize_lang_code,
    parse_language_list,
    match_track_language,
    filter_tracks_by_language,
)


def test_normalize_lang_code():
    assert normalize_lang_code("en") == "en"
    assert normalize_lang_code("eng") == "en"
    assert normalize_lang_code("English") == "en"
    assert normalize_lang_code("English [CC]") == "en"
    assert normalize_lang_code("ja") == "ja"
    assert normalize_lang_code("jpn") == "ja"
    assert normalize_lang_code("Japanese") == "ja"
    assert normalize_lang_code("Japanese - Original") == "ja"
    assert normalize_lang_code("spa") == "es"
    assert normalize_lang_code("Spanish (Latin America)") == "es"

    # alpha3 tests
    assert normalize_lang_code("en", target="alpha3") == "eng"
    assert normalize_lang_code("Japanese", target="alpha3") == "jpn"
    assert normalize_lang_code("es", target="alpha3") == "spa"


def test_parse_language_list():
    assert parse_language_list("multi") == ["multi"]
    assert parse_language_list("all") == ["multi"]
    assert parse_language_list("") == ["multi"]
    assert parse_language_list(None) == ["multi"]
    assert parse_language_list("eng, jpn") == ["en", "ja"]
    assert parse_language_list("en, ja") == ["en", "ja"]
    assert parse_language_list(["eng", "jpn"]) == ["en", "ja"]
    assert parse_language_list("spanish, french, english") == ["es", "fr", "en"]


def test_filter_tracks_subtitles():
    subs = [
        {"url": "http://sub/en.vtt", "label": "English", "language": "en"},
        {"url": "http://sub/ja.vtt", "label": "Japanese", "language": "ja"},
        {"url": "http://sub/es.vtt", "label": "Español", "language": "es"},
        {"url": "http://sub/fr.vtt", "label": "French", "language": "fr"},
    ]

    # multi downloads all
    assert len(filter_tracks_by_language(subs, "multi")) == 4
    assert len(filter_tracks_by_language(subs, "all")) == 4

    # specific languages
    res = filter_tracks_by_language(subs, "eng, jpn")
    assert len(res) == 2
    assert res[0]["language"] == "en"
    assert res[1]["language"] == "ja"

    # language not present returns empty for subs
    assert filter_tracks_by_language(subs, "deu") == []


def test_filter_tracks_audio_fallback():
    audios = [
        {"url": "http://audio/ja.m3u8", "label": "Japanese", "language": "ja", "default": True},
        {"url": "http://audio/es.m3u8", "label": "Spanish", "language": "es", "default": False},
    ]

    # Matching requested audio
    res = filter_tracks_by_language(audios, "jpn", is_audio=True)
    assert len(res) == 1
    assert res[0]["language"] == "ja"

    # Requested audio (e.g. English) is missing -> Fall back to default track (Japanese)
    fallback = filter_tracks_by_language(audios, "eng", is_audio=True)
    assert len(fallback) == 1
    assert fallback[0]["language"] == "ja"
    assert fallback[0]["default"] is True
