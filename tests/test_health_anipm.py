"""A second adapter must reuse health without confusing provider and tracker IDs."""
import pytest


def test_anipm_command_seed_and_verification_levels():
    from media_cli.cli.args import parse_cli_args
    args,_=parse_cli_args(['health','anipm','--seed','42'])
    assert args.health_action=='anipm'
    assert parse_cli_args(['health','anipm','--verify','extraction'])[0].health_verify=='extraction'
    for level in ('http','mpv'):
        assert parse_cli_args(['health','anipm','--verify',level])[0].health_verify==level
    with pytest.raises(SystemExit):parse_cli_args(['health','anipm','extra'])


def test_details_uses_verified_provider_id_not_mal_id_and_saves_it(tmp_path):
    from media_cli.health import catalogue as c
    calls=[]
    def details(pid,value):
        calls.append((pid,value))
        assert value['title_id']=='101922' and value['mal_id']==38000
        return {'status':'ok','details':[{'status':'passed','reason':'Usable details','present_fields':['title','mal_id','anilist_id'],'missing_fields':[]}]}
    report=c.check_catalogue('anipm',[{'title':'Demon Slayer','year':2019,'mal_id':38000}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'malId':38000,'anilistId':101922,'provider_id':'101922'}]},details_query=details)
    assert len(calls)==1 and report['verification']=='catalogue'
    assert report['providers'][0]['name']=='AniPM'
    from media_cli.state.health_reports import save_health_report,read_saved_report
    rid=save_health_report(tmp_path,report)
    text=read_saved_report(tmp_path,rid)
    assert '101922' in text and '38000' in text
    assert any(s['name']=='episode_catalogue' and s['status']=='not_verified' for s in report['providers'][0]['stages'])


def test_anipm_wrong_mal_does_not_reach_details():
    from media_cli.health import catalogue as c
    report=c.check_catalogue('anipm',[{'title':'Demon Slayer','year':2019,'mal_id':38000}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'malId':20,'anilistId':101922,'provider_id':'101922'}]},
        details_query=lambda *a:pytest.fail('Wrong identity used'))
    assert all(s['status']!='passed' for s in report['providers'][0]['stages'] if s['name']=='title_identity')


def test_anipm_missing_provider_id_is_not_guessed_from_mal():
    from media_cli.health import catalogue as c
    report=c.check_catalogue('anipm',[{'title':'Demon Slayer','year':2019,'mal_id':38000}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'malId':38000}]},
        details_query=lambda *a:pytest.fail('Must not guess AniList ID from MAL'))
    assert any(s['name']=='title_details' and s['status']=='not_verified' for s in report['providers'][0]['stages'])


def test_detail_anilist_contradiction_is_not_hidden_by_correct_mal():
    from media_cli.health.details import assess_details
    result=assess_details({'name':'Demon Slayer','malId':38000,'anilistId':20},38000,expected_anilist_id=101922)
    assert result['status']=='failed' and 'AniList' in result['reason']


def test_worker_uses_only_requested_provider_and_exact_returned_id(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli import providers
    from media_cli.health import search_worker
    instance=SimpleNamespace(get_title=Mock(return_value={'name':'Title','malId':38000,'anilistId':101922}))
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'anipm':lambda:instance,'senshi':lambda:pytest.fail('Wrong provider')})
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'anipm','operation':'details',
        'value':{'title_id':'101922','mal_id':38000,'anilist_id':101922}})))
    assert search_worker.main()==0
    instance.get_title.assert_called_once_with('101922')
    assert json.loads(capsys.readouterr().out)['details'][0]['status']=='passed'


def test_worker_rejects_anipm_media_before_importing_provider(monkeypatch):
    import io,json
    from media_cli.health import search_worker
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'anipm','operation':'http','value':{}})))
    assert search_worker.main()==2


def test_nested_normalized_season_year_is_recognized():
    from media_cli.health.details import assess_details
    result=assess_details({'name':'Title','season':{'year':2023}},52034)
    assert 'year' in result['present_fields']


def test_worker_reads_normalized_anilist_spelling(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from media_cli import providers
    from media_cli.health import search_worker
    instance=SimpleNamespace(search=lambda *a:[{'name':'Title','malId':'38000','aniListId':'101922','_provider_id':'101922'}])
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'anipm':lambda:instance})
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'anipm','operation':'search','value':'Demon Slayer'})))
    assert search_worker.main()==0
    row=json.loads(capsys.readouterr().out)['results'][0]
    assert row['anilistId']=='101922' and row['provider_id']=='101922'


def test_normalized_anilist_id_is_checked_in_details():
    from media_cli.health.details import assess_details
    result=assess_details({'name':'Title','malId':38000,'aniListId':101922},38000,expected_anilist_id=20)
    assert result['status']=='failed' and 'anilist_id' in result['present_fields']


def test_ambiguous_provider_ids_are_not_silently_selected():
    from media_cli.health import catalogue as c
    report=c.check_catalogue('anipm',[{'title':'Title','year':2019,'mal_id':38000}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'malId':38000,'anilistId':101922,'provider_id':'101922'},
                                                {'malId':38000,'anilistId':20,'provider_id':'20'}]},
        details_query=lambda *a:pytest.fail('Ambiguous mapping used'))
    assert any(s['name']=='title_details' and s['status']=='not_verified' for s in report['providers'][0]['stages'])


def test_anipm_cli_uses_shared_runner_without_media_work(monkeypatch,tmp_path,capsys):
    from media_cli.health import catalogue
    from media_cli.cli import health
    def check(pid,samples,**kwargs):
        assert pid=='anipm' and len(samples)==10
        assert kwargs['details_query'] is catalogue.details_isolated
        assert kwargs['episode_query'] is catalogue.episodes_isolated
        assert kwargs['resolution_query'] is catalogue.resolve_http_isolated
        assert kwargs['http_checks'] is True
        assert not any(k in kwargs for k in ('source_query','extraction_query'))
        return {'started_at':'2026-10-29T10:00:00Z','status':'complete','verification':'catalogue','providers':[]}
    monkeypatch.setattr(catalogue,'check_catalogue',check)
    monkeypatch.setattr(health.health_reports,'reports_directory',lambda:tmp_path)
    assert health.handle_health_command('anipm',seed=42,plain=True)==0
    assert 'AniPM catalogue check' in capsys.readouterr().out
