"""Silent decoding evidence must not be confused with metadata or exit status."""
import importlib.util
import pytest


def module():
    assert importlib.util.find_spec('media_cli.health.mpv_media'), 'silent mpv probe missing'
    from media_cli.health import mpv_media
    return mpv_media


def test_decoder_filter_frames_are_evidence_for_each_track():
    checks=module().decode_evidence('[ffmpeg] Parsed_showinfo_0: n: 0 pts: 0\n[ffmpeg] Parsed_ashowinfo_0: n:0 pts:0 nb_samples:1024\n',returncode=0)
    assert [c['track'] for c in checks]==['video','audio']
    assert all(c['status']=='passed' for c in checks)
    assert checks[0]['decoded_frames']==1 and checks[1]['decoded_samples']==1024


def test_metadata_and_successful_exit_do_not_prove_decode():
    checks=module().decode_evidence('VO: [null] 1920x1080\nAO: [null] stereo\n',returncode=0)
    assert all(c['status']=='inconclusive' for c in checks)


def test_video_pass_does_not_hide_missing_audio():
    checks=module().decode_evidence('[ffmpeg] Parsed_showinfo_0: n: 0 pts: 0\n',returncode=1)
    assert checks[0]['status']=='passed' and checks[1]['status']=='inconclusive'


def test_timeout_keeps_actual_partial_evidence_and_omits_raw_logs():
    checks=module().decode_evidence('[ffmpeg] Parsed_showinfo_0: n: 1 pts: 1\nhttps://cdn.example/secret?token=PRIVATE',returncode=-9,timed_out=True)
    assert checks[0]['status']=='passed' and checks[1]['status']=='inconclusive'
    assert 'time' in checks[1]['reason'].lower()
    assert 'PRIVATE' not in str(checks)


def test_mpv_is_explicit_opt_in():
    from media_cli.cli.args import parse_cli_args
    args,_=parse_cli_args(['health','senshi','--verify','mpv'])
    assert args.health_verify=='mpv'
    args,_=parse_cli_args(['health','senshi'])
    assert args.health_verify is None


def test_missing_mpv_uses_existing_local_requirement(monkeypatch):
    m=module()
    from media_cli.services import requirements
    from media_cli.health.failures import classify_failure
    monkeypatch.setattr(requirements,'require_executable',lambda *a: (_ for _ in ()).throw(requirements.MissingExecutable('mpv','Health decoding')))
    with pytest.raises(requirements.MissingExecutable) as error:
        m.probe_entries([{'streamUrl':'https://cdn.example/v.m3u8','type':'hls'}])
    assert classify_failure(error.value)['issue_scope']=='local'


def test_highest_concrete_quality_only_and_proxy_cleanup(monkeypatch):
    m=module()
    from media_cli.services import requirements
    from media_cli.media import local_proxy
    monkeypatch.setattr(requirements,'require_executable',lambda *a:'/usr/bin/mpv')
    starts=[];stops=[];server=object()
    def start(url,referer,headers,**kwargs):
        starts.append((url,kwargs));return ('http://127.0.0.1/private',server)
    monkeypatch.setattr(local_proxy,'start_local_proxy',start)
    monkeypatch.setattr(local_proxy,'stop_local_proxy',stops.append)
    monkeypatch.setattr(m,'run_sample',lambda *a,**k: [{'track':'video','status':'passed','reason':'Decoded frame','decoded_frames':1}])
    result=m.probe_entries([{'streamUrl':'https://cdn.example/480','resolution':'480p','type':'hls'}, {'streamUrl':'https://cdn.example/1080','resolution':'1080p','type':'hls','audio_url':'https://cdn.example/a'}])
    assert len(starts)==1 and starts[0][0].endswith('/1080')
    assert starts[0][1]['audio_url'].endswith('/a') and starts[0][1]['subtitles'] is None
    assert stops==[server] and result[0]['mirror_index']==1


