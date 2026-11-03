"""A fallback episode number is not proof of a working site catalogue."""

def test_fallback_eligible_catalogue_is_qualified_but_real_resolution_runs():
    from media_cli.health.catalogue import check_catalogue
    seen=[]
    report=check_catalogue('anizone',[{'title':'Naruto','year':2002,'mal_id':20}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'name':'Naruto','airedStart':{'year':2002},'provider_id':'naruto'}]},
        episode_query=lambda *a:{'status':'ok','ids':['1']},
        resolution_query=lambda *a:seen.append(a) or {'status':'ok','mirrors':[]})
    stage=next(s for s in report['providers'][0]['stages'] if s['name']=='episode_catalogue')
    assert stage['status']=='inconclusive' and stage['evidence_origin']=='site_or_fallback'
    assert 'Episode 1' in stage['reason'] and 'AniList' not in stage['reason']
    assert seen[0][1]['title_id']=='naruto'


def test_anizone_uses_shared_media_checks_on_its_cdn():
    from media_cli.health.profiles import PROFILES
    from media_cli.health.http_media import media_destination_allowed
    assert 'mpv' in PROFILES['anizone']['levels']
    assert media_destination_allowed('anizone','https://seiryuu.vid-cdn.xyz/video.m3u8')
    assert not media_destination_allowed('anizone','https://seiryuu.vid-cdn.xyz.evil.example/video')
