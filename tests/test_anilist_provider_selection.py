"""AniList title opening must not revive retired saved streaming links."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.app import anilist
from media_cli import providers


@pytest.mark.parametrize('saved_provider', ['allanime', 'miruro', 'mkissa', 'disabled'])
def test_unavailable_saved_provider_keeps_configured_default(monkeypatch, saved_provider):
    matched = run_open(monkeypatch, saved_provider, configured='anipm')
    assert matched.call_args.kwargs['provider_id'] == 'anipm'


def test_active_saved_provider_is_still_reused(monkeypatch):
    matched = run_open(monkeypatch, 'senshi', configured='anipm')
    assert matched.call_args.kwargs['provider_id'] == 'senshi'


def test_missing_saved_link_keeps_nonstandard_configured_default(monkeypatch):
    matched = run_open(monkeypatch, None, configured='reanime')
    assert matched.call_args.kwargs['provider_id'] == 'reanime'


def run_open(monkeypatch, saved_provider, configured):
    saved = {'_provider': saved_provider, '_id': 'old-source'} if saved_provider else {}
    monkeypatch.setattr(anilist.app_core, 'get_al_match', lambda *args: saved)
    monkeypatch.setattr(providers, 'is_provider_active', lambda key: key in {'anipm', 'senshi', 'reanime'})
    monkeypatch.setattr(anilist.app_core, 'provider_display_name', lambda key: key)
    monkeypatch.setattr(anilist.app_core, 'with_loading', lambda message, fn, *args, **kwargs: fn(*args, **kwargs))
    match = Mock(return_value=None)
    monkeypatch.setattr(anilist.app_core, 'match_anilist_show_to_provider', match)
    result = anilist._open_anilist_show_from_picker(
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace(provider=None),
        'sub', {'_id': '123', 'name': 'Example'}, 'ANILIST_SEARCH', {'provider': configured},
    )
    assert result == 'DETAILS'
    assert saved == ({'_provider': saved_provider, '_id': 'old-source'} if saved_provider else {})
    return match


def test_saved_senshi_match_opens_details_with_native_identity(monkeypatch):
    from media_cli.core import storage
    from media_cli.domain import matching
    from media_cli.providers.senshi import Senshi
    from media_cli.services import catalog

    saved = {'_provider': 'senshi', '_id': '52034', 'match_source': 'id'}
    monkeypatch.setattr(anilist.app_core, 'get_al_match', lambda *a: saved)
    monkeypatch.setattr(storage, 'get_al_match', lambda *a: saved)
    monkeypatch.setattr(anilist.app_core, 'match_anilist_show_to_provider', matching.match_anilist_show_to_provider)
    monkeypatch.setattr(anilist.app_core, 'with_loading', lambda message, fn, *a, **kw: fn(*a, **kw))
    monkeypatch.setattr(catalog, 'require_provider_operation', lambda *a: None)
    monkeypatch.setattr(catalog, 'get_provider', lambda key, *a: Senshi())
    catalogue = Mock(return_value=['1'])
    monkeypatch.setattr(anilist.app_core, 'load_episode_ids_for_selection', catalogue)
    ui, ms = SimpleNamespace(), SimpleNamespace()
    source = {'_id': '150672', 'name': 'Example', 'malId': 52034}
    result = anilist._open_anilist_show_from_picker(
        SimpleNamespace(), ui, ms, SimpleNamespace(provider=None, episode=None), 'sub', source,
        'ANILIST_SEARCH', {'provider': 'anipm'},
    )
    assert result == 'DETAILS'
    assert ms.show_id == '52034'
    assert ui.ui_show_ctx['_provider'] == 'senshi'
    assert ui.ui_show_ctx['_provider_id'] == '52034'
    assert int(ui.ui_show_ctx['malId']) == 52034
    assert catalogue.call_args.args[0]['_provider'] == 'senshi'


def test_invalid_match_opens_unlinked_details_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(anilist.app_core, 'get_al_match', lambda *a: {})
    monkeypatch.setattr(anilist.app_core, 'provider_display_name', lambda key: key)
    monkeypatch.setattr(anilist.app_core, 'with_loading', lambda message, fn, *a, **kw: fn(*a, **kw))
    monkeypatch.setattr(anilist.app_core, 'match_anilist_show_to_provider', lambda *a, **kw: {'name': 'Incomplete'})
    catalogue = Mock(side_effect=AssertionError('Invalid identity reached catalogue'))
    monkeypatch.setattr(anilist.app_core, 'load_episode_ids_for_selection', catalogue)
    ui, ms = SimpleNamespace(), SimpleNamespace()
    result = anilist._open_anilist_show_from_picker(
        SimpleNamespace(), ui, ms, SimpleNamespace(provider=None), 'sub', {'_id': '123', 'name': 'Example'},
        'ANILIST_SEARCH', {'provider': 'anipm'},
    )
    assert result == 'DETAILS'
    assert ui.ui_show_ctx['_has_provider_link'] is False
    assert ms.show_id == '123'
    catalogue.assert_not_called()
