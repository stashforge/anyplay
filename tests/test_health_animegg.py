"""Do not invent tracker identity or release metadata absent from AnimeGG."""

def test_title_only_search_remains_uncertain_without_fetching_guessed_identity():
    from media_cli.health.catalogue import check_catalogue
    report=check_catalogue('animegg',[{'title':'Naruto','year':2002,'mal_id':20}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'name':'Naruto','provider_id':'naruto'}]})
    stages=report['providers'][0]['stages']
    assert next(s for s in stages if s['name']=='search')['status']=='passed'
    assert next(s for s in stages if s['name']=='title_identity')['status']=='inconclusive'


def test_unique_title_candidate_can_be_probed_without_claiming_verified_identity():
    from media_cli.health.catalogue import check_catalogue
    seen=[]
    report=check_catalogue('animegg',[{'title':'Naruto','year':2002,'mal_id':20}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'name':'Naruto','provider_id':'naruto'}]},
        episode_query=lambda *a:{'status':'ok','ids':['naruto-episode-1']},
        resolution_query=lambda *a:seen.append(a) or {'status':'ok','mirrors':[]})
    identity=next(s for s in report['providers'][0]['stages'] if s['name']=='title_identity')
    assert identity['status']=='inconclusive' and identity['match_method']=='title_candidate'
    assert seen and seen[0][1]['title_id']=='naruto'
    assert report['providers'][0]['status']=='inconclusive'


def test_animegg_media_checks_are_scoped_to_existing_media_host():
    from media_cli.health.profiles import PROFILES
    from media_cli.health.http_media import media_destination_allowed
    assert 'mpv' in PROFILES['animegg']['levels']
    assert media_destination_allowed('animegg','https://animegg.org/video.mp4')
    assert not media_destination_allowed('animegg','https://animegg.org.evil.example/video.mp4')


def test_legacy_animegg_media_redirect_uses_exact_public_host_only():
    from media_cli.health.http_media import media_destination_allowed
    assert media_destination_allowed('animegg','https://www.animegg.org/video.mp4')
    assert media_destination_allowed('animegg','http://www.animegg.org/video.mp4')
    assert not media_destination_allowed('animegg','http://www.animegg.org:8080/video.mp4')
    assert not media_destination_allowed('animegg','http://www.animegg.org.evil.example/video.mp4')
    assert not media_destination_allowed('anipm','http://media.settlar.io/video.mp4')


def test_observed_animegg_cdn_port_does_not_allow_other_hosts_or_ports():
    from media_cli.health.http_media import media_destination_allowed
    assert media_destination_allowed('animegg','https://vidcache.net:8161/video.mp4')
    assert not media_destination_allowed('animegg','http://vidcache.net:8161/video.mp4')
    assert not media_destination_allowed('animegg','https://vidcache.net:22/video.mp4')
    assert not media_destination_allowed('animegg','https://vidcache.net.evil.example:8161/video.mp4')
    assert not media_destination_allowed('animegg','https://animegg.org:8161/video.mp4')


def test_observed_animegg_stream_nodes_are_scoped_by_host_pattern_and_port():
    from media_cli.health.http_media import media_destination_allowed
    assert media_destination_allowed('animegg','https://s113.vidcache.net:8166/video.mp4')
    assert not media_destination_allowed('animegg','https://s113.vidcache.net:22/video.mp4')
    assert not media_destination_allowed('animegg','https://admin.vidcache.net:8166/video.mp4')
    assert not media_destination_allowed('animegg','https://s113.vidcache.net.evil.example:8166/video.mp4')
