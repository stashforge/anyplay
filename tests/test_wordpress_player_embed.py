from media_cli.providers.shared.wordpress import extract_embed_url, mirror_unavailable_reason


def test_countdown_does_not_resolve_invisible_ad():
    page = '<iframe src="https://site.test/crypto.html" style="opacity:0"></iframe><div id="pembed"><a class="tickcounter" data-type="countdown">Episode 35</a></div>'
    assert extract_embed_url('https://site.test', page) == ''
    assert 'countdown' in mirror_unavailable_reason(page)


def test_unavailable_player_does_not_resolve_other_frames():
    page = '<iframe src="https://site.test/embed-ad"></iframe><div class="player-embed">Video Not Available</div>'
    assert extract_embed_url('https://site.test', page) == ''


def test_real_player_meta_is_preserved():
    page = '<iframe src="https://site.test/crypto.html"></iframe><div id="pembed"><meta itemprop="embedUrl" content="https://www.dailymotion.com/embed/video/abc"></div>'
    assert 'dailymotion.com' in extract_embed_url('https://site.test', page)


def test_player_script_precedes_unrelated_frame():
    page = '<iframe src="https://site.test/ad"></iframe><div id="pembed"><script src="https://geo.dailymotion.com/player/x.js" data-video="abc"></script></div>'
    assert extract_embed_url('https://site.test', page) == 'https://www.dailymotion.com/video/abc'


def test_generic_hidden_frame_is_ignored():
    page = '<iframe src="https://site.test/ad" style="opacity: 0"></iframe><iframe src="https://ok.ru/videoembed/123"></iframe>'
    assert 'ok.ru' in extract_embed_url('https://site.test', page)
