"""HTTP evidence is bounded access checking, not playback or subtitle scoring."""

import importlib.util

import pytest


def module():
    assert importlib.util.find_spec('media_cli.health.http_media'), 'HTTP media probe is missing'
    from media_cli.health import http_media
    return http_media


def response(url, body, status=200, truncated=False):
    m=module()
    return m.HTTPResult(url,status,body,truncated)


def probe(entry, bodies):
    m=module()
    calls=[]
    def fetch(url, headers, limit, sample=False):
        calls.append((url,dict(headers),limit,sample))
        value=bodies[url]
        if isinstance(value,Exception):
            raise value
        return value if isinstance(value,m.HTTPResult) else response(url,value)
    checks=m.probe_entry(entry,fetch)
    return checks,calls


def stream(**extra):
    return dict(type='hls',streamUrl='https://s-1.bcdn1.se/video.m3u8',resolution='1080p',
                headers={'Referer':'https://senshi.to/','User-Agent':'test-agent'},**extra)


def test_hls_video_audio_and_subtitle_are_checked_separately():
    entry=stream(audio_url='https://s-1.bcdn1.se/audio.m3u8',subtitles=[{'url':'https://s.anicdn.se/en.vtt','label':'English'}])
    checks,calls=probe(entry,{
        entry['streamUrl']:b'#EXTM3U\n#EXTINF:5,\nv.ts\n',
        'https://s-1.bcdn1.se/v.ts':b'\x47'+b'\0'*100,
        entry['audio_url']:b'#EXTM3U\n#EXTINF:5,\na.aac\n',
        'https://s-1.bcdn1.se/a.aac':b'\xff\xf1'+b'\0'*30,
        'https://s.anicdn.se/en.vtt':b'WEBVTT\n\n',
    })
    assert [c['track'] for c in checks]==['video','audio','subtitle']
    assert all(c['status']=='passed' for c in checks)
    assert all(c[1]['Referer']=='https://senshi.to/' for c in calls)
    assert any(c[3] and c[2]==65536 for c in calls)
    assert not any('decoded' in c['reason'] and 'not decoded' not in c['reason'] for c in checks)


def test_manifest_is_not_enough_without_a_segment():
    entry=stream()
    checks,_=probe(entry,{entry['streamUrl']:b'#EXTM3U\n#EXTINF:5,\nv.ts\n',
                         'https://s-1.bcdn1.se/v.ts':response('https://s-1.bcdn1.se/v.ts',b'',404)})
    assert checks[0]['status']=='failed' and checks[0]['http_status']==404


def test_http_200_html_is_not_media_success():
    entry=stream()
    checks,_=probe(entry,{entry['streamUrl']:b'<html>verification required</html>'})
    assert checks[0]['status']!='passed'


def test_timeout_is_inconclusive_not_dead():
    entry=stream()
    checks,_=probe(entry,{entry['streamUrl']:TimeoutError('signed URL must not appear')})
    assert checks[0]['status']=='inconclusive'
    assert 'signed URL' not in checks[0]['reason']


def test_absent_detached_audio_is_not_claimed_present():
    entry=stream()
    checks,_=probe(entry,{entry['streamUrl']:b'#EXTM3U\n#EXTINF:5,\nv.ts\n',
                         'https://s-1.bcdn1.se/v.ts':b'\x47media'})
    assert checks[1]['track']=='audio' and checks[1]['status']=='not_verified'


def test_master_playlist_is_followed_before_media_sample():
    entry=stream()
    checks,calls=probe(entry,{
        entry['streamUrl']:b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=200000\nchild.m3u8\n',
        'https://s-1.bcdn1.se/child.m3u8':b'#EXTM3U\n#EXTINF:5,\nv.ts\n',
        'https://s-1.bcdn1.se/v.ts':b'\x47media',
    })
    assert checks[0]['status']=='passed' and len(calls)==3


def test_init_segment_and_aes_key_access_are_not_skipped():
    entry=stream()
    checks,calls=probe(entry,{
        entry['streamUrl']:b'#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:5,\nv.m4s\n',
        'https://s-1.bcdn1.se/init.mp4':b'\0\0\0\x18ftypisom',
        'https://s-1.bcdn1.se/key':b'k'*16,
        'https://s-1.bcdn1.se/v.m4s':b'\0\0\0\x18mdat'+b'\0'*16,
    })
    assert checks[0]['status']=='passed'
    assert any(c[0].endswith('/key') for c in calls)
    assert any(c[0].endswith('/init.mp4') for c in calls)
    assert all('kkkk' not in str(c) for c in checks)


