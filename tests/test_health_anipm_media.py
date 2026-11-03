"""AniPM media checks reuse samplers and leave its provider unchanged."""
import pytest


@pytest.mark.parametrize('url,expected',[
 ('https://media.settlar.io/path?token=private',True),('http://media.settlar.io/path',False),
 ('https://media.settlar.io.evil.test/path',False),('https://other.example/path',False),
 ('https://user:password@media.settlar.io/path',False),('https://media.settlar.io:444/path',False),
 ('https://media.settlar.io/path#part',False)])
def test_anipm_http_policy_is_scoped(url,expected):
    from media_cli.health.http_media import media_destination_allowed
    assert media_destination_allowed('anipm',url) is expected


def test_supplied_http_policy_is_used_without_replacing_senshi(monkeypatch):
    from media_cli.health import http_media
    from media_cli.services import requirements
    from types import SimpleNamespace
    monkeypatch.setattr(requirements,'require_python',lambda *a:SimpleNamespace(Session=lambda **kw:SimpleNamespace()))
    client=http_media.HTTPFetcher(allowed=lambda url:url=='https://media.settlar.io/test')
    assert client.allowed('https://media.settlar.io/test')
    assert not client.allowed('https://s-1.bcdn1.se/test')
    assert http_media.media_destination_allowed('senshi','https://s-1.bcdn1.se/test')
    assert not http_media.media_destination_allowed('senshi','https://media.settlar.io/test')


def test_hls_quality_hint_is_honored_and_not_forwarded():
    from media_cli.health import http_media as m
    root='https://media.settlar.io/master.m3u8'
    bodies={root:b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=640x360\n360.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2000,RESOLUTION=1920x1080\n1080.m3u8\n',
            'https://media.settlar.io/360.m3u8':b'#EXTM3U\n#EXTINF:4,\na.ts\n',
            'https://media.settlar.io/a.ts':b'\x47sample'}
    seen=[]
    def fetch(url,headers,limit,sample=False):
        seen.append(url);return m.HTTPResult(url,200,bodies[url])
    result=m.probe_entry({'type':'hls','streamUrl':root+'?hls_res=360'},fetch)
    assert result[0]['status']=='passed'
    assert seen==[root,'https://media.settlar.io/360.m3u8','https://media.settlar.io/a.ts']


def test_anipm_http_worker_resolves_once_then_samples(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli import providers
    from media_cli.health import search_worker,http_media
    entries=[{'type':'hls','streamUrl':'https://media.settlar.io/private?token=PRIVATE','channel':'sub'}]
    instance=SimpleNamespace(episode_sources=Mock(return_value={'episode':{'sourceUrls':entries}}))
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'anipm':lambda:instance})
    seen=[]
    def probe(rows,**kwargs):
        seen.append(rows)
        assert kwargs['allowed']('https://media.settlar.io/segment')
        assert not kwargs['allowed']('https://other.example/segment')
        return [{'track':'video','status':'passed','reason':'Bytes only','bytes_sampled':100}]
    monkeypatch.setattr(http_media,'probe_entries',probe)
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'anipm','operation':'resolve_http',
        'value':{'title_id':'101922','mal_id':38000,'episode':'1'}})))
    assert search_worker.main()==0
    output=capsys.readouterr().out
    assert instance.episode_sources.call_count==1 and seen==[entries]
    assert 'PRIVATE' not in output and json.loads(output)['media_checks'][0]['bytes_sampled']==100


def test_combined_resolution_and_http_evidence_are_saved_separately(tmp_path):
    from media_cli.health import catalogue as c
    from media_cli.state.health_reports import save_health_report,read_saved_report
    report=c.check_catalogue('anipm',[{'title':'Title','mal_id':38000,'year':2019}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'malId':38000,'anilistId':101922,'provider_id':'101922'}]},
        episode_query=lambda *a:{'status':'ok','ids':['1']},http_checks=True,
        resolution_query=lambda *a:{'status':'ok','mirrors':[{'resolution':'1080p','type':'hls','host':'media.settlar.io',
            'channel':'sub','separate_audio':False,'headers_present':True,'subtitle_count':1}],
            'extraction_elapsed_seconds':2.0,'media_checks':[{'track':'video','status':'passed','reason':'sample','bytes_sampled':100}]})
    rows=report['providers'][0]['stages']
    assert report['verification']=='http'
    assert next(s for s in rows if s['name']=='source_resolution')['elapsed_seconds']==2.0
    assert next(s for s in rows if s['name']=='media_access')['bytes_sampled']==100
    rid=save_health_report(tmp_path,report)
    assert 'Bytes sampled: 100' in read_saved_report(tmp_path,rid)


def test_anipm_decode_worker_reuses_same_resolved_entries(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli import providers
    from media_cli.health import search_worker,http_media,mpv_media
    entries=[{'type':'hls','resolution':'1080p','streamUrl':'https://media.settlar.io/private?token=PRIVATE','channel':'sub'}]
    instance=SimpleNamespace(episode_sources=Mock(return_value={'episode':{'sourceUrls':entries}}))
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'anipm':lambda:instance})
    seen=[]
    monkeypatch.setattr(http_media,'probe_entries',lambda rows,**kw:seen.append(('http',rows)) or [])
    monkeypatch.setattr(mpv_media,'probe_entries',lambda rows:seen.append(('mpv',rows)) or [{'track':'audio','status':'passed','reason':'Decoded','decoded_samples':100}])
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'anipm','operation':'resolve_mpv',
        'value':{'title_id':'101922','mal_id':38000,'episode':'1'}})))
    assert search_worker.main()==0
    output=capsys.readouterr().out
    assert instance.episode_sources.call_count==1
    assert [name for name,rows in seen]==['http','mpv'] and seen[0][1]==seen[1][1]==entries
    assert 'PRIVATE' not in output and json.loads(output)['playback_checks'][0]['decoded_samples']==100


def test_combined_playback_local_issue_preserves_resolution_and_http():
    from media_cli.health import catalogue as c
    report=c.check_catalogue('anipm',[{'title':'Title','mal_id':38000,'year':2019}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'malId':38000,'anilistId':101922,'provider_id':'101922'}]},
        episode_query=lambda *a:{'status':'ok','ids':['1']},http_checks=True,playback_checks=True,
        resolution_query=lambda *a:{'status':'ok','mirrors':[{'resolution':'1080p','type':'hls','host':'media.settlar.io'}],
            'media_checks':[{'track':'video','status':'passed','reason':'sample'}],
            'playback_failure':{'status':'missing_dependency','requirement_kind':'executable','issue_scope':'local','reason':'mpv missing'}})
    assert report['status']=='incomplete' and report['verification']=='mpv'
    rows=report['providers'][0]['stages']
    assert next(s for s in rows if s['name']=='source_resolution')['status']=='passed'
    assert next(s for s in rows if s['name']=='media_access')['status']=='passed'
    assert next(s for s in rows if s['name']=='playback')['issue_scope']=='local'


@pytest.mark.parametrize('level,timeout',[('http',125),('mpv',170)])
def test_ani_pm_media_worker_budgets(monkeypatch,level,timeout):
    import json
    from types import SimpleNamespace
    from media_cli.health import catalogue as c
    seen=[]
    def execute(command,payload,**kw):
        seen.append(kw['timeout']);return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'ok','mirrors':[]}),stderr='')
    monkeypatch.setattr(c,'_run_extraction_worker',execute)
    getattr(c,'resolve_'+level+'_isolated')('anipm',{'title_id':'101922','mal_id':38000},'1')
    assert seen==[timeout]
