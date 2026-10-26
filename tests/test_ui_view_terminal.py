"""Exercise the production view adapter and picker through a controlling PTY."""

import base64
import errno
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import textwrap
import time
import zlib
from pathlib import Path

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.covers import chafa_cover_command, poster_symbol_lines
from media_cli.ui.layout import MIN_TUI_COLUMNS


ROOT = Path(__file__).resolve().parents[1]
PRELUDE = """
import json
import os
import sys
import threading
import time
from dataclasses import asdict, replace
from types import SimpleNamespace

def terminal_marker(*values, sep=' ', end='\\n', flush=True):
    # Test-control markers are explicit instrumentation, not production output.
    # Use a separate writer so application stdout containment remains exercised.
    sys.__stdout__.write(sep.join(str(value) for value in values) + end)
    if flush:
        sys.__stdout__.flush()

def forbid_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo'}:
        raise AssertionError('Terminal tests must not access the network')
sys.addaudithook(forbid_network)

from media_cli.ui import picker as picker_module
from media_cli.ui.poster import PosterManager
from media_cli.ui.view_model import Content, Blank, Inherit, resolve_details, DetailLines, ImageSpec, SelectionPolicy, ViewAction, ViewItem, ViewSpec
from media_cli.ui.view_session import ExitSession, PopView, PushView, ReplaceView, UpdateItems, ViewSession
from media_cli.ui.view_picker import run_view_session

flags = SimpleNamespace(plain_mode=False, show_image=False)
ui = SimpleNamespace(active_picker_query='', hovered_show_id=None, hovered_show_obj=None)
def redraw():
    picker_module._needs_redraw = True
manager = PosterManager(
    enabled=lambda: flags.show_image and not flags.plain_mode,
    cache_dir=lambda: os.path.join(os.environ['HOME'], 'covers'),
    hovered_show_id=lambda: ui.hovered_show_id,
    request_redraw=redraw,
    loading_frame=lambda: '~',
)
def finish(value):
    print('RESULT:' + json.dumps(value, default=sorted) + 'END_RESULT', flush=True)
"""


def _own_terminal():
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def terminal_environment(home):
    # A synthetic xterm must not inherit the host terminal's graphics hints.
    return dict(
        PATH=os.environ.get('PATH', os.defpath), HOME=str(home),
        XDG_CONFIG_HOME=str(home / 'config'), XDG_CACHE_HOME=str(home / 'cache'),
        XDG_STATE_HOME=str(home / 'state'), TERM='xterm-256color',
        LANG='C.UTF-8', LC_ALL='C.UTF-8', PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT),
    )


class Terminal:
    def __init__(self, tmp_path, script, *, rows=35, columns=100):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
        home = tmp_path / 'home'
        home.mkdir()
        env = terminal_environment(home)
        self.proc = subprocess.Popen(
            [sys.executable, '-c', PRELUDE + textwrap.dedent(script)],
            stdin=slave, stdout=slave, stderr=slave, cwd=tmp_path, env=env,
            preexec_fn=_own_terminal, close_fds=True,
        )
        os.close(slave)
        self.output = bytearray()
        self.cursor = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=2)
        os.close(self.master)

    def until(self, marker, timeout=6):
        start = self.cursor
        deadline = time.monotonic() + timeout
        while True:
            found = self.output.find(marker, start)
            if found >= 0:
                self.cursor = found + len(marker)
                return bytes(self.output[start:self.cursor])
            if time.monotonic() >= deadline:
                pytest.fail(f'Timed out waiting for {marker!r}; tail={bytes(self.output[-5000:])!r}')
            if not select.select([self.master], [], [], 0.05)[0]:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                data = b''
            if not data:
                pytest.fail(f'PTY closed before {marker!r}; tail={bytes(self.output[-5000:])!r}')
            self.output.extend(data)

    def send_after(self, marker, keys):
        self.until(marker)
        # Bytes already read belong to the current frame, not a future view.
        self.cursor = len(self.output)
        os.write(self.master, keys)

    def resize(self, rows, columns):
        self.cursor = len(self.output)
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
        os.kill(self.proc.pid, signal.SIGWINCH)
        return self.until(f'\033[{rows};{columns}H'.encode())

    def result(self):
        self.until(b'END_RESULT')
        self.proc.wait(timeout=2)
        assert self.proc.returncode == 0, bytes(self.output[-5000:])
        payload = bytes(self.output).split(b'RESULT:', 1)[1].split(b'END_RESULT', 1)[0]
        return json.loads(payload)


