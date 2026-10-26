"""Python stdout belongs to explicit UI writers during an interactive screen."""

import threading

from media_cli.core.terminal import strip_ansi
from media_cli.ui import player_screen


def test_stdout_guard_retains_accidental_text_and_allows_owned_frames(tmp_path):
    from test_terminal_stderr_capture import child

    output = child(f"""
import os,sys,threading
from pathlib import Path
from media_cli.state import paths
from media_cli.ui.stderr_capture import begin_stderr_capture,end_stderr_capture
from media_cli.ui.terminal_output import write_terminal
paths.LOG_DIR={str(tmp_path)!r}
begin_stderr_capture()
print('UNOWNED-PYTHON-TEXT',flush=True)
sys.stdout.buffer.write(b'UNOWNED-BINARY-TEXT\\n')
t=threading.Thread(target=lambda:print('WORKER-TEXT',flush=True))
t.start();t.join()
write_terminal('OWNED-FRAME\\n')
end_stderr_capture()
print('NORMAL-AFTER-RESTORE')
text=Path(paths.LOG_DIR,'native.log').read_text()
assert all(value in text for value in ('UNOWNED-PYTHON-TEXT','UNOWNED-BINARY-TEXT','WORKER-TEXT'))
""", tmp_path)
    assert 'OWNED-FRAME' in output and 'NORMAL-AFTER-RESTORE' in output
    assert 'UNOWNED-PYTHON-TEXT' not in output
    assert 'UNOWNED-BINARY-TEXT' not in output and 'WORKER-TEXT' not in output


def test_player_report_preserves_history_and_never_draws_from_worker(monkeypatch):
    state = {'active': True, 'status_lines': [], 'mpv_props': None}
    monkeypatch.setattr(player_screen, '_player_ui_state', state)
    monkeypatch.setattr(player_screen, 'render', lambda **kwargs: (_ for _ in ()).throw(
        AssertionError('Reporting must not draw')))
    worker = threading.Thread(target=lambda: (
        player_screen.add_status_line('old'),
        player_screen.add_status_line('[WARN] line one\nline two\x1b[2J' + '界' * 90),
    ))
    worker.start()
    worker.join()
    assert len(state['status_lines']) == 2
    assert strip_ansi(state['status_lines'][0]) == 'old'
    line = state['status_lines'][-1]
    assert '\n' not in line and '\x1b[2J' not in line


def test_inactive_player_does_not_accept_reports(monkeypatch):
    monkeypatch.setattr(player_screen, '_player_ui_state', {'active': False})
    assert not player_screen.add_status_line('outside')


def test_console_status_reports_data_to_ui_and_logs_full_diagnostics(monkeypatch, capsys, caplog):
    from media_cli.core import reporting

    messages = []
    with caplog.at_level('INFO', logger='media_cli.reporting'):
        with reporting.status_sink_scope(lambda text, color: messages.append(text) or True):
            reporting.console_status('install helper\nwith details', kind='warning')
    assert messages == ['[WARN] install helper with details']
    assert not capsys.readouterr().out
    assert 'install helper with details' in caplog.text


def test_background_child_stdout_is_routed_to_diagnostics(tmp_path):
    from test_terminal_stderr_capture import child

    output = child(f"""
import subprocess,sys
from pathlib import Path
from media_cli.state import paths
from media_cli.ui.stderr_capture import begin_stderr_capture,end_stderr_capture
from media_cli.core.processes import diagnostic_subprocess_output
paths.LOG_DIR={str(tmp_path)!r}
begin_stderr_capture()
subprocess.run([sys.executable,'-c',"print('CHILD-STDOUT')"],check=True,**diagnostic_subprocess_output())
end_stderr_capture()
assert 'CHILD-STDOUT' in Path(paths.LOG_DIR,'native.log').read_text()
""", tmp_path)
    assert 'CHILD-STDOUT' not in output


