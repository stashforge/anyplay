"""Plain-mode imports must not initialize the TUI compatibility surface."""

import subprocess
import sys

import pytest


def run_script(script):
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return result


@pytest.mark.parametrize(
    "module",
    [
        "media_cli.ui",
        "media_cli.ui.plain_picker",
        "media_cli.ui.view_plain",
        "media_cli.ui.renderer_selection",
        "media_cli.ui.view_renderer",
        "media_cli.ui.view_picker",
    ],
)
def test_importing_package_or_plain_modules_does_not_load_tui(module):
    result = run_script(
        f"import importlib, sys; importlib.import_module({module!r}); "
        "assert 'media_cli.ui.picker' not in sys.modules; "
        "assert 'media_cli.ui.player_screen' not in sys.modules; "
        "assert 'media_cli.ui.modals' not in sys.modules; "
        "assert 'media_cli.ui.view_tui' not in sys.modules"
    )
    assert result.stdout == ""


def test_old_public_exports_still_resolve_to_original_objects():
    run_script(
        "import importlib; import media_cli.ui as ui; "
        "expected = 'print_app_banner print_episode_header confirm_auto_anilist_match "
        "confirm_auto_match make_shows_info_fn make_single_show_info_fn manual_anilist_input_header "
        "manual_match_input_header search_cover_header search_input_header search_result_header "
        "select_provider_for_match render_header_card render_menu_card render_modal_card "
        "render_search_header tui_pick activate add_status_line deactivate render update_mpv_props "
        "update_stream_info'.split(); "
        "assert set(ui.__all__) == set(expected); "
        "assert callable(ui.tui_pick); "
        "assert ui._player_ui_state is importlib.import_module('media_cli.ui.player_screen')._player_ui_state; "
        "assert 'tui_pick' in ui.__all__; "
        "assert 'render_header_card' in dir(ui); "
        "assert all(getattr(ui, name) is getattr(importlib.import_module('media_cli.ui.' + module), name) "
        "for name, module in ui._EXPORTS.items()); "
        "from media_cli.ui import tui_pick; "
        "assert tui_pick is ui.tui_pick"
    )


def test_standard_submodule_imports_and_unknown_attributes_keep_python_behavior():
    run_script(
        "import media_cli.ui as ui; from media_cli.ui import plain_picker; "
        "assert callable(plain_picker.plain_pick); "
        "assert getattr(ui, 'not_a_ui_export', None) is None; "
        "assert not hasattr(ui, 'not_a_ui_export')"
    )
