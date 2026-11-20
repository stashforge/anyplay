from media_cli.state import config, paths
from media_cli.cli.args import parse_cli_args


def test_editor_priority(monkeypatch, tmp_path):
    path = tmp_path / 'config.conf'
    path.write_text('editor = nano\n')
    monkeypatch.setattr(paths, 'CONFIG_PATH', str(path))
    monkeypatch.setenv('VISUAL', 'vim')
    monkeypatch.setenv('EDITOR', 'micro')
    assert config.resolve_editor('code -w') == 'code -w'
    assert config.resolve_editor() == 'nano'
    path.write_text('# editor = nano\n')
    assert config.resolve_editor() == 'vim'
    monkeypatch.delenv('VISUAL')
    assert config.resolve_editor() == 'micro'


def test_saved_view_masks_credentials(monkeypatch, tmp_path, capsys):
    path = tmp_path / 'config.conf'
    path.write_text('# Mine\ntheme = lavender\ntmdb_token = SECRET\n# anilist_token = OTHER\n')
    monkeypatch.setattr(paths, 'CONFIG_PATH', str(path))
    config.print_saved_config()
    output = capsys.readouterr().out
    assert '# Mine\ntheme = lavender' in output
    assert 'SECRET' not in output and 'OTHER' not in output
    assert output.count('[hidden]') == 2
    assert 'provider =' not in output


def test_edit_selection():
    assert parse_cli_args(['config'])[0].config_editor is None
    assert parse_cli_args(['config', '-e'])[0].config_editor == ''
    assert parse_cli_args(['config', '-e', 'nano'])[0].config_editor == 'nano'