def row_writes(output):
    """Read absolute row writes from actual renderer output, without emulating UI."""
    pattern = rb'\x1b\[(\d+);1H\x1b\[2K(.*?)(?=\x1b\[\d+;\d+H|\Z)'
    return [(int(match[1]), strip_ansi(match[2].decode('utf-8'))) for match in re.finditer(pattern, output, re.S)]


@pytest.mark.parametrize('state', ['loading', 'empty', 'failed', 'ready'])
def test_empty_view_status_is_not_a_result_and_back_remains_available(tmp_path, state):
    script = f'''
initial = ViewSpec('root', 'Select group', state={state!r}, message='Waiting for catalog',
                   details=DetailLines(Content('Artist: Example'), Blank, Content('4 groups')))
session = ViewSession(initial)
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.until(b'Waiting for catalog')
        terminal.until(b'\033[35;100H')
        output = bytes(terminal.output)
        assert b'0/1' not in output
        rows = row_writes(output)
        assert not any('Waiting for catalog' in text for row, text in rows if row < 35 - 1)
        os.write(terminal.master, b'\x1b')
        assert terminal.result() is None


def test_empty_loading_view_accepts_async_results_without_fake_rows(tmp_path):
    script = '''
initial = ViewSpec('root', 'Select group', state='loading', message='Fetching catalog', details=DetailLines(Blank, Blank, Blank))
session = ViewSession(initial, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
generation = session.generation
def complete():
    time.sleep(0.5)
    session.post(ReplaceView('root', generation,
        ViewSpec('root', 'Select group', (ViewItem('song', 'Actual song', details=Inherit),), details=DetailLines(Blank, Blank, Blank))))
threading.Thread(target=complete, daemon=True).start()
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.until(b'Fetching catalog')
        terminal.send_after(b'Actual song', b'\r')
        assert terminal.result() == 'song'
        assert b'0/1' not in terminal.output


def tiny_png():
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))

    pixels = b'\x00\xff\x00\x00\x00\xff\x00\x00\x00\x00\xff\xff\xff\xff'
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 2, 2, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


@pytest.mark.parametrize('protocol', ['symbols', 'kitty', 'sixel'])
def test_idle_resolved_cover_reaches_terminal_and_clears_loading_footer(tmp_path, protocol):
    # Manager work is deterministic; all adapter/rendering code is production.
    packet = '\033_Ga=T,f=100,t=d;' + base64.b64encode(tiny_png()).decode() + '\033\\'
    if protocol == 'sixel':
        packet = '\033P0;1;0q"1;1;1;1#0;2;100;0;0#0~\033\\'
    payload = 'POSTER-SYMBOL\nSECOND-ROW' if protocol == 'symbols' else packet
    script = f"""
flags.show_image = True
payload = {payload!r}
class DeterministicPoster(PosterManager):
    first_get = None
    def get(self, show):
        if self.first_get is None:
            self.first_get = time.monotonic()
        if time.monotonic() - self.first_get < 0.35:
            self.set_status(show, 'loading')
            return ''
        show['_poster_raw'] = payload
        self.set_status(show, 'ready')
        return payload
manager.__class__ = DeterministicPoster
item = ViewItem('artist', 'Artist', image=ImageSpec('portrait', 'resolving'), details=Inherit)
details = DetailLines(Content('Unchanged artist'), Content('Unchanged secondary'), Content('Unchanged metadata'))
session = ViewSession(ViewSpec('root', 'COVER-IDLE', (item,), details=details, context=('Cover context',)))
started = time.monotonic()
def complete():
    time.sleep(3.25)
    session.post(UpdateItems('root', session.generation, (replace(item, image=ImageSpec('portrait', 'available', 'https://example.invalid/cover')),)))
worker = threading.Thread(target=complete)
worker.start()
run_view_session(flags, ui, session, poster_manager=manager)
worker.join()
finish(dict(elapsed=time.monotonic()-started, details=asdict(resolve_details(session.current.details)), status=ui.hovered_show_obj['_poster_status']))
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.until(b'Fetching cover')
        loading = terminal.until(b'Loading cover')
        assert payload.encode() not in terminal.output
        terminal.until(packet.encode() if protocol != 'symbols' else b'POSTER-SYMBOL')
        if protocol == 'symbols':
            terminal.until(b'SECOND-ROW')
        terminal.until(b'\033[35;100H')
        frame_output = bytes(terminal.output)
        os.write(terminal.master, b'\x03')
        result = terminal.result()
    assert result['elapsed'] > 3
    assert result['status'] == 'ready'
    assert result['details'] == dict(title='Unchanged artist', secondary='Unchanged secondary', metadata='Unchanged metadata')
    assert b'Loading cover' in loading
    footer = dict(row_writes(frame_output))[35]
    assert 'Cover context' in footer
    assert 'Fetching cover' not in footer and 'Loading cover' not in footer
    if protocol != 'symbols':
        assert frame_output.count(packet.encode()) == 1
    else:
        assert any('POSTER-SYMBOL' in text for _, text in row_writes(frame_output))


def test_late_cover_completion_cannot_replace_focused_items_image(tmp_path):
    packet_a = '\033_Ga=T,f=24,s=1,v=1,t=d;/wAA\033\\'
    packet_b = '\033_Ga=T,f=24,s=1,v=1,t=d;AAD/\033\\'
    observed = tmp_path / 'late-completion-observed'
    script = f"""
