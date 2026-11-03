from unittest.mock import patch

from media_cli.providers.miruro import MiruroProvider


def test_miruro_falls_back_to_local_anilist_resolvers_when_pipe_sources_empty():
    p = MiruroProvider()

    def fake_fetch_pipe(payload, domains):
        if payload["path"] == "episodes":
            return {
                "providers": {
                    "anidbapp": {
                        "episodes": {
                            "sub": [{"number": 1, "id": "raw-1"}],
                        },
                    },
                },
            }
        if payload["path"] == "sources":
            return {"streams": []}
        return None

    class FallbackProvider:
        name = "Anikoto"

        def episode_sources(self, provider_id, episode, ttype):
            assert provider_id == "154587"
            assert episode == "1"
            assert ttype == "sub"
            return {
                "episode": {
                    "sourceUrls": [
                        {
                            "sourceName": "Anikoto (1080p)",
                            "streamUrl": "https://cdn.example/frieren.m3u8",
                            "type": "hls",
                        },
                    ],
                },
            }

    with patch("media_cli.providers.miruro._fetch_pipe", side_effect=fake_fetch_pipe):
        with patch("media_cli.providers.get_provider", return_value=FallbackProvider()):
            sources = p.episode_sources("154587", "1", "sub")

    urls = sources["episode"]["sourceUrls"]
    assert len(urls) == 1
    assert urls[0]["sourceName"] == "Anikoto (1080p)"
    assert urls[0]["streamUrl"] == "https://cdn.example/frieren.m3u8"