def test_truncated_manifest_and_rate_limit_are_inconclusive():
    entry=stream()
    for result in (response(entry['streamUrl'],b'#EXTM3U',truncated=True),response(entry['streamUrl'],b'',429)):
        checks,_=probe(entry,{entry['streamUrl']:result})
        assert checks[0]['status']=='inconclusive'


def test_extraction_only_option_remains_available():
    from media_cli.cli.args import parse_cli_args
    args,_=parse_cli_args(['health','senshi','--verify','extraction'])
    assert args.health_verify=='extraction'


def http_run(outcome):
    from media_cli.health import catalogue
    by_title={s['title']:s['mal_id'] for s in catalogue.SENSHI_SAMPLES}
    return catalogue.check_catalogue('senshi',catalogue.SENSHI_SAMPLES,seed=42,
        query=lambda pid,title:{'status':'ok','results':[{'malId':by_title[title]}]},
        episode_query=lambda *args:{'status':'ok','ids':['1']},
        source_query=lambda *args:{'status':'ok','sources':['Vidcloud']},
        extraction_query=lambda *args:outcome,http_checks=True)


def mirror():
    return {'resolution':'1080p','type':'hls','host':'cdn.example','separate_audio':True,
            'subtitle_count':1,'headers_present':True}


def test_http_evidence_is_separate_from_extraction_and_round_trips(tmp_path):
    from media_cli.state.health_reports import save_health_report,read_saved_report
    outcome={'status':'ok','mirrors':[mirror()],'extraction_elapsed_seconds':0.1,
             'media_checks':[{'track':'video','mirror':'1080p','status':'passed','http_status':206,
             'bytes_sampled':65536,'host':'cdn.example','elapsed_seconds':0.2,'reason':'Bounded sample; not decoded'}]}
    report=http_run(outcome)
    stages=report['providers'][0]['stages']
    assert report['verification']=='http'
    assert sum(s['name']=='mirror_extraction' and s['status']=='passed' for s in stages)==3
    assert sum(s['name']=='media_access' and s['status']=='passed' for s in stages)==3
    assert all(s['status']=='not_verified' for s in stages if s['name']=='playback')
    run_id=save_health_report(tmp_path,report)
    assert 'Bytes sampled: 65536' in read_saved_report(tmp_path,run_id)


def test_http_prerequisite_failure_preserves_extracted_mirrors():
    report=http_run({'status':'ok','mirrors':[mirror()],
        'http_failure':{'status':'missing_dependency','issue_scope':'local','requirement_kind':'python','reason':'HTTP library missing'}})
    provider=report['providers'][0]
    assert report['status']=='incomplete'
    assert provider['local_issues']
    assert any(s['name']=='mirror_extraction' and s['status']=='passed' for s in provider['stages'])
    assert all(s['status']=='not_verified' for s in provider['stages'] if s['name']=='media_access')


def test_encrypted_manifest_uses_shared_decoder(monkeypatch):
    from media_cli.media import decryption
    seen=[]
    def decode(value):
        seen.append(value)
        return '#EXTM3U\n#EXTINF:5,\nv.ts\n'
    monkeypatch.setattr(decryption,'decrypt_em3u8',decode)
    entry=stream()
    checks,_=probe(entry,{entry['streamUrl']:b'EM3U8v1:synthetic',
                         'https://s-1.bcdn1.se/v.ts':b'\x47media'})
    assert checks[0]['status']=='passed' and seen==['EM3U8v1:synthetic']


def test_dash_manifest_is_not_mistaken_for_fetched_media():
    entry=stream()
    entry['type']='dash'
    checks,calls=probe(entry,{})
    assert checks[0]['status']=='not_verified' and calls==[]


def test_stream_fetch_stops_at_prefix_closes_response_and_preserves_headers():
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace
    m=module()
    client=m.HTTPFetcher.__new__(m.HTTPFetcher)
    client.allowed=lambda url:True
    client.cache={}
    client.deadline=time.monotonic()+10
    seen=[]
    closed=[]
    def chunks(**kwargs):
        yield b'v'*65536
        pytest.fail('Must not consume the rest of the media file')
    @contextmanager
    def streamed(method,url,**kwargs):
        seen.append((method,url,kwargs))
        try:
            yield SimpleNamespace(status_code=206,iter_content=chunks)
        finally:
            closed.append(True)
    client.session=SimpleNamespace(stream=streamed,cookies=SimpleNamespace(clear=lambda:None))
    headers={'Referer':'https://senshi.to/'}
    result=client('https://s-1.bcdn1.se/segment',headers,65536,sample=True)
    assert len(result.body)==65536 and closed==[True]
    assert seen[0][0]=='GET' and seen[0][2]['headers']['Range']=='bytes=0-65535'
    assert headers=={'Referer':'https://senshi.to/'}
    assert client('https://s-1.bcdn1.se/segment',headers,65536,sample=True)==result
    assert len(seen)==1


