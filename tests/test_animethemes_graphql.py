import json

from media_cli.services import animethemes as service


def test_graphql_slug_lookup_preserves_existing_song_shape(monkeypatch):
    captured = {}
    def request(url, data=None, **kwargs):
        captured.update(url=url, body=json.loads(data))
        return {"data": {"anime": {"id": 1, "slug": "example", "title": {"romaji": "Example"},
            "images": {"nodes": [{"link": "https://example.test/cover.jpg"}]},
            "themes": [{"id": 3, "slug": "OP1", "song": {"title": {"romaji": "Song"},
                "staff": [{"role": "Performance", "artist": {"name": {"main": "Artist"}}}]},
                "entries": [{"version": 1, "episodes": "1-11", "videos": {"nodes": [
                    {"link": "https://example.test/video.webm", "resolution": 1080,
                     "audio": {"link": "https://example.test/exact.ogg", "mimetype": "audio/ogg"}}]}}]}]}}}
    monkeypatch.setattr(service, "request_json", request)
    anime = service.get_anime_for_show({"slug": "example"})
    assert captured["url"] == "https://rs.animethemes.moe/graphql"
    assert captured["body"]["variables"] == {"slug": "example"}
    rows = service.anime_song_rows(anime)
    assert rows[0]["song"] == "Song"
    assert rows[0]["artists"] == "Artist"
    assert rows[0]["variants"][0]["episodes"] == "1-11"
    assert "audio { link mimetype }" in captured["body"]["query"]
    assert rows[0]["variants"][0]["raw"]["video"]["audio"]["link"] == "https://example.test/exact.ogg"


def test_graphql_error_is_not_empty_success(monkeypatch):
    import pytest
    from media_cli.core.api import SearchFailure
    monkeypatch.setattr(service, "request_json", lambda *a, **kw: {"errors": [{"message": "bad"}]})
    with pytest.raises(SearchFailure):
        service.get_anime_for_show({"slug": "example"})


def test_search_maps_graphql_buckets_without_rest_enrichment(monkeypatch):
    def request(url, data=None, **kwargs):
        assert url == service.GRAPHQL_URL
        assert json.loads(data)["variables"]["search"] == "oshi no ko"
        anime = {"slug": "oshi", "title": {"romaji": "Oshi"}, "themes": [], "images": {"nodes": []}}
        return {"data": {"search": {
            "anime": {"data": [anime]}, "themes": {"data": []},
            "artists": {"data": [{"slug": "yoasobi", "name": {"main": "YOASOBI"}, "images": {"nodes": []}}]},
            "series": {"data": [{"slug": "oshi", "title": {"romaji": "Oshi"}}]},
            "studios": {"data": []}, "playlists": {"data": []},
        }}}
    monkeypatch.setattr(service, "request_json", request)
    found = service.search("oshi no ko")
    assert found["anime"][0]["name"] == "Oshi"
    assert found["artists"][0]["name"] == "YOASOBI"
    assert found["series"][0]["name"] == "Oshi"


def test_artist_maps_performance_and_group_credits(monkeypatch):
    def request(url, data=None, **kwargs):
        assert url == service.GRAPHQL_URL
        assert json.loads(data)["variables"] == {"slug": "artist"}
        return {"data": {"artist": {
            "name": {"main": "Artist"}, "images": {"nodes": []},
            "groups": {"edges": [{"node": {"slug": "group", "name": {"main": "Group"}}, "as": "Character"}]},
            "songStaff": [{"role": "Performance", "as": "Character", "song": {
                "id": 1, "title": {"romaji": "Song"}, "themes": [{"id": 2, "slug": "OP1",
                    "anime": {"slug": "anime", "title": {"romaji": "Anime"}}}]}}],
            "memberSongStaff": [{"role": "Performance", "song": {"id": 3}}],
        }}}
    monkeypatch.setattr(service, "request_json", request)
    artist = service.get_artist("artist")
    assert artist["name"] == "Artist"
    assert artist["groups"][0]["artistmember"]["as"] == "Character"
    assert service.artist_theme_rows(artist)[0]["label"] == "Song OP 1"
    assert artist["memberPerformances"][0]["song"]["id"] == 3


def test_theme_lookup_uses_exact_graphql_id(monkeypatch):
    def request(url, data=None, **kwargs):
        assert url == service.GRAPHQL_URL
        assert json.loads(data)["variables"] == {"ids": [11]}
        return {"data": {"themeConnection": {"nodes": [{
            "id": 11, "slug": "OP1", "anime": {"title": {"romaji": "Anime"}},
            "song": {"title": {"romaji": "Song"}}, "entries": [],
        }]}}}
    monkeypatch.setattr(service, "request_json", request)
    assert service.get_theme(11)["song"]["title"] == "Song"


def test_studio_uses_existing_anime_shape(monkeypatch):
    def request(url, data=None, **kwargs):
        assert json.loads(data)["variables"] == {"slug": "doga_kobo"}
        return {"data": {"studio": {"slug": "doga_kobo", "name": "Doga Kobo",
            "anime": {"nodes": [{"slug": "oshi", "title": {"romaji": "Oshi"}, "themes": []}]}}}}
    monkeypatch.setattr(service, "request_json", request)
    assert service.get_studio("doga_kobo")["anime"][0]["name"] == "Oshi"
