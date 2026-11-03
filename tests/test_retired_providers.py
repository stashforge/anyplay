"""Archived providers are absent from the CLI without rewriting history IDs."""
import importlib.util
from pathlib import Path

import pytest

from media_cli import providers
from media_cli.provider_manifest import PROVIDER_REGISTRY
from media_cli.state.config import DEFAULT_CONFIG, migrate_config_keys


@pytest.mark.parametrize('key', ['miruro', 'mkissa', 'allanime'])
def test_retired_provider_is_not_selectable_or_silently_replaced(key):
    assert key not in PROVIDER_REGISTRY
    assert key not in providers.PROVIDERS
    assert key not in providers.PROVIDER_FACTORIES
    assert key not in providers.available_providers()
    assert not providers.is_provider_active(key)
    with pytest.raises(ValueError, match='no longer included'):
        providers.get_provider(key)


@pytest.mark.parametrize('key', ['miruro', 'mkissa', 'allanime'])
def test_removed_saved_default_migrates_without_changing_unrelated_settings(key):
    config = {'provider': key, 'quality': '720p', 'anilist_token': 'test-token'}
    assert migrate_config_keys(config)
    assert config == {'provider': 'anipm', 'quality': '720p', 'anilist_token': 'test-token'}
    assert DEFAULT_CONFIG['provider'] == 'anipm'


@pytest.mark.parametrize('module', ['miruro', 'allanime'])
def test_provider_source_is_archived_outside_distributed_package(module):
    assert importlib.util.find_spec('media_cli.providers.' + module) is None
    archive = Path(__file__).resolve().parents[1] / 'archive' / 'providers' / '2026-10-28'
    assert (archive / 'media_cli' / 'providers' / (module + '.py')).is_file()


def test_retired_history_refresh_never_fetches_from_replacement_provider(monkeypatch):
    from media_cli.domain.history import refresh_history_entry_provider_catalog
    from media_cli.services import catalog
    monkeypatch.setattr(catalog, 'get_allanime_show', lambda *a: pytest.fail('Retired history used default provider'))
    monkeypatch.setattr(catalog, 'fetch_episode_catalog', lambda *a, **kw: pytest.fail('Retired history requested catalogue'))
    for key in ('miruro', 'mkissa', 'allanime'):
        entry = {'show': {'_id': 'one', '_provider': key, 'name': 'Saved', 'watched_episodes': 3}}
        refresh_history_entry_provider_catalog(entry)
        assert entry['show']['_provider'] == key
        assert entry['show']['watched_episodes'] == 3
