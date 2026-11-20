from concurrent.futures import ThreadPoolExecutor
from media_cli.providers.shared import wordpress
from media_cli.providers.animexin import AnimeXinProvider


def test_title_and_catalog_share_tree_only_within_refresh(monkeypatch):
    markup = '<h1 class="entry-title">Example</h1><div class="eplister"><ul><li><a href="https://animexin.dev/example-episode-1/"><span>1</span></a></li></ul></div>'
    original = wordpress.BeautifulSoup
    calls = []
    def parse(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(wordpress, "BeautifulSoup", parse)
    provider = AnimeXinProvider(fetch=lambda _: markup)
    with provider.page_parsing_cache():
        title = provider.get_title('https://animexin.dev/example/')
        catalog = provider.episode_catalog('https://animexin.dev/example/')
    assert title['name'] == 'Example'
    assert catalog['ids']
    assert calls == [markup]
    with provider.page_parsing_cache():
        provider.get_title('https://animexin.dev/example/')
    assert len(calls) == 2


def test_cache_is_scoped_bounded_and_not_shared_between_threads():
    with wordpress.cached_page_parsing():
        first = wordpress._parse_page('<h1>same</h1>')
        assert first is wordpress._parse_page('<h1>same</h1>')
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(wordpress._parse_page, '<h1>same</h1>').result() is not first
        for n in range(8):
            wordpress._parse_page(f'<p>{n}</p>')
        assert len(wordpress._page_trees.get()) == 4
    assert wordpress._page_trees.get() is None
