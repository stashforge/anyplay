"""Theme discovery without launching the media application."""

from .args_common import _help_color_enabled


def list_themes():
    from ..state.config import load_config_file
    from ..state.paths import CONFIG_PATH
    from ..state.theme import THEME_PRESETS

    saved = load_config_file(CONFIG_PATH, disabled=True).get('theme', 'mint')
    from ..state.theme_catalog import THEME_DESCRIPTIONS as descriptions
    color = _help_color_enabled()
    print('Themes:')
    for name, theme in THEME_PRESETS.items():
        marker = '✔' if name == saved else ' '
        swatches = ''
        if color:
            swatches = '  ' + ''.join(theme.prefix(theme.resolve_style(role)) + '■ ' + '\033[0m'
                                     for role in ('prompt', 'primary', 'secondary', 'muted', 'separator'))
        print(f'  {marker} {name:<10} {descriptions[name]}{swatches}')
    print('\n✔ Configured theme\nPreview: anyplay theme preview <name>\nSave:    anyplay config set theme <name>')
    return 0
