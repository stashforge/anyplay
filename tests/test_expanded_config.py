from media_cli.state.config_editor import expanded_view, merge_edits
from media_cli.state.config import load_config_file
from media_cli.cli.args import parse_cli_args


def test_view_and_merge_keep_real_order_comments_and_sparse_keys():
    original = '# My settings\nprovider = anipm\ntheme = lavender\n'
    view = expanded_view(original)
    assert 'provider = anipm' in view and 'theme = lavender' in view
    assert '# history_refresh_minutes = 60' in view
    edited = view.replace('provider = anipm', 'provider = animepahe').replace('# history_refresh_minutes = 60', 'history_refresh_minutes = 90')
    assert merge_edits(original, edited) == '# My settings\nprovider = animepahe\ntheme = lavender\nhistory_refresh_minutes = 90\n'
    assert merge_edits(original, view) == original
    assert merge_edits(original, view.replace('theme = lavender', '# theme = lavender')) == '# My settings\nprovider = anipm\n'


def test_sparse_load_does_not_restore_deleted_defaults(tmp_path):
    path = tmp_path / 'config.conf'
    text = '# Keep my ordering\ntheme = lavender\n'
    path.write_text(text)
    assert load_config_file(str(path))['provider'] == 'anipm'
    assert path.read_text() == text
    path.write_text(text + 'history_refresh_minutes = 5\n')
    assert load_config_file(str(path))['history_refresh_minutes'] == 30
    assert path.read_text() == text + 'history_refresh_minutes = 30\n'


def test_custom_settings_and_quoted_paths_preserved():
    original = '# custom\ncustom = "hello world"\ndownload_dir = "/tmp/a=b"\n'
    assert merge_edits(original, expanded_view(original)) == original


def test_edit_flag_optional_editor_and_expanded():
    args, _ = parse_cli_args(['config', '-e', '--expanded'])
    assert args.expanded and args.config_editor == ''
    args, _ = parse_cli_args(['config', '-e', 'nano', '--expanded'])
    assert args.config_editor == 'nano'


def test_editor_merge_and_failure_leave_real_file_safe(tmp_path, monkeypatch):
    from media_cli.state import config, paths
    from types import SimpleNamespace
    from pathlib import Path
    import subprocess
    path = tmp_path / 'config.conf'
    original = '# Mine\ntheme = lavender\n'
    path.write_text(original)
    monkeypatch.setattr(paths, 'CONFIG_PATH', str(path))
    monkeypatch.setattr(config, 'resolve_editor', lambda *args: 'fake-editor')
    def edit(command):
        view = Path(command[-1])
        assert view.suffix == '.conf'
        assert view.stat().st_mode & 0o777 == 0o600
        view.write_text(view.read_text().replace('# history_refresh_minutes = 60', 'history_refresh_minutes = 90'))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', edit)
    assert config.open_config_editor(expanded=True) == 0
    assert path.read_text() == original + 'history_refresh_minutes = 90\n'
    def fail(command):
        Path(command[-1]).write_text('theme = mint\n')
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(subprocess, 'run', fail)
    assert config.open_config_editor(expanded=True) == 1
    assert path.read_text() == original + 'history_refresh_minutes = 90\n'
