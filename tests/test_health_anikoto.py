"""External metadata must not masquerade as native catalogue evidence."""
import pytest


def run_health(**kwargs):
    from media_cli.health.catalogue import check_catalogue
    return check_catalogue('anikoto', [{'title':'Demon Slayer','year':2019,'mal_id':38000,'anilist_id':101922}],
        seed=42, query=lambda *a: {'status':'ok','results':[{'name':'Demon Slayer','malId':38000,
            'anilistId':101922,'provider_id':'101922'}]}, **kwargs)


def test_external_metadata_is_labeled_in_saved_report():
    from media_cli.domain.health_report import prepare_health_evidence
    report=run_health(details_query=lambda *a:{'status':'ok','details':[{'status':'passed','reason':'Usable metadata'}]})
    stages=prepare_health_evidence(report)['providers'][0]['stages']
    for name in ('search','title_identity','title_details'):
        stage=next(s for s in stages if s['name']==name)
        assert stage['evidence_origin']=='AniList'
        assert 'AniList' in stage['reason']


def test_uncertain_catalogue_does_not_block_independent_stream_resolution():
    calls=[]
    def resolve(provider,reference,episode):
        calls.append((provider,reference,episode))
        return {'status':'ok','mirrors':[{'resolution':'1080p','type':'hls','host':'media.example',
                   'separate_audio':False,'subtitle_count':1,'headers_present':True}]}
    report=run_health(episode_query=lambda *a:{'status':'ok','ids':['1','2']},resolution_query=resolve)
    stages=report['providers'][0]['stages']
    catalogue=next(s for s in stages if s['name']=='episode_catalogue')
    assert catalogue['status']=='inconclusive' and catalogue['episode_count']==2
    assert catalogue['evidence_origin']=='site_or_metadata'
    assert 'origin not exposed' in catalogue['reason']
    assert calls and calls[0][1]['title_id']=='101922' and calls[0][2] in ('1','2')
    resolution=next(s for s in stages if s['name']=='source_resolution')
    assert resolution['status']=='passed' and resolution.get('evidence_origin')!='AniList'
    assert report['providers'][0]['status']=='inconclusive'


def test_anilist_id_must_match_anikoto_lookup_id():
    from media_cli.health.profiles import verified_locator
    assert verified_locator('anikoto',{'mal_id':38000,'anilist_id':101922},
                            [{'provider_id':'38000','anilistId':101922}])=={}


def test_health_command_accepts_anikoto():
    from media_cli.cli.args import parse_cli_args
    assert parse_cli_args(['health','anikoto','--verify','extraction'])[0].health_action=='anikoto'


def test_worker_resolves_using_verified_anilist_id_without_modifying_provider(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli import providers
    from media_cli.health import search_worker
    instance=SimpleNamespace(episode_sources=Mock(return_value={'episode':{'sourceUrls':[
        {'type':'hls','resolution':'1080p','link':'https://media.example/stream.m3u8',
         'subtitles':[{'url':'https://media.example/en.vtt','label':'English'}]}]}}))
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'anikoto':lambda:instance})
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'anikoto','operation':'resolve',
        'value':{'title_id':'101922','mal_id':38000,'anilist_id':101922,'episode':'1'}})))
    assert search_worker.main()==0
    instance.episode_sources.assert_called_once_with('101922','1','sub')
    result=json.loads(capsys.readouterr().out)
    assert result['mirrors'][0]['subtitle_count']==1
    assert 'stream.m3u8' not in json.dumps(result)


def test_failed_catalogue_keeps_actual_failure_and_does_not_fabricate_stream_check():
    report=run_health(episode_query=lambda *a:{'status':'timeout','reason':'Request budget exceeded'},
                      resolution_query=lambda *a:pytest.fail('No returned episode to sample'))
    stages=report['providers'][0]['stages']
    catalogue=next(s for s in stages if s['name']=='episode_catalogue')
    assert catalogue['status']=='inconclusive' and catalogue['reason']=='Request budget exceeded'
    assert not any(s['name']=='source_resolution' and s['status']=='passed' for s in stages)


def test_worker_importing_display_still_emits_only_json_at_exit():
    import subprocess,sys,json
    script='''
import io,json,runpy,sys
from types import SimpleNamespace
from media_cli import providers

def resolve(*args):
    import media_cli.ui.display
    return {'episode':{'sourceUrls':[]}}
providers.PROVIDER_FACTORIES={'anikoto':lambda:SimpleNamespace(episode_sources=resolve)}
sys.stdin=io.StringIO(json.dumps({'provider':'anikoto','operation':'resolve','value':
    {'title_id':'101922','mal_id':38000,'anilist_id':101922,'episode':'1'}}))
runpy.run_module('media_cli.health.search_worker',run_name='__main__')
'''
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0
    assert result.stdout.strip().endswith('}'), 'Unowned terminal cleanup corrupted worker output'
    assert json.loads(result.stdout)['mirrors']==[]
    assert '\x1b' not in result.stdout+result.stderr


@pytest.mark.parametrize('level',['http','mpv'])
def test_anikoto_exposes_shared_media_verification(level):
    from media_cli.health.profiles import PROFILES
    assert level in PROFILES['anikoto']['levels']


@pytest.mark.parametrize('url,expected',[
 ('https://megap.shiora.top/video.m3u8',True),
 ('https://megap.shiora.top.evil.example/video.m3u8',False),
 ('http://megap.shiora.top/video.m3u8',False),
 ('https://127.0.0.1/video.m3u8',False)])
def test_anikoto_media_policy_is_scoped(url,expected):
    from media_cli.health.http_media import media_destination_allowed
    assert media_destination_allowed('anikoto',url) is expected


@pytest.mark.parametrize('url,expected',[
 ('https://p19-ad-site-sign-sg.tiktokcdn.com/video',True),
 ('https://de3bv.eclipseharbor.world/en.vtt',True),
 ('https://yaf6t.emeralddrift.world/en.vtt',True),
 ('https://de3bv.eclipseharbor.world.evil.example/en.vtt',False)])
def test_verified_anikoto_cdn_hosts_are_allowed(url,expected):
    from media_cli.health.http_media import media_destination_allowed
    assert media_destination_allowed('anikoto',url) is expected
