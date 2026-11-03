"""Working is scoped to the requested samples and requires real media evidence."""
import copy
import importlib.util
import pytest


def report(level='http'):
    stages=[]
    for i in range(3):
        for name in ('search','title_identity','title_details','episode_catalogue'):
            stages.append({'name':name,'title':f'Title {i}','status':'passed'})
        stages.append({'name':'source_resolution','title':f'Title {i}','status':'passed','mirror_count':1,
                       'mirrors':[{'resolution':'1080p','type':'hls','host':'media.settlar.io','headers_present':True,'separate_audio':False,'subtitle_count':0}]})
        stages.append({'name':'media_access','title':f'Title {i}','status':'passed','track':'video','required':True})
        stages.append({'name':'media_access','title':f'Title {i}','status':'not_verified','track':'audio','required':False})
        if level=='mpv':
            stages.extend({'name':'playback','title':f'Title {i}','status':'passed','track':track,'required':True} for track in ('video','audio'))
    stages.append({'name':'catalogue_summary','status':'inconclusive','reason':'Recorded samples only'})
    return {'verification':level,'status':'complete','providers':[{'id':'anipm','name':'AniPM','status':'inconclusive','stages':stages}]}


def apply(data):
    assert importlib.util.find_spec('media_cli.health.verdicts'), 'shared health verdicts missing'
    from media_cli.health.verdicts import apply_verdicts
    apply_verdicts(data)
    return data['providers'][0]['status']


@pytest.mark.parametrize('level',['http','mpv'])
def test_successful_required_samples_get_working(level):
    data=report(level)
    assert apply(data)=='working'
    assert data['providers'][0]['stages'][-1]['status']=='passed'
    assert 'sample' in data['providers'][0]['stages'][-1]['reason'].lower()


@pytest.mark.parametrize('status',['inconclusive','not_verified'])
def test_unknown_or_pending_required_sample_is_inconclusive(status):
    data=report();next(s for s in data['providers'][0]['stages'] if s['name']=='media_access' and s['required'])['status']=status
    assert apply(data)=='inconclusive'


def test_one_proven_failure_among_working_samples_is_partial():
    data=report();next(s for s in data['providers'][0]['stages'] if s['name']=='media_access' and s['required'])['status']='failed'
    assert apply(data)=='partly_working'


@pytest.mark.parametrize('condition',['cancelled','local_issue','missing_stage','no_media','not_enough_identities','missing_one_video','missing_decode_audio'])
def test_incomplete_evidence_cannot_be_working(condition):
    data=report('mpv');p=data['providers'][0]
    if condition=='cancelled':data['status']='cancelled'
    elif condition=='local_issue':p['local_issues']=[{'kind':'executable','reason':'mpv missing'}]
    elif condition=='missing_stage':p['stages']=[s for s in p['stages'] if s['name']!='episode_catalogue']
    elif condition=='no_media':p['stages']=[s for s in p['stages'] if s['name']!='media_access']
    elif condition=='not_enough_identities':p['stages']=[s for s in p['stages'] if s.get('title')!='Title 2']
    elif condition=='missing_one_video':p['stages'].remove(next(s for s in p['stages'] if s['name']=='media_access' and s.get('track')=='video'))
    elif condition=='missing_decode_audio':p['stages'].remove(next(s for s in p['stages'] if s['name']=='playback' and s.get('track')=='audio'))
    assert apply(data)=='inconclusive'


def test_supplied_external_audio_must_be_checked():
    data=report();next(s for s in data['providers'][0]['stages'] if s['name']=='source_resolution')['mirrors'][0]['separate_audio']=True
    assert apply(data)=='inconclusive'


def test_missing_optional_track_flag_is_not_assumed_optional():
    data=report()
    for s in data['providers'][0]['stages']:s.pop('required',None)
    assert apply(data)=='inconclusive'


@pytest.mark.parametrize('level',['catalogue','extraction'])
def test_early_levels_do_not_claim_media_health(level):
    assert apply(report(level))=='inconclusive'


def test_readable_working_label_is_qualified_by_level():
    from media_cli.domain.health_report import format_health_report
    data=report('mpv');data.update(run_id='example',started_at='2026-10-29T00:00:00Z')
    apply(data)
    text=format_health_report(data)
    assert 'Working — decode checked' in text and 'sample' in text.lower()


def test_http_absent_detached_tracks_are_explicitly_optional():
    from media_cli.health import http_media as m
    def fetch(url,*args,**kwargs):return m.HTTPResult(url,200,b'video prefix')
    rows=m.probe_entry({'type':'mp4','streamUrl':'https://media.settlar.io/v.mp4'},fetch)
    assert rows[0]['required'] is True and all(s['required'] is False for s in rows[1:])


def test_a_proven_source_failure_can_be_partial_without_inventing_mirrors():
    data=report();rows=data['providers'][0]['stages']
    source=next(s for s in rows if s['name']=='source_resolution')
    source.update(status='failed',mirror_count=0,mirrors=[])
    rows[:]=[s for s in rows if not (s['name']=='media_access' and s.get('title')==source['title'])]
    assert apply(data)=='partly_working'


def test_required_marker_roundtrips_and_old_reports_stay_unchanged(tmp_path):
    import json
    from media_cli.state.health_reports import save_health_report,read_saved_report
    old=report();old.update(run_id='old',started_at='2026-10-29T00:00:00Z')
    old_snapshot=copy.deepcopy(old)
    old_id=save_health_report(tmp_path,old)
    before={p:p.read_bytes() for p in (tmp_path/old_id).iterdir()}
    data=report();data['started_at']='2026-10-29T01:00:00Z';apply(data)
    rid=save_health_report(tmp_path,data)
    text=read_saved_report(tmp_path,rid)
    saved=json.loads((tmp_path/rid/'report.json').read_text())
    assert 'Working — HTTP checked' in text
    assert any(s.get('required') is False for s in saved['providers'][0]['stages'])
    assert old==old_snapshot and before=={p:p.read_bytes() for p in before}
