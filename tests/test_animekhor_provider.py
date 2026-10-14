from media_cli.providers.animekhor import AnimeKhorProvider


def test_animekhor_prefers_current_org_domain():
    provider = AnimeKhorProvider()
    provider.metadata = {"name": "AnimeKhor"}
    provider.domains = ["https://animekhor.xyz", "https://animekhor.org"]

    assert provider.base_url == "https://animekhor.org"


def test_animekhor_search_uses_html_cards_without_ajax():
    calls = []
    ajax_calls = []

    def fetch(url):
        calls.append(url)
        return """
        <div class="listupd">
          <article class="bs">
            <a href="https://animekhor.org/anime/martial-master/" title="Martial Master (Wushen Zhuzai)">
              <div class="limit">
                <div class="typez ONA">ONA</div>
                <img src="https://animekhor.org/poster.jpg" />
              </div>
              <div class="tt">
                Martial Master (Wushen Zhuzai)
                <h2>Martial Master (Wushen Zhuzai)</h2>
              </div>
            </a>
          </article>
        </div>
        """

    def ajax_fetch(query):
        ajax_calls.append(query)
        raise AssertionError("AnimeKhor should not use AnimeXin ajax search")

    provider = AnimeKhorProvider(fetch=fetch, ajax_fetch=ajax_fetch)
    provider.metadata = {"name": "AnimeKhor"}
    provider.domains = ["https://animekhor.org", "https://animekhor.xyz"]

    rows = provider.search("martial master")

    assert ajax_calls == []
    assert calls == ["https://animekhor.org/?s=martial+master"]
    assert len(rows) == 1
    assert rows[0]["_provider"] == "animekhor"
    assert rows[0]["_provider_id"] == "https://animekhor.org/anime/martial-master/"
    assert rows[0]["name"] == "Martial Master (Wushen Zhuzai)"


def test_animekhor_catalog_keeps_batch_labels_and_latest_episode():
    pages = {
        "https://animekhor.org/anime/martial-master/": """
        <div class="lastend">
          <div class="inepcx"><a href="#"><span>First Episode</span><span class="epcur epcurfirst">Episode 1</span></a></div>
          <div class="inepcx">
            <a href="https://animekhor.org/martial-master-episode-698-subtitles-english-indonesian/">
              <span>New Episode</span><span class="epcur epcurlast">Episode 698</span>
            </a>
          </div>
        </div>
        <div class="eplister"><ul>
          <li data-index="0">
            <a href="https://animekhor.org/martial-master-episode-698-subtitles-english-indonesian/">
              <div class="epl-num">698</div><div class="epl-title">Episode 698</div>
            </a>
          </li>
          <li data-index="1">
            <a href="https://animekhor.org/martial-master-episodes-1-to-80-english-subtitles/">
              <div class="epl-num">1 to 80</div><div class="epl-title">Episodes 1 to 80</div>
            </a>
          </li>
        </ul></div>
        """,
    }

    def fetch(url):
        return pages[url]

    provider = AnimeKhorProvider(fetch=fetch)
    provider.metadata = {"name": "AnimeKhor"}
    provider.domains = ["https://animekhor.org", "https://animekhor.xyz"]

    catalog = provider.episode_catalog("https://animekhor.org/anime/martial-master/")

    episodes = catalog["episodes"]["sub"]
    assert [episode["label"] for episode in episodes] == ["1 to 80", "698"]
    assert episodes[0]["kind"] == "batch"
    assert episodes[0]["title"] == "Episodes 1 to 80"


