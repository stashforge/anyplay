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
