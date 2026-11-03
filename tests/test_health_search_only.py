"""Search-only diagnostics must not start media resolution or torrent transfers."""
import pytest


@pytest.mark.parametrize('provider',['torrent','animethemes'])
def test_search_only_health_uses_no_catalogue_or_media_workers(monkeypatch,tmp_path,capsys,provider):
    from media_cli.cli import health
    from media_cli.health import catalogue
    from media_cli.state import health_reports
    monkeypatch.setattr(health_reports,'reports_directory',lambda:tmp_path/'reports')
    monkeypatch.setattr(catalogue,'check_catalogue',lambda pid,samples,**kw:assert_search_only(pid,samples,kw))
    assert health.handle_health_command(provider,plain=True)==0
    assert 'search only' in capsys.readouterr().out.lower()


def assert_search_only(pid,samples,kwargs):
    assert len(samples)==10
    assert kwargs.get('episode_query') is None and kwargs.get('details_query') is None
    assert 'resolution_query' not in kwargs and 'extraction_query' not in kwargs
    return {'started_at':'2026-10-29T00:00:00+00:00','status':'complete','verification':'catalogue',
            'providers':[{'id':pid,'name':pid,'status':'inconclusive','stages':[]}]}


def test_catalogue_verification_is_available_only_where_supported():
    from media_cli.cli.args import parse_cli_args
    assert parse_cli_args(['health','torrent','--verify','catalogue'])[0].health_verify=='catalogue'
    with pytest.raises(SystemExit):
        parse_cli_args(['health','torrent','--verify','mpv'])


def test_search_only_worker_rejects_media_operation_before_imports(monkeypatch):
    import io,json
    from media_cli.health import search_worker
    monkeypatch.setattr(search_worker.sys,'stdin',io.StringIO(json.dumps({'provider':'torrent','operation':'resolve','value':{}})))
    assert search_worker.main()==2


def test_animethemes_search_bridge_reuses_existing_service(monkeypatch):
    from media_cli.services import animethemes
    from media_cli.health.search_worker import _theme_search
    monkeypatch.setattr(animethemes,'search',lambda q:{'anime':[{'id':10,'name':'Anime','year':2023}]})
    assert _theme_search('Anime','sub')==[{'_id':'10','name':'Anime','airedStart':{'year':2023}}]
