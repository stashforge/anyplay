"""Observe AniPM's native combined operation without changing its provider."""
import pytest


def mirror():
    return {'resolution':'1080p','type':'hls','host':'cdn.example','channel':'sub','source':'AniPM [Sub] 1080p',
            'separate_audio':False,'subtitle_count':1,'headers_present':True}


def run(resolve):
    from media_cli.health import catalogue as c
    by_title={s['title']:s for s in c.ANIPM_SAMPLES}
    def search(pid,title):
        mid=by_title[title]['mal_id']
        return {'status':'ok','results':[{'malId':mid,'anilistId':mid+100000,'provider_id':str(mid+100000)}]}
    return c.check_catalogue('anipm',c.ANIPM_SAMPLES,seed=42,query=search,
        episode_query=lambda *a:{'status':'ok','ids':['1','2']},resolution_query=resolve)


def test_combined_sampling_saves_channels_mirrors_and_honest_limitation(tmp_path):
    calls=[]
    def resolve(pid,reference,episode):
        calls.append((pid,reference,episode))
        assert int(reference['title_id'])==reference['mal_id']+100000
        return {'status':'ok','mirrors':[mirror(),dict(mirror(),resolution='720p')],'unresolved_count':0}
    report=run(resolve)
    assert len(calls)==3 and report['verification']=='extraction'
    stages=report['providers'][0]['stages'];combined=[s for s in stages if s['name']=='source_resolution']
    assert len(combined)==3 and all(s['status']=='passed' and s['mirror_count']==2 and s['channel_count']==1 for s in combined)
    assert {s['age_group'] for s in combined}=={'older','middle','recent'}
    assert not any(s['status']=='passed' for s in stages if s['name'] in ('source_discovery','mirror_extraction'))
    from media_cli.state.health_reports import save_health_report,read_saved_report
    rid=save_health_report(tmp_path,report);text=read_saved_report(tmp_path,rid)
    assert 'AniPM [Sub]' in text and 'Channel count: 1' in text
    assert 'together' in text and 'attempted' in text


@pytest.mark.parametrize('outcome',[{'status':'ok','mirrors':[]},{'status':'timeout','mirrors':[]}])
def test_no_mirrors_does_not_guess_which_part_failed(outcome):
    report=run(lambda *a:outcome)
    rows=[s for s in report['providers'][0]['stages'] if s['name']=='source_resolution']
    assert len(rows)==3 and all(s['status']=='inconclusive' for s in rows)
    assert all('distinguished' in s['reason'] for s in rows)


def test_local_requirement_and_cancel_preserve_pending_resolution():
    report=run(lambda *a:{'status':'missing_dependency','mirrors':[],'requirement_kind':'python','issue_scope':'local','reason':'Module unavailable'})
    assert report['status']=='incomplete' and report['providers'][0]['local_issues'][0]['stage']=='source_resolution'
    report=run(lambda *a:(_ for _ in ()).throw(KeyboardInterrupt()))
    assert report['status']=='cancelled'
    assert len([s for s in report['providers'][0]['stages'] if s['name']=='source_resolution'])==3


def test_worker_resolves_once_and_keeps_no_raw_urls(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli import providers
    from media_cli.health import search_worker
    instance=SimpleNamespace(episode_sources=Mock(return_value={'episode':{'sourceUrls':[
        {'streamUrl':'https://cdn.example/secret?token=PRIVATE','type':'hls','resolution':'1080p',
         'channel':'sub','sourceName':'AniPM [Sub] 1080p','headers':{'Cookie':'PRIVATE'}}]}}))
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'anipm':lambda:instance})
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'anipm','operation':'resolve',
        'value':{'mal_id':38000,'title_id':'101922','anilist_id':101922,'episode':'1'}})))
    assert search_worker.main()==0
    output=capsys.readouterr().out
    instance.episode_sources.assert_called_once_with('101922','1','sub')
    assert 'PRIVATE' not in output and '/secret' not in output
    result=json.loads(output)
    assert result['mirrors'][0]['channel']=='sub' and result['mirrors'][0]['source']=='AniPM [Sub] 1080p'


def test_combined_worker_uses_extraction_budget(monkeypatch):
    import json
    from types import SimpleNamespace
    from media_cli.health import catalogue as c
    seen=[]
    def execute(command,payload,**kwargs):
        seen.append(kwargs.get('timeout',c.EXTRACTION_TIMEOUT))
        return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'ok','mirrors':[]}),stderr='')
    monkeypatch.setattr(c,'_run_extraction_worker',execute)
    c.resolve_isolated('anipm',{'mal_id':38000,'title_id':'101922'},'1')
    assert seen==[60]