def test_redirect_to_untrusted_destination_is_not_followed():
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace
    m=module()
    client=m.HTTPFetcher.__new__(m.HTTPFetcher)
    client.allowed=lambda url:url.startswith('https://s-1.bcdn1.se/')
    client.cache={}
    client.deadline=time.monotonic()+10
    seen=[]
    @contextmanager
    def streamed(method,url,**kwargs):
        seen.append(url)
        yield SimpleNamespace(status_code=302,headers={'Location':'https://untrusted.example/private'})
    client.session=SimpleNamespace(stream=streamed,cookies=SimpleNamespace(clear=lambda:None))
    with pytest.raises(m.CheckIssue):
        client('https://s-1.bcdn1.se/manifest',{},1024)
    assert seen==['https://s-1.bcdn1.se/manifest']


def test_worker_extracts_once_then_probes_without_publishing_urls(monkeypatch, capsys):
    import io,json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli.health import search_worker, http_media
    from media_cli import providers
    entry=stream(audio_url='https://s-1.bcdn1.se/audio.m3u8?token=PRIVATE')
    entry['streamUrl']+='?token=PRIVATE'
    instance=SimpleNamespace(extract_discovered_source=Mock(return_value=[entry]))
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'senshi':lambda:instance})
    seen=[]
    monkeypatch.setattr(http_media,'probe_entries',lambda entries:seen.append(entries) or [{'track':'video','status':'passed','reason':'sample only'}])
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'senshi','operation':'http','value':{'mal_id':20,'episode':'1','source':'Vidcloud'}})))
    assert search_worker.main()==0
    output=capsys.readouterr().out
    assert 'PRIVATE' not in output
    assert instance.extract_discovered_source.call_count==1 and len(seen)==1
    assert json.loads(output)['media_checks'][0]['track']=='video'


def test_http_worker_uses_combined_budget(monkeypatch):
    import json
    from types import SimpleNamespace
    from media_cli.health import catalogue
    seen=[]
    def worker(command,payload,**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'ok','mirrors':[]}),stderr='')
    monkeypatch.setattr(catalogue,'_run_extraction_worker',worker)
    catalogue.http_isolated('senshi',20,'1','Vidcloud')
    assert seen==[{'timeout':125}]


@pytest.mark.parametrize('argv', [
    ['health','reports','--verify','http'],
    ['health','report','latest','--verify','http'],
])
def test_verify_option_is_for_checks_not_saved_report_commands(argv):
    from media_cli.cli.args import parse_cli_args
    with pytest.raises(SystemExit) as exc:
        parse_cli_args(argv)
    assert exc.value.code==2


def test_detached_audio_track_list_is_checked_without_audio_url():
    entry={'type':'mp4','link':'https://media.example/video.mp4',
           'audio_tracks':[{'url':'https://media.example/audio.m4a'}]}
    checks,calls=probe(entry,{'https://media.example/video.mp4':b'video bytes',
                              'https://media.example/audio.m4a':b'audio bytes'})
    audio=[c for c in checks if c['track']=='audio']
    assert len(audio)==1 and audio[0]['required'] is True and audio[0]['status']=='passed'
    from media_cli.health.mirrors import summarize_mirrors
    assert summarize_mirrors([entry])['mirrors'][0]['separate_audio'] is True


