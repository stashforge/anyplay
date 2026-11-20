"""Downloaded episode presentation retains shared-session actions and decisions."""
import os

import pytest
from test_ui_view_terminal import Terminal


def script():
    return '''
from media_cli.app.details_menu_controller import run_details_menu
from media_cli.app.episode_menu_view import EpisodeMenuOption, build_episode_menu_view
from media_cli.ui.confirmation import confirm_view_action
from media_cli.state.theme import MINT_SCREEN_THEME
from media_cli.context import UiState
show = dict(name="Example", _id="one", _local_progress=1)
ui = UiState(ui_show_ctx=show)
options = (EpisodeMenuOption("1", "Episode 1", watched=True, downloaded=True),
           EpisodeMenuOption("2", "Episode 2", downloaded=True))
decisions = []
feedback = ""
def view():
    return build_episode_menu_view(show, options, view_id="episodes:test", image_key="test", ttype="sub", local_only=True, show_cover=False, feedback=feedback)
presentation = dict(screen_title="Downloads · Episodes", summary_noun="episodes", placeholder="Filter episodes…", empty="No matching episodes",
                    info_fn=lambda i,w: ["Example", "No alternative title", "Metadata", "Genres"])
def delete(reference):
    global feedback
    decision = confirm_view_action(flags, ui, view(), item_id="episode:sub:" + reference,
        question="Delete downloaded EP " + reference + "?", theme=MINT_SCREEN_THEME, presentation=presentation)
    decisions.append(decision)
    feedback = "DELETED" if decision else "KEPT"
result = run_details_menu(flags, ui, read_view=view, switch_translation=lambda: None, information=lambda: None,
    action_handlers={"flip": lambda ref: None, "delete": delete}, presentation=presentation)
finish(dict(kind=result.kind, selected=result.action_id, decisions=decisions))
'''


def test_episode_filter_selection_preserves_episode_identity(tmp_path):
    with Terminal(tmp_path, script()) as terminal:
        terminal.send_after(b"Downloads \xc2\xb7 Episodes", b"/Episode 2\r")
        assert terminal.result() == {"kind": "selected", "selected": "2", "decisions": []}
        assert "✔".encode() in terminal.output


@pytest.mark.parametrize("key,confirmed", [(b"y", True), (b"n", False), (b"\x1b", False)])
def test_episode_delete_confirmation_stays_in_footer(tmp_path, key, confirmed):
    with Terminal(tmp_path, script()) as terminal:
        terminal.send_after(b"Downloads \xc2\xb7 Episodes", b"\x04")
        terminal.until(b"Delete downloaded EP 1? y/N")
        os.write(terminal.master, key)
        terminal.until(b"DELETED" if confirmed else b"KEPT")
        os.write(terminal.master, b"\x1b")
        assert terminal.result() == {"kind": "back", "selected": None, "decisions": [confirmed]}
