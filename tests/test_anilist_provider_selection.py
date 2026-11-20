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
