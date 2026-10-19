from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from media_cli.providers.anikoto import AnikotoProvider


def card(href, title, original=""):
    return (
        f'<a href="{href}"><img alt="{title}"><span>13 13 TV</span></a>'
        f'<a href="{href}" data-jp="{original}">{title}</a>'
    )


def install(monkeypatch, media, results, pages=None):
    provider = AnikotoProvider()
    provider._media_cache[str(media["id"])] = media
    seen = []
    pages = pages or {}

    def request(req, timeout=None):
        url = req.full_url
        seen.append(url)
        if "/filter?" in url:
            query = parse_qs(urlsplit(url).query)["keyword"][0]
            html = results(query) if callable(results) else results
        else:
            html = pages.get(url, '<div id="watch-main" data-id="999"><h1>Wrong show</h1></div>')
        return Mock(read=lambda: html.encode())

    monkeypatch.setattr("urllib.request.urlopen", request)
    return provider, seen


def season_two():
    return {
        "id": 166531,
        "title": {"english": "OSHI NO KO Season 2", "romaji": "[Oshi no Ko] 2nd Season", "native": "【推しの子】第2期"},
        "synonyms": ["我推的孩子", "【OSHI NO KO】Season 2"],
    }


def test_translated_card_original_title_is_used_after_poster_anchor(monkeypatch):
    href = "https://anikototv.to/watch/my-star-season-2-cwjwu/ep-1"
    provider, seen = install(
        monkeypatch,
        season_two(),
        card("/watch/kuroko-s-basketball-2-qsoni/ep-1", "Kuroko's Basketball 2")
        + card(href, "My Star: Season 2", "Oshi no Ko 2nd Season"),
        {href: '<div id="watch-main" data-id="6328"><h1 data-jp="Oshi no Ko 2nd Season">My Star: Season 2</h1></div>'},
    )
    assert provider._get_anime_page("166531") == ("6328", href)
    assert not any("kuroko" in url for url in seen)


@pytest.mark.parametrize(
    "media, html",
    [
        (season_two(), card("/watch/kuroko-s-basketball-2-qsoni/ep-1", "Kuroko's Basketball 2")),
        (
            {
                "id": 150672,
                "title": {"english": "OSHI NO KO", "romaji": "[Oshi no Ko]"},
                "synonyms": ["Favorite Girl", "My Idol's Child"],
            },
            card("/watch/wasteful-days-of-high-school-girl-e8q1j/ep-1", "Wasteful Days of High School Girl"),
        ),
    ],
)
def test_shared_words_or_season_digits_never_authorize_other_anime(monkeypatch, media, html):
    provider, seen = install(monkeypatch, media, html)
    assert provider._get_anime_page(str(media["id"])) == (None, None)
    assert all("/filter?" in url for url in seen)
    assert provider._anime_page_cache == {}


def test_successful_but_irrelevant_search_does_not_stop_other_title_queries(monkeypatch):
    media = {"id": 123, "title": {"english": "A Different Translation", "native": "本当の名前"}}
    href = "https://anikototv.to/watch/the-real-title-ab123/ep-1"
    provider, seen = install(
        monkeypatch,
        media,
        lambda q: (
            card(href, "Site translation", "本当の名前")
            if q == "本当の名前"
            else card("/watch/other-abcde", "Different Show")
        ),
        {href: '<div id="watch-main" data-id="1234"><h1 data-jp="本当の名前">Site translation</h1></div>'},
    )
    assert provider._get_anime_page("123") == ("1234", href)
    assert any("%E6%9C%AC" in url for url in seen)


def test_redirected_or_mismatched_watch_page_is_not_cached(monkeypatch):
    href = "https://anikototv.to/watch/oshi-no-ko-season-2-ab123/ep-1"
    provider, _ = install(
        monkeypatch,
        season_two(),
        card(href, "OSHI NO KO Season 2"),
        {href: '<div id="watch-main" data-id="1402"><h1>Kuroko\'s Basketball 2</h1></div>'},
    )
    assert provider._get_anime_page("166531") == (None, None)
    assert provider._anime_page_cache == {}


