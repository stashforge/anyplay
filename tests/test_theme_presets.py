from media_cli.state import theme
from media_cli.state.config import DEFAULT_CONFIG, serialize_conf


def test_lavender_preserves_roles_and_status_semantics():
    mint, lavender = theme.MINT_THEME, theme.LAVENDER_THEME
    assert set(dict(mint.extensions)) == set(dict(lavender.extensions))
    assert mint.tab_active != lavender.tab_active
    assert lavender.resolve_style('screen.brand').foreground == 183
    for role in ('library.finished', 'library.progress', 'history.new_release', 'torrent.rose'):
        assert mint.resolve_style(role) == lavender.resolve_style(role)


def test_theme_override_and_invalid_fallback(monkeypatch):
    monkeypatch.setenv('ANYPLAY_THEME', 'lavender')
    assert theme.selected_theme_name() == 'lavender'
    monkeypatch.setenv('ANYPLAY_THEME', 'unknown')
    assert theme.selected_theme_name() == 'mint'


def test_config_documents_and_saves_preset():
    assert DEFAULT_CONFIG['theme'] == 'mint'
    assert 'theme = lavender' in serialize_conf(dict(DEFAULT_CONFIG, theme='lavender'))


def test_player_roles_preserve_mint_and_follow_lavender():
    from media_cli.ui.theme import TextStyle, BasicColor
    mint, lavender = theme.MINT_THEME, theme.LAVENDER_THEME
    assert mint.resolve_style('player.title') == TextStyle((145, 185, 245), bold=True)
    assert mint.resolve_style('player.status') == TextStyle(BasicColor.CYAN, bold=True)
    assert mint.resolve_style('player.title') != lavender.resolve_style('player.title')
    assert mint.resolve_style('player.section') != lavender.resolve_style('player.section')
    for role in ('player.progress', 'player.progress_empty', 'player.preferred', 'player.status'):
        assert mint.resolve_style(role) == lavender.resolve_style(role)


def test_player_uses_selected_preset_in_fresh_process():
    import os
    import subprocess
    import sys
    code = '''from media_cli.ui.player_screen import TITLE_COLOR, SECTION_LABEL
from media_cli.state.theme import MINT_SCREEN_THEME
assert TITLE_COLOR == MINT_SCREEN_THEME.prefix(MINT_SCREEN_THEME.resolve_style('player.title'))
assert SECTION_LABEL == MINT_SCREEN_THEME.prefix(MINT_SCREEN_THEME.resolve_style('player.section'))
'''
    for preset in ('mint', 'lavender'):
        result = subprocess.run([sys.executable, '-c', code],
                                env={**os.environ, 'ANYPLAY_THEME': preset},
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_progress_is_neutral_and_airing_is_distinct():
    for preset in (theme.MINT_THEME, theme.LAVENDER_THEME):
        assert preset.resolve_style('library.progress') == preset.resolve_style('library.value')
        airing = preset.resolve_style('library.airing')
        assert airing.foreground == 117
        assert not airing.bold
        assert preset.resolve_style('library.finished').foreground == 114
