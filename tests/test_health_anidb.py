"""AniDB.app local post IDs are not MAL or AniList IDs."""

def test_native_post_id_is_kept_after_confident_metadata_match():
    from media_cli.health.catalogue import check_catalogue
    seen=[]
    report=check_catalogue('anidb',[{'title':'Death Note','year':2006,'mal_id':1535}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'name':'Death Note','airedStart':{'year':2006},'provider_id':'98765'}]},
        episode_query=lambda p,ref:seen.append(ref) or {'status':'ok','ids':['1']},
        resolution_query=lambda p,ref,ep:seen.append(ref) or {'status':'ok','mirrors':[]})
    assert len(seen)==2 and all(ref['title_id']=='98765' for ref in seen)
    assert any(s['name']=='title_identity' and s['match_method']=='metadata' for s in report['providers'][0]['stages'])


def test_anidb_media_checks_use_observed_cdn_only():
    from media_cli.health.profiles import PROFILES
    from media_cli.health.http_media import media_destination_allowed
    assert 'mpv' in PROFILES['anidb']['levels']
    assert media_destination_allowed('anidb','https://b1.xlsbox.com/video.m3u8')
    assert not media_destination_allowed('anidb','https://b1.xlsbox.com.evil.example/video')
