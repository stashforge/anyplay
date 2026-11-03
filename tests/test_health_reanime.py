"""Provider slugs and AniList IDs are distinct identity namespaces."""
import pytest


def test_reanime_health_command_is_supported():
    from media_cli.cli.args import parse_cli_args
    assert parse_cli_args(['health','reanime','--seed','42'])[0].health_action=='reanime'


def test_reanime_uses_anilist_match_and_returned_slug_for_details_and_episodes():
    from media_cli.health.catalogue import check_catalogue
    calls=[]
    def lookup(pid,ref):
        calls.append(ref)
        assert pid=='reanime' and ref=={'title_id':'demon-slayer','mal_id':38000,'anilist_id':101922}
        return {'status':'ok','ids':['1']}
    report=check_catalogue('reanime',[{'title':'Demon Slayer','year':2019,'mal_id':38000,'anilist_id':101922}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'anilistId':101922,'provider_id':'demon-slayer'}]},
        details_query=lambda pid,ref: (lookup(pid,ref) and {'status':'ok','details':[{'status':'passed','reason':'Usable metadata'}]}),
        episode_query=lookup)
    assert len(calls)==2 and report['providers'][0]['name']=='Re:ANIME'
    assert any(s['name']=='title_identity' and s['status']=='passed' for s in report['providers'][0]['stages'])


@pytest.mark.parametrize('row',[{'anilistId':38000,'provider_id':'wrong'}, {'anilistId':101922,'malId':20,'provider_id':'wrong'}])
def test_wrong_or_contradictory_tracker_ids_are_not_used(row):
    from media_cli.health.catalogue import check_catalogue
    report=check_catalogue('reanime',[{'title':'Title','year':2019,'mal_id':38000,'anilist_id':101922}],seed=42,
        query=lambda *a:{'status':'ok','results':[row]},details_query=lambda *a:pytest.fail('Wrong identity used'))
    assert not any(s['name']=='title_identity' and s['status']=='passed' for s in report['providers'][0]['stages'])


def test_worker_uses_native_slug_without_anilist_conversion(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli import providers
    from media_cli.health import search_worker
    instance=SimpleNamespace(get_title=Mock(return_value={'name':'Title','anilist_id':101922}))
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'reanime':lambda:instance})
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'reanime','operation':'details',
        'value':{'title_id':'demon-slayer','mal_id':38000,'anilist_id':101922}})))
    assert search_worker.main()==0
    instance.get_title.assert_called_once_with('demon-slayer')
    assert json.loads(capsys.readouterr().out)['details'][0]['status']=='passed'


@pytest.mark.parametrize('title_id',['../outside','https://example.com','slug?redirect=secret','slug/path','bad\nslug'])
def test_bad_slugs_are_rejected_before_lookup(monkeypatch,title_id):
    import io,json
    from types import SimpleNamespace
    from media_cli import providers
    from media_cli.health import search_worker
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'reanime':lambda:SimpleNamespace(get_title=lambda *a:pytest.fail('Bad slug used'))})
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'reanime','operation':'details',
        'value':{'title_id':title_id,'mal_id':38000,'anilist_id':101922}})))
    assert search_worker.main()==2


def test_raw_anilist_alias_is_checked_by_shared_details_assessor():
    from media_cli.health.details import assess_details
    result=assess_details({'name':'Title','anilist_id':101922},38000,expected_anilist_id=20)
    assert result['status']=='failed'


@pytest.mark.parametrize('url,expected',[
 ('https://fetch8.flixcloud.cc/download/file?token=private',True),
 ('https://flixcloud.cc/video/1080.m3u8',True),
 ('https://enc-dec.app/api/parse-flixcloud?url=private',True),
 ('https://enc-dec.app/api/dec-flixcloud',False),
 ('https://flixcloud.cc.evil.example/video',False),
 ('http://flixcloud.cc/video',False)])
def test_reanime_http_destinations_are_scoped(url,expected):
    from media_cli.health.http_media import media_destination_allowed
    assert media_destination_allowed('reanime',url) is expected


def test_silent_decode_preserves_existing_flixcloud_proxy_options(monkeypatch):
    from media_cli.health import mpv_media
    from media_cli.services import requirements
    from media_cli.media import local_proxy
    monkeypatch.setattr(requirements,'require_executable',lambda *a:'/usr/bin/mpv')
    seen=[];server=object()
    monkeypatch.setattr(local_proxy,'start_local_proxy',lambda *a,**kw:seen.append(kw) or ('http://127.0.0.1/private',server))
    monkeypatch.setattr(local_proxy,'stop_local_proxy',lambda *a:None)
    monkeypatch.setattr(mpv_media,'run_sample',lambda *a,**kw:[])
    mpv_media.probe_entries([{'type':'hls','streamUrl':'https://flixcloud.cc/v','resolution':'1080p',
                             'flixcloud_w_payload':'PRIVATE','flixcloud_xor_mask':[1,2,3]}])
    assert seen[0]['flixcloud_w_payload']=='PRIVATE' and seen[0]['flixcloud_xor_mask']==[1,2,3]


