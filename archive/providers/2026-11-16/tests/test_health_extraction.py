"""Extract source-specific entries without confusing that with playable media."""

import json

import pytest

from media_cli.health import catalogue
from media_cli.providers.senshi import Senshi


def test_extract_one_named_source_without_extracting_other_routes(monkeypatch):
    provider = Senshi()
    monkeypatch.setattr(provider, '_fetch_json', lambda *args: [
        {'status': 'sub', 'remote_source_id': 'r', 'server2': 'https://embed.test/a', 'serverFM': 'https://embed.test/b'},
        {'status': 'dub', 'remote_source_id': 'dub'},
    ])
    seen = []
    monkeypatch.setattr(provider, '_extract_embed_sources', lambda embeds, is_dub: seen.append((embeds, is_dub)) or [])
    assert hasattr(provider, 'extract_discovered_source'), 'Need source-specific extraction boundary'
    provider.extract_discovered_source('20', '8', 'Vidcloud', 'sub')
    assert seen == [([{'remote_source_id': 'r'}], False)]


def run(extract):
    by_title = {s['title']: s['mal_id'] for s in catalogue.SENSHI_SAMPLES}
    return catalogue.check_catalogue('senshi', catalogue.SENSHI_SAMPLES, seed=42,
        query=lambda pid, title: {'status': 'ok', 'results': [{'malId': by_title[title]}]},
        episode_query=lambda *args: {'status': 'ok', 'ids': ['1', '2']},
        source_query=lambda *args: {'status': 'ok', 'sources': ['Vidcloud', 'FileMoon']},
        extraction_query=extract)


def mirrors():
    return [{'resolution': '1080p', 'type': 'hls', 'host': 'cdn.example',
             'separate_audio': True, 'subtitle_count': 2, 'headers_present': True}]


def test_extraction_records_each_discovered_source_and_keeps_playback_unverified():
    calls = []
    def extract(pid, mid, episode, source):
        calls.append((mid, episode, source))
        return {'status': 'ok', 'mirrors': mirrors(), 'unresolved_count': 0}
    report = run(extract)
    stages = [s for s in report['providers'][0]['stages'] if s['name'] == 'mirror_extraction']
    assert len(stages) == len(calls) == 6
    assert {s['source'] for s in stages} == {'Vidcloud', 'FileMoon'}
    assert all(s['status'] == 'passed' and s['mirror_count'] == 1 for s in stages)
    assert all((s['mal_id'], s['episode'], s['source']) in calls for s in stages)
    assert report['providers'][0]['status'] == 'inconclusive'
    assert any(s['name']=='media_access' and s['status']=='not_verified' for s in report['providers'][0]['stages'])


@pytest.mark.parametrize('outcome', [
    {'status': 'ok', 'mirrors': [], 'unresolved_count': 1},
    {'status': 'timeout', 'mirrors': []}, {'status': 'error', 'mirrors': []},
])
def test_unresolved_or_timed_out_extraction_is_not_a_provider_failure(outcome):
    report = run(lambda *args: outcome)
    stages = [s for s in report['providers'][0]['stages'] if s['name']=='mirror_extraction']
    assert all(s['status']=='inconclusive' for s in stages)
    assert report['providers'][0]['status']=='inconclusive'


def test_extraction_cancellation_retains_pending_work():
    calls=[]
    def extract(*args):
        calls.append(args)
        if len(calls)==2:
            raise KeyboardInterrupt
        return {'status':'ok','mirrors':mirrors()}
    report=run(extract)
    stages=[s for s in report['providers'][0]['stages'] if s['name']=='mirror_extraction']
    assert report['status']=='cancelled'
    assert [s['status'] for s in stages] == ['passed']+['not_verified']*5


def test_safe_extraction_evidence_round_trip(tmp_path):
    from media_cli.state.health_reports import save_health_report,read_saved_report
    report=run(lambda *args: {'status':'ok','mirrors':mirrors()})
    run_id=save_health_report(tmp_path,report)
    saved=json.loads((tmp_path/run_id/'report.json').read_text())
    stage=next(s for s in saved['providers'][0]['stages'] if s['name']=='mirror_extraction')
    assert stage['mirrors'][0]['separate_audio'] is True
    assert stage['mirrors'][0]['subtitle_count']==2
    assert '1080p' in read_saved_report(tmp_path,run_id)
    assert 'cdn.example' in read_saved_report(tmp_path,run_id)


def test_extraction_timeout_budget_is_separate(monkeypatch):
    assert hasattr(catalogue,'extract_isolated'), 'Extraction worker is missing'
    seen=[]
    def operation(*args,**kwargs):
        seen.append((args,kwargs))
        return {'status':'ok','mirrors':[]}
    monkeypatch.setattr(catalogue,'_query_isolated',operation)
    catalogue.extract_isolated('senshi',20,'8','Vidcloud')
    assert catalogue.EXTRACTION_TIMEOUT==60
    assert seen[0][0][1]=='extract'


def test_mirror_summary_omits_signed_urls_and_unresolved_embeds():
    from media_cli.health.mirrors import summarize_mirrors
    result = summarize_mirrors([
        {'type':'hls','streamUrl':'https://user:secret@cdn.example/private.m3u8?token=secret',
         'resolution':'1080p','audio_url':'https://cdn.example/a.m3u8?token=secret',
         'subtitles':[{'url':'https://cdn.example/sub?secret=1'}], 'headers':{'Cookie':'secret'}},
        {'type':'embed','link':'https://embed.example/player'},
        {'type':'mp4','streamUrl':'file:///private/video.mp4'},
    ])
    assert result['unresolved_count']==2 and len(result['mirrors'])==1
    mirror=result['mirrors'][0]
    assert mirror['host']=='cdn.example' and mirror['separate_audio'] is True
    assert mirror['subtitle_count']==1
    assert 'secret' not in json.dumps(result) and 'private.m3u8' not in json.dumps(result)


@pytest.mark.parametrize('interruption', ['timeout','cancel'])
def test_extraction_worker_kills_its_process_group_on_interruption(monkeypatch, interruption):
    import os
    import signal
    from unittest.mock import Mock
    if os.name != 'posix':
        pytest.skip('POSIX process group check')
    error = catalogue.subprocess.TimeoutExpired(['worker'],60) if interruption=='timeout' else KeyboardInterrupt()
    process=Mock(pid=43210)
    process.communicate.side_effect=[error, ('','')]
    factory=Mock(return_value=process)
    kill=Mock()
    monkeypatch.setattr(catalogue.subprocess,'Popen',factory)
    monkeypatch.setattr(os,'killpg',kill)
    with pytest.raises(type(error)):
        catalogue._run_extraction_worker(['worker'],'{}')
    kill.assert_called_once_with(43210,signal.SIGKILL)
    assert factory.call_args.kwargs['start_new_session'] is True
    assert process.communicate.call_count==2


def test_missing_extractor_dependency_retains_pending_sources_and_saves_incomplete():
    report=run(lambda *args: {'status':'missing_dependency','mirrors':[], 'reason':'Node.js unavailable'})
    stages=[s for s in report['providers'][0]['stages'] if s['name']=='mirror_extraction']
    assert report['status']=='incomplete'
    assert report['providers'][0]['status']=='inconclusive'
    assert report['providers'][0]['local_issues']
    assert stages[0]['status']=='not_verified'
    assert stages[0]['issue_scope']=='local'
    assert all(s['status']=='not_verified' for s in stages[1:])
