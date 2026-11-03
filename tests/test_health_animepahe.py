"""Health supports native title/episode sessions without guessing tracker IDs."""
import pytest


def test_animepahe_native_sessions_reach_resolution():
    from media_cli.health.catalogue import check_catalogue
    seen=[]
    report=check_catalogue('animepahe',[{'title':'Naruto','year':2002,'mal_id':20}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'name':'Naruto','airedStart':{'year':2002},'provider_id':'title-session'}]},
        episode_query=lambda *a:{'status':'ok','ids':['title-session/episode-session']},
        resolution_query=lambda p,r,e:seen.append((r,e)) or {'status':'ok','mirrors':[]})
    stages=report['providers'][0]['stages']
    assert next(s for s in stages if s['name']=='title_identity')['match_method']=='metadata'
    assert next(s for s in stages if s['name']=='episode_catalogue')['status']=='passed'
    assert seen[0][0]['title_id']=='title-session' and seen[0][1]=='title-session/episode-session'


@pytest.mark.parametrize('episode',['other/episode-session','title-session/../secret','https://evil.example/x','title-session/ep?x=secret'])
def test_foreign_or_unsafe_episode_sessions_do_not_reach_resolution(episode):
    from media_cli.health.catalogue import check_catalogue
    report=check_catalogue('animepahe',[{'title':'Naruto','year':2002,'mal_id':20}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'name':'Naruto','airedStart':{'year':2002},'provider_id':'title-session'}]},
        episode_query=lambda *a:{'status':'ok','ids':[episode]},resolution_query=lambda *a:pytest.fail('Invalid session used'))
    assert not any(s['name']=='episode_catalogue' and s['status']=='passed' for s in report['providers'][0]['stages'])


def test_worker_reuses_normal_embed_resolver(monkeypatch,capsys):
    import io,json
    from types import SimpleNamespace
    from media_cli import providers
    from media_cli.health import search_worker
    from media_cli.media import resolver
    descriptor={'sourceName':'Kwik 1080p','sourceUrl':'https://kwik.example/embed','type':'embed'}
    monkeypatch.setattr(providers,'PROVIDER_FACTORIES',{'animepahe':lambda:SimpleNamespace(
        episode_sources=lambda *a:{'episode':{'sourceUrls':[descriptor]}})})
    seen=[]
    monkeypatch.setattr(resolver,'resolve_source',lambda source,**kw:seen.append(source) or [
        {'link':'https://cdn.example/video.m3u8','type':'hls','resolution':'1080p'}])
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'animepahe','operation':'resolve',
        'value':{'title_id':'title-session','mal_id':20,'episode':'title-session/ep-session'}})))
    assert search_worker.main()==0
    result=json.loads(capsys.readouterr().out)
    assert seen==[descriptor] and len(result['mirrors'])==1 and result['source_count']==1


def test_unmatched_metadata_identity_does_not_claim_missing_anilist_id():
    from media_cli.health.catalogue import check_catalogue
    report=check_catalogue('animepahe',[{'title':'Naruto','year':2002,'mal_id':20}],query=lambda *a:{'status':'ok','results':[]})
    reason=next(s['reason'] for s in report['providers'][0]['stages'] if s['name']=='title_identity')
    assert 'None' not in reason and 'metadata' in reason