from pathlib import Path
flags.show_image = True
release_a, completed_a = threading.Event(), threading.Event()
held = {{}}
selected_at_completion = []
reads_after_completion = []
class FocusRacePoster(PosterManager):
    def get(self, show):
        if show['thumbnail'].endswith('/a'):
            held['a'] = show
            if not completed_a.is_set():
                self.set_status(show, 'loading')
        else:
            held['b'] = show
            show.setdefault('_poster_raw', {packet_b!r})
            self.set_status(show, 'ready')
            if completed_a.is_set():
                reads_after_completion.append(show['_id'])
                Path({str(observed)!r}).write_text('observed')
        return show.get('_poster_raw', '')
manager.__class__ = FocusRacePoster
items = tuple(ViewItem(key, key.upper(), image=ImageSpec(key, 'available', 'https://example.invalid/' + key), details=Inherit) for key in ('a', 'b'))
view = ViewSpec('root', 'FOCUS-RACE', items, reverse_items=False,
                actions=(ViewAction('complete-a', 'Complete A', ('CTRL_R',)),), details=DetailLines(Blank, Blank, Blank))
def handle(event):
    if event.kind == 'action':
        release_a.set()
    elif event.kind == 'select':
        return ExitSession(event.item_id)
session = ViewSession(view, handle)
def complete_a():
    if not release_a.wait(3):
        return
    selected_at_completion.append(session.position.selected_id)
    held['a']['_poster_raw'] = {packet_a!r}
    manager.set_status(held['a'], 'ready')
    completed_a.set()
    redraw()
