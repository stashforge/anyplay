"""TMDB identities and catalogues do not prove streaming mirror health."""

def test_movie_native_tmdb_id_is_matched_and_streams_checked_independently():
    from media_cli.health.catalogue import check_catalogue
    seen=[]
    report=check_catalogue('movies',[{'title':'The Matrix','year':1999,'native_id':'movie:603'}],seed=42,
        query=lambda *a:{'status':'ok','results':[{'name':'The Matrix','provider_id':'movie:603','airedStart':'1999-03-31'}]},
        episode_query=lambda *a:{'status':'ok','ids':['1']},
        resolution_query=lambda *a:seen.append(a) or {'status':'ok','mirrors':[]})
    stages=report['providers'][0]['stages']
    assert next(s for s in stages if s['name']=='title_identity')['status']=='passed'
    assert next(s for s in stages if s['name']=='search')['evidence_origin']=='TMDB'
    assert next(s for s in stages if s['name']=='episode_catalogue')['status']=='inconclusive'
    assert seen[0][1]['title_id']=='movie:603' and seen[0][1].get('mal_id') is None


def test_date_string_metadata_uses_existing_year_parser():
    from media_cli.health.profiles import matching_rows
    result=matching_rows('animegg',{'title':'Movie','year':2010},
        [{'name':'Movie','provider_id':'movie','airedStart':'2010-01-01'}])
    assert result[0]['match_method']=='metadata'