def test_deferred_diagnostics_are_data_until_cli_restores_terminal(monkeypatch, capsys):
    from media_cli.cli import application
    from media_cli.core import reporting

    events = []
    reporting.take_deferred_notices()
    monkeypatch.setenv('ALLMANGA_NO_OS_EXIT', '1')
    monkeypatch.setattr(application, '_cleanup', lambda: None)
    monkeypatch.setattr(application.app, 'restore_terminal', lambda: events.append('restored'))
    def app_main(parsed):
        reporting.defer_notice('Diagnostic log unavailable')
        assert capsys.readouterr().err == ''
    monkeypatch.setattr(application.app, 'app_main', app_main)
    assert application.run(object()) == 0
    assert events == ['restored']
    assert 'Diagnostic log unavailable' in capsys.readouterr().err
    assert reporting.take_deferred_notices() == ()


def test_download_exit_choice_stays_visible_under_stdout_guard(tmp_path):
    from test_terminal_stderr_capture import child
    output = child(f"""
import os,sys
from media_cli.state import paths
from media_cli.ui.stderr_capture import begin_stderr_capture,end_stderr_capture
from media_cli.ui.download_manager import prompt_download_exit_cli
paths.LOG_DIR={str(tmp_path)!r}
sys.stdin.isatty=lambda:True
begin_stderr_capture()
read=os.read
os.read=lambda fd,size:b'1' if fd==sys.stdin.fileno() else read(fd,size)
try:
    assert prompt_download_exit_cli(2,3)=='STOP_QUIT'
finally:
    os.read=read
    end_stderr_capture()
""", tmp_path)
    assert 'What would you like to do?' in output


def test_full_player_exit_restores_requested_console_output(tmp_path):
    from test_terminal_stderr_capture import child
    output = child(f"""
from media_cli.state import paths
from media_cli.ui import player_screen
from media_cli.ui.stderr_capture import begin_stderr_capture,end_stderr_capture
paths.LOG_DIR={str(tmp_path)!r}
begin_stderr_capture()
player_screen.activate({{}},1,1)
player_screen.deactivate(close_alt=True)
print('REQUESTED-STREAM-URL',flush=True)
end_stderr_capture()
""", tmp_path)
    assert 'REQUESTED-STREAM-URL' in output


def test_plain_player_does_not_swallow_console_status(monkeypatch, capsys):
    from media_cli.context import FLAGS
    from media_cli.core import reporting
    monkeypatch.setattr(FLAGS, 'plain_mode', True)
    monkeypatch.setattr(player_screen, '_player_ui_state', {'active': True, 'status_lines': []})
    with reporting.status_sink_scope(player_screen.add_status_line):
        reporting.console_status('Visible plain progress')
        reporting.info('Visible plain information')
    output = capsys.readouterr().out
    assert 'Visible plain progress' in output
    assert 'Visible plain information' in output