def test_duplicate_matching_titles_are_ambiguous_not_first_result_wins(monkeypatch):
    provider, seen = install(
        monkeypatch,
        season_two(),
        card("/watch/copy-a-abcde/ep-1", "OSHI NO KO Season 2")
        + card("/watch/copy-b-abcde/ep-1", "OSHI NO KO Season 2"),
    )
    assert provider._get_anime_page("166531") == (None, None)
    assert all("/filter?" in url for url in seen)


def test_first_season_alias_cannot_override_second_season_identity(monkeypatch):
    href = "https://anikototv.to/watch/first-season-abcde/ep-1"
    provider, seen = install(monkeypatch, season_two(), card(href, "我推的孩子"))
    assert provider._get_anime_page("166531") == (None, None)
    assert all("/filter?" in url for url in seen)


def test_unrelated_recommendation_data_id_is_not_a_watch_page_identity(monkeypatch):
    href = "https://anikototv.to/watch/oshi-no-ko-season-2-ab123/ep-1"
    provider, _ = install(
        monkeypatch,
        season_two(),
        card(href, "OSHI NO KO Season 2"),
        {href: '<h1>OSHI NO KO Season 2</h1><aside data-id="1402">Recommendations</aside>'},
    )
    assert provider._get_anime_page("166531") == (None, None)


def test_verified_match_is_cached_and_logs_both_identities(monkeypatch, caplog):
    href = "https://anikototv.to/watch/my-star-season-2-cwjwu/ep-1"
    provider, seen = install(
        monkeypatch,
        season_two(),
        card(href, "My Star: Season 2", "Oshi no Ko 2nd Season"),
        {href: '<div id="watch-main" data-id="6328"><h1 data-jp="Oshi no Ko 2nd Season">My Star: Season 2</h1></div>'},
    )
    with caplog.at_level("DEBUG", logger="media_cli.providers.anikoto"):
        assert provider._get_anime_page("166531") == ("6328", href)
    count = len(seen)
    assert provider._get_anime_page("166531") == ("6328", href)
    assert len(seen) == count
    assert "166531" in caplog.text and "6328" in caplog.text


def test_numeric_anime_title_is_an_identity_not_a_partial_season_match(monkeypatch):
    href = "https://anikototv.to/watch/86-ab123/ep-1"
    provider, _ = install(
        monkeypatch,
        {"id": 131586, "title": {"romaji": "86"}},
        card(href, "86"),
        {href: '<div id="watch-main" data-id="86"><h1>86</h1></div>'},
    )
    assert provider._get_anime_page("131586") == ("86", href)


def test_movie_card_cannot_borrow_tv_original_title(monkeypatch):
    href = "https://anikototv.to/watch/oshi-no-ko-movie-ab123/ep-1"
    media = {"id": 150672, "title": {"english": "OSHI NO KO"}, "format": "TV"}
    provider, seen = install(monkeypatch, media, card(href, "Oshi no Ko Movie", "Oshi no Ko"))
    assert provider._get_anime_page("150672") == (None, None)
    assert all("/filter?" in url for url in seen)


def test_punctuation_distinguished_sequel_is_not_original(monkeypatch):
    href = "https://anikototv.to/watch/k-on-abcde/ep-1"
    media = {"id": 5680, "title": {"romaji": "K-ON!", "native": "けいおん!"}, "synonyms": ["K-On"]}
    provider, seen = install(
        monkeypatch,
        media,
        card(href, "K-ON!!", "けいおん!!"),
        {href: '<div id="watch-main" data-id="7791"><h1>K-ON!!</h1></div>'},
    )
    assert provider._get_anime_page("5680") == (None, None)
    assert provider._anime_page_cache == {}


def test_numbered_synonym_does_not_disqualify_exact_distinctive_primary_title(monkeypatch):
    href = "https://anikototv.to/watch/nitian-xie-shen-nian-fan-abcde/ep-1"
    media = {"id": 184872, "title": {"romaji": "Nitian Xie Shen: Nian Fan"}, "synonyms": ["Against the Gods 2"]}
    provider, _ = install(
        monkeypatch,
        media,
        card(href, "Nitian Xie Shen: Nian Fan"),
        {href: '<div id="watch-main" data-id="8765"><h1>Nitian Xie Shen: Nian Fan</h1></div>'},
    )
    assert provider._get_anime_page("184872") == ("8765", href)