worker = threading.Thread(target=complete_a, daemon=True)
worker.start()
value = run_view_session(flags, ui, session, poster_manager=manager)
worker.join(timeout=1)
assert not worker.is_alive()
finish(dict(value=value, selected=session.position.selected_id, completed=completed_a.is_set(),
            selected_at_completion=selected_at_completion, a_id=held['a']['_id'], b_id=held['b']['_id'],
            hovered_id=ui.hovered_show_id, reads_after_completion=reads_after_completion))
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Loading cover', b'\x1b[B')
        terminal.until(packet_b.encode())
        terminal.until(b'\033[35;100H')
        os.write(terminal.master, b'\x12')
        deadline = time.monotonic() + 3
        while not observed.exists():
            assert time.monotonic() < deadline, bytes(terminal.output[-2000:])
            if select.select([terminal.master], [], [], 0.05)[0]:
                terminal.output.extend(os.read(terminal.master, 65536))
        os.write(terminal.master, b'\r')
        result = terminal.result()
        rendered = bytes(terminal.output).split(b'RESULT:', 1)[0]
    assert result['completed'] and result['selected_at_completion'] == ['b']
    assert result['value'] == result['selected'] == 'b'
    assert result['a_id'] != result['b_id'] == result['hovered_id']
    assert result['reads_after_completion']
    assert set(result['reads_after_completion']) == {result['b_id']}
    assert packet_b.encode() in rendered
    assert packet_a.encode() not in rendered


@pytest.mark.skipif(not shutil.which('chafa'), reason='chafa is required to decode the cached PNG')
@pytest.mark.parametrize('host_terminal', ('xterm', 'konsole'))
def test_real_cached_png_is_decoded_by_chafa_and_written_to_pty(tmp_path, monkeypatch, host_terminal):
    if host_terminal == 'konsole':
        monkeypatch.setenv('KONSOLE_VERSION', '260800')
    png = tiny_png()
    source = tmp_path / 'tiny.png'
    source.write_bytes(png)
    env = terminal_environment(tmp_path)
    rendered = subprocess.run(chafa_cover_command(str(source)), capture_output=True, text=True, env=env, timeout=3)
    assert rendered.returncode == 0, rendered.stderr
    symbols = poster_symbol_lines(rendered.stdout.rstrip('\n'), 8, 100)
    visible_lines = [line for line in symbols if strip_ansi(line).strip()]
    assert visible_lines, f'Expected xterm symbols, got {rendered.stdout[:300]!r}; stderr={rendered.stderr!r}'
    expected_line = visible_lines[0]
    script = f"""
import base64
import hashlib
from pathlib import Path
flags.show_image = True
url = 'https://example.invalid/cached-cover'
cache = Path(manager.cache_dir())
cache.mkdir()
(cache / (hashlib.sha256(url.encode()).hexdigest()[:32] + '.jpg')).write_bytes(base64.b64decode({base64.b64encode(png).decode()!r}))
item = ViewItem('artist', 'Artist', image=ImageSpec('portrait', 'available', url), details=Inherit)
session = ViewSession(ViewSpec('root', 'REAL-PNG', (item,), context=('PNG context',), details=DetailLines(Blank, Blank, Blank)))
run_view_session(flags, ui, session, poster_manager=manager)
finish(dict(status=ui.hovered_show_obj['_poster_status'], raw=ui.hovered_show_obj.get('_poster_raw', '')))
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.until(expected_line.encode())
        terminal.until(b'\033[35;100H')
        output = bytes(terminal.output)
        os.write(terminal.master, b'\x03')
        result = terminal.result()
    assert result['status'] == 'ready'
    assert result['raw'] == rendered.stdout.rstrip('\n')
    assert expected_line.encode() in output
    assert 'Loading cover' not in dict(row_writes(output))[35]


def test_nested_back_restores_selected_ids_filter_and_scroll(tmp_path):
    script = """
snapshots = []
class RecordingSession(ViewSession):
    def apply(self, command):
        before = (self.current.id, asdict(self.position))
        accepted = super().apply(command)
        if isinstance(command, PopView):
            snapshots.append(dict(before=before, after=(self.current.id, asdict(self.position))))
        return accepted
root = ViewSpec('root', 'ROOT-NAV', tuple(ViewItem(f's{i}', f'song {i:02}', details=DetailLines(Content(f'Focused root {i}'), Blank, Blank)) for i in range(60)), reverse_items=False, details=DetailLines(Blank, Blank, Blank))
child = ViewSpec('child', 'CHILD-NAV', tuple(ViewItem(f'v{i}', f'verse {i:02}', details=DetailLines(Content(f'Focused child {i}'), Blank, Blank)) for i in range(60)), reverse_items=False, details=DetailLines(Blank, Blank, Blank))
grand = ViewSpec('grand', 'GRAND-NAV', (ViewItem('leaf', 'Leaf', details=Inherit),), reverse_items=False, details=DetailLines(Blank, Blank, Blank))
saved = {}
def handle(event):
    if event.kind != 'select':
        return
    if event.view_id == 'root' and 'root' not in saved:
        saved['root'] = asdict(session.position)
        return PushView(child)
    if event.view_id == 'child':
        saved['child'] = asdict(session.position)
        return PushView(grand)
    return ExitSession('done')
session = RecordingSession(root, handle)
value = run_view_session(flags, ui, session, poster_manager=manager)
finish(dict(value=value, saved=saved, snapshots=snapshots, final=asdict(session.position)))
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'ROOT-NAV', b'song' + b'\x1b[B' * 40)
        terminal.send_after(b'Focused root 40', b'\r')
        terminal.send_after(b'CHILD-NAV', b'verse' + b'\x1b[B' * 36)
        terminal.send_after(b'Focused child 36', b'\r')
        terminal.send_after(b'GRAND-NAV', b'\x1b[D')
        terminal.send_after(b'CHILD-NAV', b'\x1b')
        terminal.send_after(b'ROOT-NAV', b'\r')
        result = terminal.result()
    assert result['value'] == 'done'
    saved = result['saved']
    assert saved['root']['selected_id'] == 's40' and saved['root']['query'] == 'song'
    assert saved['child']['selected_id'] == 'v36' and saved['child']['query'] == 'verse'
    assert saved['root']['scroll'] > 0 and saved['child']['scroll'] > 0
    assert [snapshot['after'][0] for snapshot in result['snapshots']] == ['child', 'root']
    for snapshot in result['snapshots']:
        view, position = snapshot['after']
        assert position == saved[view]
    assert result['final'] == saved['root']


