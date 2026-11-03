"""AniPM episodes reuse the shared sampling while retaining provider identity."""
import pytest


def run(episodes,rows=True):
    from media_cli.health import catalogue as c
    by_title={s['title']:s for s in c.ANIPM_SAMPLES}
    def search(pid,title):
        mid=by_title[title]['mal_id']
        return {'status':'ok','results':[{'malId':mid, **({'anilistId':mid+100000,'provider_id':str(mid+100000)} if rows else {})}]}
    return c.check_catalogue('anipm',c.ANIPM_SAMPLES,seed=42,query=search,episode_query=episodes)


def test_episode_checks_use_six_provider_ids_not_mal_and_save(tmp_path):
    calls=[]
    def episodes(pid,reference):
        calls.append(reference)
        assert pid=='anipm' and int(reference['title_id'])==reference['mal_id']+100000
        return {'status':'ok','ids':['1','2','2']}
    report=run(episodes)
    rows=[s for s in report['providers'][0]['stages'] if s['name']=='episode_catalogue']
    assert len(calls)==len(rows)==6
    assert {g:sum(s['age_group']==g for s in rows) for g in ('older','middle','recent')}=={'older':2,'middle':2,'recent':2}
    assert all(s['status']=='passed' and s['episode_count']==2 and s['provider_title_id'] for s in rows)
    from media_cli.state.health_reports import save_health_report,read_saved_report
    rid=save_health_report(tmp_path,report)
    assert 'Episode catalogue — Passed' in read_saved_report(tmp_path,rid)


def test_missing_provider_id_is_not_guessed_for_episodes():
    report=run(lambda *a:pytest.fail('Must not guess provider ID'),rows=False)
    rows=[s for s in report['providers'][0]['stages'] if s['name']=='episode_catalogue']
    assert len(rows)==6 and all(s['status']=='not_verified' for s in rows)


@pytest.mark.parametrize('outcome',[{'status':'ok','ids':[]},{'status':'error','ids':[]},{'status':'timeout','ids':[]}])
def test_empty_or_failed_episode_catalogue_is_inconclusive(outcome):
    report=run(lambda *a:outcome)
    rows=[s for s in report['providers'][0]['stages'] if s['name']=='episode_catalogue']
    assert all(s['status']=='inconclusive' for s in rows)


def test_worker_looks_up_episodes_with_exact_anipm_id(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli import providers
    from media_cli.health import search_worker
    instance=SimpleNamespace(episode_catalog=Mock(return_value={'state':'loaded','ids':['1','2']}))
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'anipm':lambda:instance})
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'anipm','operation':'episodes',
        'value':{'title_id':'101922','mal_id':38000,'anilist_id':101922}})))
    assert search_worker.main()==0
    instance.episode_catalog.assert_called_once_with('101922','sub')
    assert json.loads(capsys.readouterr().out)['ids']==['1','2']


def test_worker_rejects_bare_mal_number_for_anipm_episode_lookup(monkeypatch):
    import io,json
    from media_cli.health import search_worker
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'anipm','operation':'episodes','value':38000})))
    assert search_worker.main()==2