def test_off_origin_redirect_cannot_supply_a_verified_identity(monkeypatch):
    href = "https://anikototv.to/watch/oshi-no-ko-abcde/ep-1"
    provider = AnikotoProvider()
    provider._media_cache["150672"] = {"id": 150672, "title": {"romaji": "[Oshi no Ko]"}}

    def request(req, timeout=None):
        is_search = "/filter?" in req.full_url
        html = (
            card(href, "[Oshi no Ko]")
            if is_search
            else '<div id="watch-main" data-id="777"><h1>[Oshi no Ko]</h1></div>'
        )
        return Mock(
            read=lambda: html.encode(),
            geturl=lambda: req.full_url if is_search else "https://other.example/watch/wrong",
        )

    monkeypatch.setattr("urllib.request.urlopen", request)
    assert provider._get_anime_page("150672") == (None, None)
    assert provider._anime_page_cache == {}


@pytest.mark.parametrize("actual_mal, accepted", [(52034, True), (55791, False), (None, False)])
def test_mal_metadata_verifies_reused_title_before_caching(monkeypatch, actual_mal, accepted):
    import json

    href = "https://anikototv.to/watch/oshi-no-ko-abcde/ep-1"
    provider = AnikotoProvider()
    provider._media_cache["150672"] = {"id": 150672, "idMal": 52034, "title": {"romaji": "[Oshi no Ko]"}}

    def request(req, timeout=None):
        if "/filter?" in req.full_url:
            html = card(href, "[Oshi no Ko]")
        elif "/ajax/episode/list/" in req.full_url:
            attribute = f'data-mal="{actual_mal}"' if actual_mal else ""
            html = json.dumps({"result": f'<div class="episodes"><a data-num="1" {attribute}>1</a></div>'})
        else:
            html = '<div id="watch-main" data-id="6475"><h1>[Oshi no Ko]</h1></div>'
        return Mock(read=lambda: html.encode())

    monkeypatch.setattr("urllib.request.urlopen", request)
    expected = ("6475", href) if accepted else (None, None)
    assert provider._get_anime_page("150672") == expected
    assert bool(provider._anime_page_cache) is accepted


def test_native_search_translation_can_be_verified_by_mal_not_guessed(monkeypatch):
    import json

    href = "https://anikototv.to/watch/my-star-hg319/ep-1"
    provider = AnikotoProvider()
    provider._media_cache["150672"] = {
        "id": 150672,
        "idMal": 52034,
        "title": {"english": "OSHI NO KO", "native": "【推しの子】"},
    }

    def request(req, timeout=None):
        if "/filter?" in req.full_url:
            query = parse_qs(urlsplit(req.full_url).query)["keyword"][0]
            html = (
                ('<div class="inner"><div class="poster" data-tip="6475">' + card(href, "My Star") + "</div></div>")
                if query == "推しの子"
                else ""
            )
        elif "/ajax/episode/list/6475" in req.full_url:
            html = json.dumps({"result": '<a data-num="1" data-mal="52034">1</a>'})
        else:
            html = '<div id="watch-main" data-id="6475"><h1>My Star</h1></div>'
        return Mock(read=lambda: html.encode())

    monkeypatch.setattr("urllib.request.urlopen", request)
    assert provider._get_anime_page("150672") == ("6475", href)


def test_apostrophe_distinguished_sequel_is_not_original_without_mal(monkeypatch):
    href = "https://anikototv.to/watch/working-abcde/ep-1"
    media = {"id": 6956, "title": {"romaji": "WORKING!!"}}
    provider, _ = install(
        monkeypatch,
        media,
        card(href, "WORKING'!!"),
        {href: '<div id="watch-main" data-id="10521"><h1>WORKING\'!!</h1></div>'},
    )
    assert provider._get_anime_page("6956") == (None, None)