def test_missing_channels_are_not_reported_as_zero_sources():
    from media_cli.health.catalogue import check_catalogue
    report=check_catalogue('reanime',[{'title':'Title','year':2019,'mal_id':38000,'anilist_id':101922}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'anilistId':101922,'provider_id':'title-slug'}]},
        episode_query=lambda *a:{'status':'ok','ids':['1']},resolution_query=lambda *a:{'status':'ok','mirrors':[
            {'resolution':'1080p','type':'mp4','host':'fetch8.flixcloud.cc','source':'HD-1','separate_audio':False,'subtitle_count':0,'headers_present':True}], 'unresolved_count':2})
    stage=next(s for s in report['providers'][0]['stages'] if s['name']=='source_resolution')
    assert stage['mirror_count']==1 and stage['unresolved_count']==2
    assert 'channel_count' not in stage


def test_missing_tracker_id_uses_shared_confident_metadata_match():
    from media_cli.health.profiles import matching_rows, verified_locator
    sample={'title':'Naruto','year':2002,'mal_id':20,'anilist_id':20}
    row={'name':'Naruto','airedStart':{'year':2002},'anilistId':0,'provider_id':'naruto-original'}
    matches=matching_rows('reanime',sample,[row])
    assert len(matches)==1 and matches[0]['match_method']=='metadata'
    assert verified_locator('reanime',sample,matches)=={'provider_title_id':'naruto-original'}
    assert row.get('match_method') is None


@pytest.mark.parametrize('changes',[
    {'anilistId':999}, {'malId':999}, {'airedStart':{'year':2020}}, {'airedStart':None}])
def test_metadata_fallback_rejects_conflicting_ids_or_missing_release_evidence(changes):
    from media_cli.health.profiles import matching_rows
    row={'name':'Naruto','airedStart':{'year':2002},'anilistId':0,'provider_id':'naruto-original',**changes}
    assert matching_rows('reanime',{'title':'Naruto','year':2002,'mal_id':20,'anilist_id':20},[row])==[]


def test_metadata_fallback_does_not_choose_ambiguous_results():
    from media_cli.health.profiles import matching_rows
    rows=[{'name':'Naruto','airedStart':{'year':2002},'anilistId':0,'provider_id':slug}
          for slug in ('first','second')]
    assert matching_rows('reanime',{'title':'Naruto','year':2002,'mal_id':20,'anilist_id':20},rows)==[]


@pytest.mark.parametrize('level',['http','mpv'])
def test_reanime_exposes_shared_media_checks(level):
    from media_cli.health.profiles import PROFILES
    assert level in PROFILES['reanime']['levels']


@pytest.mark.parametrize('url,expected',[
 ('https://dl.spacedl.top/file',True),
 ('https://vault-95.cobaltcdn.top/sub.vtt',True),
 ('https://vault-93.soniccdn.top/sub.vtt',True),
 ('https://vault-95.rundowncdn.top/sub.vtt',True),
 ('https://vault-95.cobaltcdn.top.evil.example/sub.vtt',False),
 ('https://anything.cobaltcdn.top/sub.vtt',False),
 ('https://enc-dec.app/api/dec-flixcloud',False)])
def test_reanime_observed_cdn_routes_are_narrowly_allowed(url,expected):
    from media_cli.health.http_media import media_destination_allowed
    assert media_destination_allowed('reanime',url) is expected


def test_saved_report_labels_metadata_match_without_claiming_provider_returned_id():
    from media_cli.health.catalogue import check_catalogue
    from media_cli.domain.health_report import prepare_health_evidence, format_health_report
    report=check_catalogue('reanime',[{'title':'Naruto','year':2002,'mal_id':20,'anilist_id':20}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'name':'Naruto','anilistId':0,'airedStart':{'year':2002},'provider_id':'naruto-slug'}]})
    saved=prepare_health_evidence(report)
    stage=next(s for s in saved['providers'][0]['stages'] if s['name']=='title_identity')
    assert stage['match_method']=='metadata' and 'ID missing' in stage['reason']
    assert 'Match method: metadata' in format_health_report({**saved, 'run_id':'test-metadata'})
