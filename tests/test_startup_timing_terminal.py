"""The real picker marks startup only after its first terminal frame."""

from test_ui_view_terminal import Terminal


def test_ready_initial_frame_is_painted_before_waiting_for_keyboard(tmp_path):
    from test_terminal_stderr_capture import child

    child("""
from types import SimpleNamespace
import sys
import fcntl,struct,termios
from media_cli.ui import picker
from media_cli.ui.terminal_runtime import TerminalRuntime

def select_after_frame(read,write,errors,timeout=0):
    if timeout > 0:
        assert picker._persistent_rendered_lines, 'Ready first frame waited for keyboard input'
        return read,[],[]
    return [],[],[]

picker.select.select=select_after_frame
picker._get_key=lambda fd:'CTRL_C'
flags=SimpleNamespace(plain_mode=False,show_image=False)
ui=SimpleNamespace(active_picker_query='',hovered_show_id=None,hovered_show_obj=None)
sys.stdin.isatty=lambda:True
fcntl.ioctl(1,termios.TIOCSWINSZ,struct.pack('HHHH',24,100,0,0))
with TerminalRuntime(fd=1):
    assert picker.tui_pick(flags,ui,'Search Anime',['Ready'],return_on_quit=True)==-5
""",tmp_path)


def test_first_picker_frame_records_entry_timing(tmp_path):
    script = """
import logging
from media_cli.core.startup import begin_startup, startup_checkpoint
messages = []
class Capture(logging.Handler):
    def emit(self, record):
        messages.append(record.getMessage())
logger = logging.getLogger('media_cli.core.startup')
logger.addHandler(Capture())
logger.setLevel(logging.DEBUG)
logger.propagate = False
begin_startup()
startup_checkpoint('runtime_setup')
result = picker_module.tui_pick(flags, ui, 'STARTUP-TIMING', ['First', 'Second'],
    header='Primary\\nSecondary\\nMetadata\\nFooter', return_on_quit=True)
finish(dict(result=result, messages=messages))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\x1b[12;100H", b"\x1b")
        result = terminal.result()
        assert result["result"] == -2
        assert len(result["messages"]) == 1
        assert "runtime_setup=" in result["messages"][0]
        assert "first_frame=" in result["messages"][0]
