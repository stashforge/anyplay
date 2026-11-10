import json

from media_cli.services import animethemes as service


def test_playlist_keeps_track_order_and_selected_video(monkeypatch):
    def request(url, data=None, **kwargs):
        assert json.loads(data)["variables"] == {"id": "playlist"}
        theme = {"id": 1, "slug": "OP1", "song": {"title": {"romaji": "Song"}},
                 "anime": {"title": {"romaji": "Anime"}}}
        return {"data": {"playlist": {"id": "playlist", "name": "Playlist", "tracks": [
            {"id": "second", "position": 2, "entry": {"version": 2, "theme": theme},
             "video": {"id": 2, "link": "https://example.test/two.webm", "resolution": 1080}},
            {"id": "first", "position": 1, "entry": {"version": 1, "theme": theme},
             "video": {"id": 1, "link": "https://example.test/one.webm", "resolution": 720}},
        ]}}}
    monkeypatch.setattr(service, "request_json", request)
    playlist = service.get_playlist("playlist")
    songs = service.playlist_song_rows(playlist)
    assert [song["variants"][0]["stream"]["link"] for song in songs] == [
        "https://example.test/one.webm", "https://example.test/two.webm"]
    assert [song["variants"][0]["theme"] for song in songs] == ["OP1", "OP1v2"]


def test_playlist_opens_shared_details_without_autoplay(monkeypatch):
    from media_cli.app import animethemes_song_playback as flow

    monkeypatch.setattr(flow.app_core, "with_loading", lambda message, fn, *args: fn(*args))
    monkeypatch.setattr(flow.service, "get_playlist", lambda identity: {"id": identity})
    songs = [{"song": "Song"}]
    monkeypatch.setattr(flow.service, "playlist_song_rows", lambda playlist: songs)
    monkeypatch.setattr(flow, "call_on_ui_thread", lambda work: work())
    captured = {}
    monkeypatch.setattr(flow, "run_song_collection", lambda *args, **kwargs: captured.update(kwargs) or "back")
    assert flow.open_playlist(None, None, {}, None, "playlist") == "back"
    assert captured["autoplay"] is False
    assert captured["collection_id"] == "animethemes:playlist:playlist"
