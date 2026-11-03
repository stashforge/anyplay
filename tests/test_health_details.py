"""Details checking must not pass Senshi's placeholder or invent metadata."""
import importlib.util
import pytest


def assess(value,mid=1535):
    assert importlib.util.find_spec('media_cli.health.details'), 'details assessment missing'
    from media_cli.health.details import assess_details
    return assess_details(value,mid)


def test_usable_title_passes_with_optional_fields_missing():
    result=assess({'name':'Death Note','malId':1535})
    assert result['status']=='passed'
    assert 'title' in result['present_fields']
    assert 'cover' in result['missing_fields']


def test_placeholder_is_not_successful_metadata():
    result=assess({'name':'MAL ID: 1535','url':'https://myanimelist.net/anime/1535'})
    assert result['status']=='inconclusive'
    assert 'placeholder' in result['reason'].lower()
    assert 'title' in result['missing_fields']


@pytest.mark.parametrize('value',[None,[],{}, {'name':'Unknown'},{'name':42}])
def test_empty_or_malformed_metadata_does_not_pass(value):
    assert assess(value)['status']=='inconclusive'


def test_only_explicit_mal_id_conflicts_fail_not_provider_local_id():
    assert assess({'name':'Death Note','malId':20})['status']=='failed'
    assert assess({'name':'Death Note','_id':20})['status']=='inconclusive'


def test_field_checks_do_not_publish_raw_values_or_fetch_cover():
    result=assess({'name':'Secret title','thumbnail':'https://cdn.example/img?token=PRIVATE',
                  'description':'PRIVATE description','format':'TV','status':'Finished','year':2006,
                  'romajiName':'Desu Noto','malId':1535})
    assert set(result['present_fields'])=={'title','alternate_title','cover','description','format','status','year','mal_id'}
    assert 'PRIVATE' not in str(result) and 'Secret title' not in str(result)


def run(details,episodes=None):
    from media_cli.health import catalogue as c
    by_title={s['title']:s['mal_id'] for s in c.SENSHI_SAMPLES}
    return c.check_catalogue('senshi',c.SENSHI_SAMPLES,seed=42,
        query=lambda pid,t:{'status':'ok','results':[{'malId':by_title[t]}]},
        episode_query=episodes,details_query=details)


def test_details_reuses_episode_sample_and_saves_field_evidence(tmp_path):
    detail_ids=[];episode_ids=[]
    def details(pid,mid):
        detail_ids.append(mid)
        return {'status':'ok','details':[assess({'name':'Title','malId':mid},mid)]}
    report=run(details,lambda pid,mid:episode_ids.append(mid) or {'status':'ok','ids':['1']})
    assert len(detail_ids)==6 and detail_ids==episode_ids
    from media_cli.state.health_reports import save_health_report,read_saved_report
    rid=save_health_report(tmp_path,report)
    text=read_saved_report(tmp_path,rid)
    assert 'Missing fields: alternate_title' in text
    assert all(s['status']=='passed' for s in report['providers'][0]['stages'] if s['name']=='title_details')


def test_details_local_requirement_does_not_blame_provider():
    report=run(lambda *a:{'status':'missing_dependency','details':[],'issue_scope':'local','requirement_kind':'python','reason':'Module missing'})
    assert report['status']=='incomplete'
    assert report['providers'][0]['local_issues'][0]['stage']=='title_details'
    assert report['providers'][0]['status']=='inconclusive'


def test_cancel_keeps_completed_and_pending_details():
    calls=[]
    def details(*args):
        calls.append(args)
        if len(calls)==2:raise KeyboardInterrupt
        return {'status':'ok','details':[assess({'name':'Title'})]}
    report=run(details)
    rows=[s for s in report['providers'][0]['stages'] if s['name']=='title_details']
    assert report['status']=='cancelled'
    assert len(rows)==6 and sum(s['status']=='not_verified' for s in rows)==5


def test_worker_calls_adapter_without_external_enrichment_or_raw_payload(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli.health import search_worker
    from media_cli import providers
    instance=SimpleNamespace(get_title=Mock(return_value={'name':'MAL ID: 1535','url':'https://example.com/?token=PRIVATE'}))
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'senshi':lambda:instance})
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'senshi','operation':'details','value':1535})))
    assert search_worker.main()==0
    output=capsys.readouterr().out
    assert 'PRIVATE' not in output and 'example.com' not in output
    instance.get_title.assert_called_once_with('1535')
    assert json.loads(output)['details'][0]['status']=='inconclusive'


@pytest.mark.parametrize('outcome',[{'status':'timeout','details':[]}, {'status':'error','details':[]}, {'status':'ok','details':[]}])
def test_bad_request_response_is_separate_from_missing_fields(outcome):
    report=run(lambda *a:outcome)
    rows=[s for s in report['providers'][0]['stages'] if s['name']=='title_details']
    assert len(rows)==6 and all(s['status']=='inconclusive' for s in rows)
    assert all('missing_fields' not in s for s in rows)