def test_proxy_cleanup_on_cancel(monkeypatch):
    m=module()
    from media_cli.services import requirements
    from media_cli.media import local_proxy
    monkeypatch.setattr(requirements,'require_executable',lambda *a:'/usr/bin/mpv')
    server=object();stops=[]
    monkeypatch.setattr(local_proxy,'start_local_proxy',lambda *a,**k:('http://127.0.0.1/private',server))
    monkeypatch.setattr(local_proxy,'stop_local_proxy',stops.append)
    monkeypatch.setattr(m,'run_sample',lambda *a,**k:(_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        m.probe_entries([{'streamUrl':'https://cdn.example/v','type':'hls'}])
    assert stops==[server]


def test_playback_evidence_survives_saved_report(tmp_path):
    from media_cli.health.catalogue import check_catalogue,SENSHI_SAMPLES
    by_title={s['title']:s['mal_id'] for s in SENSHI_SAMPLES}
    report=check_catalogue('senshi',SENSHI_SAMPLES,seed=42,
        query=lambda pid,t:{'status':'ok','results':[{'malId':by_title[t]}]},
        episode_query=lambda *a:{'status':'ok','ids':['1']},
        source_query=lambda *a:{'status':'ok','sources':['Vidcloud']},
        extraction_query=lambda *a:{'status':'ok','mirrors':[{'resolution':'1080p','type':'hls','host':'cdn.example','separate_audio':True,'headers_present':True,'subtitle_count':1}],
            'playback_checks':[{'track':'video','status':'passed','reason':'Decoded frames','decoded_frames':12}]},playback_checks=True)
    from media_cli.state.health_reports import save_health_report,read_saved_report
    run_id=save_health_report(tmp_path,report)
    assert report['verification']=='mpv'
    stages=report['providers'][0]['stages']
    assert len([s for s in stages if s['name']=='playback'])==3
    assert all(s['decoded_frames']==12 for s in stages if s['name']=='playback')
    assert '12' in read_saved_report(tmp_path,run_id)


def test_real_local_media_decodes_without_window_sound_or_state(tmp_path):
    import shutil,subprocess,os
    if not shutil.which('mpv') or not shutil.which('ffmpeg'):
        pytest.skip('mpv/ffmpeg unavailable for generated media check')
    sample=tmp_path/'sample.mkv'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=size=64x64:rate=10:duration=1',
                    '-f','lavfi','-i','sine=duration=1','-c:v','mpeg4','-c:a','aac','-y',str(sample)],check=True,timeout=10)
    checks=module().run_sample(shutil.which('mpv'),str(sample),timeout=10)
    assert all(c['status']=='passed' for c in checks)
    assert checks[0]['decoded_frames']>=5 and checks[1]['decoded_samples']>1000
    assert sorted(p.name for p in tmp_path.iterdir())==['sample.mkv']


def test_process_timeout_kills_and_reaps_player(tmp_path):
    import os,sys,time
    script=tmp_path/'fake-mpv'
    script.write_text('#!'+sys.executable+'\nimport time,os\nprint("[ffmpeg] Parsed_showinfo_0: n: 0 pts: 0",flush=True)\ntime.sleep(30)\n')
    script.chmod(0o700)
    before=time.monotonic()
    checks=module().run_sample(str(script),'http://127.0.0.1/sample',timeout=0.2)
    assert time.monotonic()-before<3
    assert checks[0]['status']=='passed' and checks[1]['status']=='inconclusive'


def test_mpv_worker_budget_covers_extraction_http_and_decode(monkeypatch):
    from media_cli.health import catalogue
    import subprocess,json
    timeouts=[]
    monkeypatch.setattr(catalogue,'_run_extraction_worker',lambda cmd,payload,timeout: (timeouts.append(timeout) or subprocess.CompletedProcess(cmd,0,json.dumps({'status':'ok','mirrors':[]}),'')))
    assert catalogue.mpv_isolated('senshi',1,'1','Vidcloud')['status']=='ok'
    assert timeouts==[170]


def test_missing_player_keeps_successful_extraction_and_marks_local():
    from media_cli.health.catalogue import check_catalogue,SENSHI_SAMPLES
    by_title={s['title']:s['mal_id'] for s in SENSHI_SAMPLES}
    report=check_catalogue('senshi',SENSHI_SAMPLES,seed=42,
        query=lambda pid,t:{'status':'ok','results':[{'malId':by_title[t]}]},
        episode_query=lambda *a:{'status':'ok','ids':['1']},source_query=lambda *a:{'status':'ok','sources':['Vidcloud']},
        extraction_query=lambda *a:{'status':'ok','mirrors':[{'resolution':'1080p'}],
            'playback_failure':{'status':'missing_dependency','issue_scope':'local','requirement_kind':'executable','reason':'mpv missing'}},
        playback_checks=True)
    assert report['status']=='incomplete'
    provider=report['providers'][0]
    assert provider['status']=='inconclusive'
    assert provider['local_issues'][0]['stage']=='playback'
    assert any(s['status']=='passed' for s in provider['stages'] if s['name']=='mirror_extraction')


def test_worker_extracts_once_reuses_entries_and_drops_private_urls(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli.health import search_worker,http_media
    from media_cli import providers
    m=module()
    entries=[{'type':'hls','resolution':'1080p','streamUrl':'https://s-1.bcdn1.se/video?token=PRIVATE'}]
    instance=SimpleNamespace(extract_discovered_source=Mock(return_value=entries))
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'senshi':lambda:instance})
    seen=[]
    monkeypatch.setattr(http_media,'probe_entries',lambda rows:seen.append(('http',rows)) or [])
    monkeypatch.setattr(m,'probe_entries',lambda rows:seen.append(('mpv',rows)) or [{'track':'video','status':'passed','reason':'Decoded','decoded_frames':3}])
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'senshi','operation':'mpv','value':{'mal_id':20,'episode':'1','source':'Vidcloud'}})))
    assert search_worker.main()==0
    output=capsys.readouterr().out
    assert 'PRIVATE' not in output
    assert instance.extract_discovered_source.call_count==1
    assert [name for name,rows in seen]==['http','mpv']
    assert seen[0][1]==seen[1][1]==entries
    assert json.loads(output)['playback_checks'][0]['decoded_frames']==3