def test_short_player_keeps_original_controls_without_added_footer(monkeypatch):
    import os

    from media_cli.context import FLAGS
    monkeypatch.setattr(FLAGS, 'plain_mode', False)
    monkeypatch.setattr(player_screen, '_player_ui_state', dict(player_screen._player_ui_state))
    monkeypatch.setattr(player_screen.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(player_screen.sys.stdin, 'fileno', lambda: 0)
    monkeypatch.setattr(player_screen.os, 'get_terminal_size', lambda fd: os.terminal_size((80,12)))
    monkeypatch.setattr(player_screen.terminal_images, 'replace_for_frame', lambda **kw: '')
    frames = []
    monkeypatch.setattr(player_screen, '_safe_stdout_write', lambda text, **kw: frames.append(text) or True)
    player_screen.activate({'name':'Title'},1,1)
    player_screen._player_ui_state['mpv_props'] = {'duration':100,'playback-time':1}
    player_screen.render()
    body = frames[-1].split('\x1b[H',1)[1].split('\x1b[J',1)[0]
    rows = body.split('\r\n')
    assert len(rows) <= 12
    assert 'Q Quit' in rows[-1]
    assert '\x1b[12;1H' not in frames[-1]


def test_saved_stdout_reference_cannot_leak_after_capture_closes(tmp_path):
    from test_terminal_stderr_capture import child
    output = child(f"""
import sys
from media_cli.state import paths
from media_cli.ui.stderr_capture import begin_stderr_capture,end_stderr_capture
paths.LOG_DIR={str(tmp_path)!r}
begin_stderr_capture()
saved=sys.stdout
saved.writelines(['BEFORE-CLOSE\\n'])
end_stderr_capture()
saved.write('LATE-REFERENCE-MUST-NOT-DRAW\\n')
saved.buffer.write(b'LATE-BINARY-MUST-NOT-DRAW\\n')
print('NORMAL-OUTPUT',flush=True)
""", tmp_path)
    assert 'NORMAL-OUTPUT' in output
    assert 'MUST-NOT-DRAW' not in output and 'BEFORE-CLOSE' not in output


def test_stdout_guard_also_respects_redirected_stderr(tmp_path):
    from test_terminal_stderr_capture import child
    output = child(f"""
import os,sys
from pathlib import Path
from media_cli.ui.stderr_capture import begin_stderr_capture,end_stderr_capture
path=Path({str(tmp_path / 'caller.log')!r})
with path.open('wb') as target:
    os.dup2(target.fileno(),2)
    begin_stderr_capture()
    print('CALLER-DIAGNOSTIC',flush=True)
    end_stderr_capture()
assert 'CALLER-DIAGNOSTIC' in path.read_text()
print('NORMAL-OUTPUT',flush=True)
""", tmp_path)
    assert 'NORMAL-OUTPUT' in output and 'CALLER-DIAGNOSTIC' not in output


def test_plain_picker_fallback_remains_visible_under_guard(tmp_path):
    from test_terminal_stderr_capture import child
    output = child(f"""
from types import SimpleNamespace
from media_cli.state import paths
from media_cli.ui.stderr_capture import begin_stderr_capture,end_stderr_capture
from media_cli.ui.plain_picker import plain_pick
paths.LOG_DIR={str(tmp_path)!r}
begin_stderr_capture()
result=plain_pick(SimpleNamespace(plain_mode=True),SimpleNamespace(active_picker_query=''),
    'Fallback prompt',['VISIBLE-FALLBACK-ROW'],input_fn=lambda prompt:'1')
assert result==0
end_stderr_capture()
""", tmp_path)
    assert 'VISIBLE-FALLBACK-ROW' in output


def test_loading_status_stays_with_loading_block_instead_of_screen_bottom(monkeypatch):
    import os

    from media_cli.context import FLAGS
    monkeypatch.setattr(FLAGS, 'plain_mode', False)
    monkeypatch.setattr(player_screen, '_player_ui_state', dict(player_screen._player_ui_state))
    monkeypatch.setattr(player_screen.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(player_screen.sys.stdin, 'fileno', lambda: 0)
    monkeypatch.setattr(player_screen.os, 'get_terminal_size', lambda fd: os.terminal_size((80,40)))
    monkeypatch.setattr(player_screen.terminal_images, 'replace_for_frame', lambda **kw: '')
    frames = []
    monkeypatch.setattr(player_screen, '_safe_stdout_write', lambda text, **kw: frames.append(text) or True)
    player_screen.activate({'name':'Title'},1,1)
    player_screen.add_status_line('Requesting episode metadata')
    player_screen.render()
    frame = frames[-1]
    body = frame.split('\x1b[H',1)[1].split('\x1b[J',1)[0]
    assert 'Requesting episode metadata' in strip_ansi(body)
    assert 'Requesting episode metadata' not in strip_ansi(frame.split('\x1b[J',1)[1])
    rows = strip_ansi(body).split('\r\n')
    assert rows[-1] == 'Requesting episode metadata'
    assert 'cancel' in rows[-3].lower()
