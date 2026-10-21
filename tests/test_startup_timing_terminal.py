"""The real picker marks startup only after its first terminal frame."""

from test_ui_view_terminal import Terminal


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
