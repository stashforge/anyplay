from media_cli.providers.animegg import AnimeGG


def test_animegg_uses_available_iframe_when_requested_audio_missing():
    p = AnimeGG()
    episode_html = """
    <div class="tab-pane" id="dubbed-Animegg">
      <iframe src="/embed/144542"></iframe>
    </div>
    """
    embed_html = """
    <script>
    var videoSources = [
      {file: "/play/535980/video.mp4?for=101", label: "480p"},
      {file: "/play/535981/video.mp4?for=101", label: "1080p"}
    ];
    </script>
    """

    def fake_fetch(url):
        if "/embed/" in url:
            return embed_html
        return episode_html

    p._fetch_html = fake_fetch

    sources = p.episode_sources("frieren", "frieren-episode-1", "sub")
    urls = sources["episode"]["sourceUrls"]

    assert [src["resolution"] for src in urls] == ["1080", "480"]
    assert urls[0]["sourceName"] == "AnimeGG (1080p)"
    assert urls[0]["streamUrl"] == "https://www.animegg.org/play/535981/video.mp4?for=101"