@pytest.mark.parametrize('columns', [18, MIN_TUI_COLUMNS, 42])
def test_resize_bounds_rows_and_shows_width_notice_or_four_detail_lines(tmp_path, columns):
    script = """
view = ViewSpec('root', 'RESIZE', tuple(ViewItem(str(i), 'Long row label 界 ' * 8, details=Inherit) for i in range(12)),
                details=DetailLines(Content('Title 界 ' * 20), Content('Secondary ' * 20), Content('Metadata ' * 20)), context=('Context ' * 15,))
session = ViewSession(view)
run_view_session(flags, ui, session, poster_manager=manager)
finish(dict(selected=session.position.selected_id))
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.until(b'\033[35;100H')
        frame = terminal.resize(38, columns)
        os.write(terminal.master, b'\x03')
        assert terminal.result()['selected'] == '0'
    writes = row_writes(frame)
    assert writes
    for row, text in writes:
        assert 1 <= row <= 38
        assert display_width(text) <= columns - 1, (row, text)
    rows = dict(writes)
    if columns < MIN_TUI_COLUMNS:
        assert rows[37].startswith(f'Resize to {MIN_TUI_COLUMNS}')
        assert rows[38].startswith('Esc: back')
        assert not any(text.startswith('Title') for _, text in writes)
        return
    assert rows[35].startswith('Title')
    assert rows[36].startswith('Secondary')
    assert rows[37].startswith('Metadata')
    assert rows[38].startswith('Context')


def test_plain_async_completes_without_input_and_does_not_number_headings(tmp_path):
    script = """
flags.plain_mode = True
heading = ViewItem('heading', 'Performances', kind='heading', details=Inherit)
one = ViewItem('one', 'Song One', section_id='heading', details=Inherit)
two = ViewItem('two', 'Song Two', section_id='heading', details=Inherit)
initial = ViewSpec('root', 'PLAIN-ASYNC', (ViewItem('loading', 'Loading sections', kind='heading', details=Inherit),),
                   state='loading', message='Waiting for catalog', selection_policy=SelectionPolicy(auto_single=True), details=DetailLines(Blank, Blank, Blank))
session = ViewSession(initial, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
generation = session.generation
def complete():
    time.sleep(0.35)
    ready = replace(initial, items=(heading, one, two), state='ready', message='Catalog ready')
    session.post(ReplaceView('root', generation, ready))
    time.sleep(0.35)
    session.post(ReplaceView('root', generation, replace(ready, items=(heading, one))))
worker = threading.Thread(target=complete)
worker.start()
value = run_view_session(flags, ui, session, poster_manager=manager)
worker.join()
finish(dict(value=value))
"""
    with Terminal(tmp_path, script) as terminal:
        result = terminal.result()
        output = strip_ansi(bytes(terminal.output).decode())
    assert result['value'] == 'one'
    assert 'Waiting for catalog' in output and 'Catalog ready' in output
    assert 'Performances' in output and 'Loading sections' in output
    assert '[1] Song One' in output and '[2] Song Two' in output
    assert not re.search(r'\[\d+\] (?:Performances|Loading sections)', output)
    assert 'Automatically selecting only result' in output
