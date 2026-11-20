import json
import pytest
from media_cli.state.config import (DEFAULT_CONFIG, CONFIG_MINIMUMS, CONFIG_CHOICES,
                                    normalize_config, load_config_file)


@pytest.mark.parametrize('key', DEFAULT_CONFIG)
@pytest.mark.parametrize('empty', [None, '', '   '])
def test_missing_or_empty_setting_uses_default(key, empty):
    assert normalize_config({key: empty})[key] == DEFAULT_CONFIG[key]
    assert normalize_config({})[key] == DEFAULT_CONFIG[key]


@pytest.mark.parametrize('key,minimum', CONFIG_MINIMUMS.items())
def test_numeric_bounds_preserve_zero_and_positive_values(key, minimum):
    assert normalize_config({key: minimum - 1})[key] == minimum
    assert normalize_config({key: minimum})[key] == minimum
    assert normalize_config({key: minimum + 5})[key] == minimum + 5
    for invalid in (True, [], {}, 'broken', float('nan'), float('inf'), 31.5):
        assert normalize_config({key: invalid})[key] == DEFAULT_CONFIG[key]


@pytest.mark.parametrize('key,choices', CONFIG_CHOICES.items())
def test_choice_settings_accept_supported_and_default_unsupported(key, choices):
    for choice in choices:
        assert normalize_config({key: choice.upper()})[key] == choice
    assert normalize_config({key: 'not-a-real-choice'})[key] == DEFAULT_CONFIG[key]


def test_false_zero_custom_paths_and_extension_keys_remain_valid():
    values = dict(search_image=False, torrent_min_seeders=0, log_backup_count=0,
                  search_image_path='/custom/image with spaces.png', custom_setting='anything',
                  download_sub_langs='en,ja', torrent_prefer_size=False)
    normalized = normalize_config(values)
    for key, value in values.items():
        assert normalized[key] == ('off' if key == 'torrent_prefer_size' else value)
    assert values['torrent_prefer_size'] is False


@pytest.mark.parametrize('suffix', ['.conf', '.json'])
def test_loader_normalizes_without_discarding_valid_settings(tmp_path, suffix):
    path = tmp_path / ('config' + suffix)
    data = dict(history_refresh_minutes=5, search_image='', quality='720p', theme='bad', custom='kept')
    path.write_text(json.dumps(data) if suffix == '.json' else '\n'.join(f'{key} = {value}' for key, value in data.items()))
    cfg = load_config_file(str(path), disabled=True)
    assert cfg['history_refresh_minutes'] == 30
    assert cfg['search_image'] is True
    assert cfg['theme'] == 'mint'
    assert cfg['quality'] == '720p'
    assert cfg['custom'] == 'kept'


def test_legacy_json_migration_uses_same_policy(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps(dict(default_provider='anipm', history_refresh_minutes=-1, theme=None)))
    cfg = load_config_file(str(tmp_path / 'config.conf'), disabled=True)
    assert cfg['history_refresh_minutes'] == 30
    assert cfg['theme'] == 'mint'
    assert cfg['provider'] == 'anipm'


def test_quality_and_supported_structured_legacy_values():
    assert normalize_config({'quality': 'invalid'})['quality'] == '1080p'
    assert normalize_config({'quality': '2160p'})['quality'] == '2160p'
    cfg = normalize_config({'spinner': ['.', '..'], 'disabled_providers': ['animegg', 'anidb']})
    assert cfg['spinner'] == ['.', '..']
    assert cfg['disabled_providers'] == 'animegg,anidb'
