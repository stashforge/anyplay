from types import SimpleNamespace

from media_cli.context import FLAGS
from media_cli.core.terminal import strip_ansi
from media_cli.ui import player_screen


def test_intentional_player_logs_keep_last_eight_lines_and_colors(monkeypatch):
    state = {'active':True,'status_lines':[]}
    monkeypatch.setattr(player_screen,'_player_ui_state',state)
    monkeypatch.setattr(FLAGS,'plain_mode',False)
    monkeypatch.setattr(player_screen,'render',lambda:None)
    for number in range(10):
        player_screen.add_status_line(f'Progress {number}', '\033[92m')
    assert [strip_ansi(line) for line in state['status_lines']] == [f'Progress {i}' for i in range(2,10)]
    assert all(line.startswith('\033[92m') for line in state['status_lines'])


def test_loading_frame_shows_progress_history_below_cancel_hint(monkeypatch):
    monkeypatch.setattr(player_screen,'_player_ui_state',dict(player_screen._player_ui_state))
    monkeypatch.setattr(FLAGS,'plain_mode',False)
    monkeypatch.setattr(player_screen,'sys',SimpleNamespace(stdin=SimpleNamespace(isatty=lambda:True,fileno=lambda:0)))
    monkeypatch.setattr(player_screen.os,'get_terminal_size',lambda fd:SimpleNamespace(columns=80,lines=40))
    monkeypatch.setattr(player_screen.terminal_images,'replace_for_frame',lambda **kw:'')
    frames=[]
    monkeypatch.setattr(player_screen,'_safe_stdout_write',lambda text,**kw:frames.append(text) or True)
    player_screen.activate({'name':'Title'},1,1)
    player_screen._store_status(player_screen._player_ui_state,'Found servers','\033[94m')
    player_screen._store_status(player_screen._player_ui_state,'Resolved stream','\033[92m')
    player_screen.render()
    frame=frames[-1]
    text=strip_ansi(frame)
    assert text.index('cancel') < text.index('Found servers') < text.index('Resolved stream')
    assert '\033[94mFound servers\033[0m' in frame
    assert '\033[92mResolved stream\033[0m' in frame
    assert '\033[40;1H' not in frame