def test_wordpress_catalog_skips_future_episode_row_with_stale_release_date():
    pages = {
        "https://animekhor.org/anime/against-the-gods-season-2/": """
        <div class="lastend">
          <div class="inepcx">
            <a href="https://animekhor.org/against-the-gods-season-2-episode-25-55/">
              <span>New Episode</span><span class="epcur epcurlast">Episode 25</span>
            </a>
          </div>
        </div>
        <div class="eplister"><ul>
          <li data-index="0">
            <a href="https://animekhor.org/against-the-gods-season-2-episode-25-55/">
              <div class="epl-num">25 [55] [4K]</div>
              <div class="epl-title">Against the Gods Season 2 Episode 25 [55] English Sub</div>
              <div class="epl-date">September 17, 2026</div>
            </a>
          </li>
          <li data-index="1">
            <a href="https://animekhor.org/against-the-gods-season-2-episode-24-54/">
              <div class="epl-num">24 [54] [4K]</div>
              <div class="epl-title">Against the Gods Season 2 Episode 24 [54] English Sub</div>
              <div class="epl-date">September 10, 2026</div>
            </a>
          </li>
          <li data-index="2">
            <a href="https://animekhor.org/against-the-gods-season-2-episode-23-53/">
              <div class="epl-num">23 [53] [4K]</div>
              <div class="epl-title">Against the Gods Season 2 Episode 23 [53] English Sub</div>
              <div class="epl-date">September 3, 2026</div>
            </a>
          </li>
          <li data-index="3">
            <a href="https://animekhor.org/against-the-gods-season-2-episode-29-59/">
              <div class="epl-num">29 [59] [4K]</div>
              <div class="epl-title">Against the Gods Season 2 Episode 29 [59] English Sub</div>
              <div class="epl-date">September 1, 2026</div>
            </a>
          </li>
          <li data-index="4">
            <a href="https://animekhor.org/against-the-gods-season-2-episode-22-52/">
              <div class="epl-num">22 [52] [4K]</div>
              <div class="epl-title">Against the Gods Season 2 Episode 22 [52] English Sub</div>
              <div class="epl-date">August 27, 2026</div>
            </a>
          </li>
        </ul></div>
        """,
    }

    def fetch(url):
        return pages[url]

    provider = AnimeKhorProvider(fetch=fetch)
    provider.metadata = {"name": "AnimeKhor"}
    provider.domains = ["https://animekhor.org"]

    catalog = provider.episode_catalog("https://animekhor.org/anime/against-the-gods-season-2/")

    labels = catalog["labels"]
    assert "29 [59]" not in labels.values()
    assert "25 [55]" in labels.values()
    assert catalog["ids"][-1] == "https://animekhor.org/against-the-gods-season-2-episode-25-55/"


def test_history_provider_refresh_reuses_wordpress_title_page(monkeypatch):
    from media_cli.domain import history as history_domain
    from media_cli.services import catalog

    title_url = "https://animekhor.org/anime/against-the-gods-season-2/"
    calls = []
    page = """
    <div class="spe">
      <span>Status: Ongoing</span>
      <span>Type: Donghua</span>
    </div>
    <div class="eplister"><ul>
      <li>
        <a href="https://animekhor.org/against-the-gods-season-2-episode-25-55/">
          <div class="epl-num">25 [55]</div>
          <div class="epl-title">Against the Gods Season 2 Episode 25 [55] English Sub</div>
          <div class="epl-date">September 17, 2026</div>
        </a>
      </li>
    </ul></div>
    """

    def fetch(url):
        calls.append(url)
        return page

    provider = AnimeKhorProvider(fetch=fetch)
    provider.metadata = {"name": "AnimeKhor"}
    provider.domains = ["https://animekhor.org"]
    monkeypatch.setattr(catalog, "_provider_for_title", lambda show: provider)
    monkeypatch.setattr("media_cli.core.enrichment.enrich_show_if_missing", lambda show: False)

    entry = {
        "show": {
            "_id": title_url,
            "_provider": "animekhor",
            "name": "Against the Gods Season 2",
            "availableEpisodes": {"sub": 24},
        },
        "episode": "24",
        "translation_type": "sub",
        "was_caught_up": True,
    }

    assert history_domain.refresh_history_entry_provider_catalog(entry) is True
    assert calls == [title_url]
    assert entry["show"]["availableEpisodes"]["sub"] == 25
    assert entry["has_new_release"] is True
