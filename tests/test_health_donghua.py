"""Donghua diagnostics use native IDs, never fabricated anime tracker IDs."""

def test_donghua_sample_without_mal_id_reaches_native_resolution():
    from media_cli.health.catalogue import check_catalogue
    from media_cli.domain.health_report import prepare_health_evidence
    seen=[]
    report=check_catalogue('animexin',[{'title':'Renegade Immortal','year':2023}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'name':'Renegade Immortal','airedStart':{'year':2023},
            'provider_id':'https://animexin.dev/renegade-immortal/'}]},
        details_query=lambda *a:{'status':'ok','details':[{'status':'passed','reason':'Usable metadata'}]},
        episode_query=lambda *a:{'status':'ok','ids':['https://animexin.dev/renegade-immortal-episode-1/']},
        resolution_query=lambda *a:seen.append(a) or {'status':'ok','mirrors':[]})
    assert seen and seen[0][1]['title_id']=='https://animexin.dev/renegade-immortal/'
    assert seen[0][1].get('mal_id') is None
    saved=prepare_health_evidence(report)
    assert all('mal_id' not in s for s in saved['providers'][0]['stages'])


def test_donghua_foreign_or_unsafe_ids_are_rejected():
    from media_cli.health.profiles import valid_title_id,valid_episode_id
    assert not valid_title_id('animexin','https://evil.example/series/')
    assert not valid_title_id('animexin','https://animexin.dev/../private')
    assert not valid_episode_id('animexin','https://evil.example/episode/','https://animexin.dev/show/')


def test_donghua_worker_samples_one_returned_source_using_existing_resolver(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from media_cli import providers
    from media_cli.health import search_worker
    from media_cli.media import resolver
    descriptors=[{'sourceName':name,'type':'embed','sourceUrl':'https://video.example/embed'} for name in ('First','Second')]
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'animexin':lambda:SimpleNamespace(
        episode_sources=lambda *a:{'episode':{'sourceUrls':descriptors}})})
    seen=[]
    monkeypatch.setattr(resolver,'resolve_source',lambda d,**kw:seen.append(d) or [
        {'link':'https://video.example/stream.m3u8','type':'hls','resolution':'1080p'}])
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'animexin','operation':'resolve',
        'value':{'title_id':'https://animexin.dev/show/','episode':'https://animexin.dev/show-episode-1/','sampling_seed':42}})))
    assert search_worker.main()==0
    result=json.loads(capsys.readouterr().out)
    assert len(seen)==1 and result['source_count']==2 and result['source']==seen[0]['sourceName']


def test_encoded_traversal_is_not_a_native_title_id():
    from media_cli.health.profiles import valid_title_id
    assert not valid_title_id('animexin','https://animexin.dev/%2e%2e/private')


def test_animexin_media_checks_allow_only_verified_media_services():
    from media_cli.health.profiles import PROFILES
    from media_cli.health.http_media import media_destination_allowed
    assert 'mpv' in PROFILES['animexin']['levels']
    assert media_destination_allowed('animexin','https://vod3.cf.dmcdn.net/video.m3u8')
    assert not media_destination_allowed('animexin','https://vod3.cf.dmcdn.net.evil.example/video')


def test_lucifer_uses_its_own_native_domain_and_shared_category_samples():
    from media_cli.health.profiles import valid_title_id,PROFILES
    from media_cli.health.catalogue import HEALTH_SAMPLES
    assert valid_title_id('lucifer','https://luciferdonghua.in/show/')
    assert not valid_title_id('lucifer','https://animexin.dev/show/')
    assert all('mal_id' not in sample for sample in HEALTH_SAMPLES['lucifer'])
    assert PROFILES['lucifer']['source_limit']==1


def test_animekhor_native_domain_is_isolated():
    from media_cli.health.profiles import valid_title_id,PROFILES
    from media_cli.health.catalogue import HEALTH_SAMPLES
    assert valid_title_id('animekhor','https://animekhor.org/show/')
    assert not valid_title_id('animekhor','https://luciferdonghua.in/show/')
    assert len(HEALTH_SAMPLES['animekhor'])==10 and PROFILES['animekhor']['source_limit']==1


def test_dailymotion_native_subtitle_route_is_scoped_to_captions():
    from media_cli.health.http_media import media_destination_allowed
    assert media_destination_allowed('animexin','https://www.dailymotion.com/cdn/subtitle/video/example')
    assert not media_destination_allowed('animexin','https://www.dailymotion.com/cdn/admin/private')
    assert not media_destination_allowed('animexin','https://www.dailymotion.com.evil.example/cdn/subtitle/video/example')