def test_public_http_redirect_does_not_receive_credentials_or_custom_headers():
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace
    m=module()
    client=m.HTTPFetcher.__new__(m.HTTPFetcher)
    client.allowed=lambda u:m.media_destination_allowed('animegg',u)
    client.deadline=time.monotonic()+10
    client.cache={}
    seen=[]
    @contextmanager
    def streamed(method,url,**kwargs):
        seen.append((url,kwargs['headers']))
        if len(seen)==1:
            yield SimpleNamespace(status_code=302,headers={'Location':'http://www.animegg.org/video.mp4#PRIVATE-FRAGMENT'})
        else:
            yield SimpleNamespace(status_code=206,iter_content=lambda **kw:iter([b'video']))
    client.session=SimpleNamespace(stream=streamed,cookies=SimpleNamespace(clear=lambda:None))
    headers={'Authorization':'Bearer PRIVATE','Cookie':'private=session','X-Secret':'PRIVATE',
             'User-Agent':'test-agent','Referer':'https://animegg.org/'}
    result=client('https://animegg.org/video.mp4',headers,64,sample=True)
    assert result.status==206 and len(seen)==2
    assert seen[1][0]=='http://www.animegg.org/video.mp4'
    assert seen[1][1]=={'User-Agent':'test-agent','Referer':'https://animegg.org/','Range':'bytes=0-63'}
    assert headers['Authorization']=='Bearer PRIVATE'


def test_url_fragments_are_removed_before_policy_and_network_checks():
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace
    m=module()
    client=m.HTTPFetcher.__new__(m.HTTPFetcher)
    allowed=[];seen=[]
    client.allowed=lambda u:allowed.append(u) or u=='https://s-1.bcdn1.se/segment.ts'
    client.deadline=time.monotonic()+10;client.cache={}
    @contextmanager
    def streamed(method,url,**kwargs):
        seen.append(url)
        yield SimpleNamespace(status_code=206,iter_content=lambda **kw:iter([b'video']))
    client.session=SimpleNamespace(stream=streamed,cookies=SimpleNamespace(clear=lambda:None))
    result=client('https://s-1.bcdn1.se/segment.ts#PRIVATE-FRAGMENT',{},64,sample=True)
    assert result.status==206
    assert allowed==seen==['https://s-1.bcdn1.se/segment.ts']


def test_cross_origin_https_media_redirect_drops_credentials():
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace
    m=module();client=m.HTTPFetcher.__new__(m.HTTPFetcher)
    client.allowed=lambda u:m.media_destination_allowed('animegg',u)
    client.deadline=time.monotonic()+10;client.cache={};seen=[]
    @contextmanager
    def streamed(method,url,**kwargs):
        seen.append((url,kwargs['headers']))
        if len(seen)==1:
            yield SimpleNamespace(status_code=302,headers={'Location':'https://vidcache.net:8161/video.mp4'})
        else:
            yield SimpleNamespace(status_code=206,iter_content=lambda **kw:iter([b'video']))
    client.session=SimpleNamespace(stream=streamed,cookies=SimpleNamespace(clear=lambda:None))
    client('https://animegg.org/video.mp4',{'Authorization':'Bearer PRIVATE','Cookie':'secret=value',
           'X-Api-Key':'PRIVATE','User-Agent':'test'},64,sample=True)
    assert len(seen)==2 and seen[1][1]=={'User-Agent':'test','Range':'bytes=0-63'}


def test_valid_four_redirect_media_chain_reaches_bounded_sample():
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace
    m=module();client=m.HTTPFetcher.__new__(m.HTTPFetcher)
    client.allowed=lambda u:True
    client.deadline=time.monotonic()+10;client.cache={};calls=[]
    @contextmanager
    def streamed(method,url,**kwargs):
        calls.append(url)
        if len(calls)<=4:
            yield SimpleNamespace(status_code=302,headers={'Location':f'https://media.example/hop{len(calls)}'})
        else:
            yield SimpleNamespace(status_code=206,iter_content=lambda **kw:iter([b'video']))
    client.session=SimpleNamespace(stream=streamed,cookies=SimpleNamespace(clear=lambda:None))
    assert client('https://media.example/start',{},64,sample=True).body==b'video'
    assert len(calls)==5


def test_redirect_loop_reports_limit_instead_of_blocked_destination():
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace
    m=module();client=m.HTTPFetcher.__new__(m.HTTPFetcher)
    client.allowed=lambda u:True
    client.deadline=time.monotonic()+10;client.cache={};calls=[]
    @contextmanager
    def streamed(method,url,**kwargs):
        calls.append(url)
        yield SimpleNamespace(status_code=302,headers={'Location':url})
    client.session=SimpleNamespace(stream=streamed,cookies=SimpleNamespace(clear=lambda:None))
    with pytest.raises(m.CheckIssue) as caught:
        client('https://media.example/loop',{},64,sample=True)
    assert 'redirect limit' in caught.value.reason.lower()
    assert caught.value.status=='inconclusive' and len(calls)<=6
