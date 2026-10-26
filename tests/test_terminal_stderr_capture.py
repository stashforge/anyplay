"""Operating-system stderr containment against a real PTY, not sys.stderr mocks."""

import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def child(code, tmp_path):
    master, slave = pty.openpty()
    process = None
    try:
        process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=slave, stderr=slave, env={**os.environ, "HOME": str(tmp_path)})
        os.close(slave)
        slave = -1
        output = bytearray()
        deadline = time.monotonic() + 10
        while process.poll() is None:
            if time.monotonic() > deadline:
                raise AssertionError("PTY child exceeded its deadline")
            if select.select([master], [], [], .05)[0]:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
        process.wait(timeout=5)
        while select.select([master], [], [], 0)[0]:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            output.extend(data)
        assert process.returncode == 0, output.decode(errors="replace")
        return output.decode(errors="replace")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if slave >= 0:
            os.close(slave)
        os.close(master)


def test_native_python_and_child_stderr_are_logged_without_touching_frame(tmp_path):
    output = child(f"""
import os, subprocess, sys
from pathlib import Path
from media_cli.ui.stderr_capture import StderrCapture
path=Path({str(tmp_path / 'native.log')!r})
with StderrCapture(path):
    os.write(1,b'FRAME-START\\n')
    print('PYTHON-WARNING',file=sys.stderr,flush=True)
    os.write(2,b'NATIVE-LINKER-WARNING\\n')
    subprocess.run([sys.executable,'-c',"import os;os.write(2,b'CHILD-WARNING\\\\n')"],check=True)
    os.write(1,b'FRAME-END\\n')
os.write(2,b'OUTSIDE-ERROR\\n')
assert all(text in path.read_text() for text in ('PYTHON-WARNING','NATIVE-LINKER-WARNING','CHILD-WARNING'))
""", tmp_path)
    assert "FRAME-START" in output and "FRAME-END" in output and "OUTSIDE-ERROR" in output
    assert not any(word in output for word in ("PYTHON-WARNING", "NATIVE-LINKER-WARNING", "CHILD-WARNING"))


@pytest.mark.parametrize("failure", ["ValueError", "KeyboardInterrupt"])
def test_capture_restores_descriptor_on_failure(tmp_path, failure):
    output = child(f"""
import os
from pathlib import Path
from media_cli.ui.stderr_capture import StderrCapture
try:
    with StderrCapture(Path({str(tmp_path / 'native.log')!r})):
        os.write(2,b'INSIDE\\n')
        raise {failure}()
except {failure}:
    pass
os.write(2,b'RESTORED\\n')
""", tmp_path)
    assert "RESTORED" in output and "INSIDE" not in output


def test_inherited_child_stderr_does_not_delay_capture_shutdown(tmp_path):
    child(f"""
import subprocess,sys,time
from pathlib import Path
from media_cli.ui.stderr_capture import StderrCapture
started=time.monotonic()
with StderrCapture(Path({str(tmp_path / 'native.log')!r})):
    process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'],stdout=subprocess.DEVNULL)
assert time.monotonic()-started<2
process.terminate()
process.wait(timeout=2)
""", tmp_path)


def test_capture_rotates_private_log_without_logging_recursion(tmp_path):
    child(f"""
import logging,os
from pathlib import Path
from media_cli.ui.stderr_capture import StderrCapture
path=Path({str(tmp_path / 'native.log')!r})
logging.basicConfig()
with StderrCapture(path,max_bytes=4096):
    logging.warning('CAPTURED-LOGGER')
    os.write(2,b'x'*30000)
assert path.stat().st_size<=4096
assert path.stat().st_mode & 0o777==0o600
assert Path(str(path)+'.1').exists()
""", tmp_path)


@pytest.mark.parametrize("stage", ["pipe", "dup", "thread"])
def test_startup_failure_restores_stderr_and_closes_resources(tmp_path, stage):
    child(f"""
import os,threading
from pathlib import Path
from media_cli.ui import stderr_capture as module
before=len(os.listdir('/proc/self/fd'))
def fail(*a,**kw):
    raise OSError('synthetic startup failure')
original_pipe,original_dup,original_start=os.pipe,os.dup,threading.Thread.start
if {stage!r}=='pipe':os.pipe=fail
if {stage!r}=='dup':os.dup=fail
if {stage!r}=='thread':threading.Thread.start=fail
try:
    try:
        with module.StderrCapture(Path({str(tmp_path / 'native.log')!r})):
            raise AssertionError('Failed capture started')
    except OSError:
        pass
finally:
    os.pipe,os.dup,threading.Thread.start=original_pipe,original_dup,original_start
assert len(os.listdir('/proc/self/fd'))==before
assert os.isatty(2)
""", tmp_path)


def test_shared_capture_survives_nested_terminal_handoffs_and_restores_before_error(tmp_path):
    output = child(f"""
import os
from pathlib import Path
from media_cli.state import paths
from media_cli.ui.terminal_runtime import TerminalRuntime,borrow_terminal
from media_cli.ui.stderr_capture import begin_stderr_capture,end_stderr_capture
paths.LOG_DIR={str(tmp_path)!r}
with TerminalRuntime(fd=1):
    os.write(2,b'FIRST-SCREEN\\n')
    with borrow_terminal():
        begin_stderr_capture()
        os.write(2,b'SECOND-SCREEN\\n')
    os.write(2,b'LOADING-SCREEN\\n')
    end_stderr_capture()
    os.write(2,b'RESTORED-ERROR\\n')
text=Path(paths.LOG_DIR,'native.log').read_text()
assert all(value in text for value in ('FIRST-SCREEN','SECOND-SCREEN','LOADING-SCREEN'))
""", tmp_path)
    assert "RESTORED-ERROR" in output
    assert "FIRST-SCREEN" not in output and "SECOND-SCREEN" not in output and "LOADING-SCREEN" not in output


def test_capture_can_be_reused_after_close(tmp_path):
    output = child(f"""
import os
from pathlib import Path
from media_cli.ui.stderr_capture import StderrCapture
path=Path({str(tmp_path / 'native.log')!r})
capture=StderrCapture(path)
with capture:
    os.write(2,b'FIRST-PASS\\n')
with capture:
    os.write(2,b'SECOND-PASS\\n')
assert 'SECOND-PASS' in path.read_text()
""", tmp_path)
    assert 'SECOND-PASS' not in output


def test_primary_terminal_restoration_ends_capture(tmp_path):
    output = child(f"""
import os
from pathlib import Path
from media_cli.state import paths
from media_cli.ui import display
from media_cli.ui.terminal_runtime import TerminalRuntime
paths.LOG_DIR={str(tmp_path)!r}
with TerminalRuntime(fd=1):
    display.enter_alt_screen()
    os.write(2,b'CAPTURED-BEFORE-RESTORE\\n')
    display.restore_terminal()
    os.write(2,b'VISIBLE-AFTER-RESTORE\\n')
assert 'CAPTURED-BEFORE-RESTORE' in Path(paths.LOG_DIR,'native.log').read_text()
""", tmp_path)
    assert 'VISIBLE-AFTER-RESTORE' in output
    assert 'CAPTURED-BEFORE-RESTORE' not in output


def test_caller_redirected_stderr_is_preserved(tmp_path):
    child(f"""
import os
from pathlib import Path
from media_cli.ui import stderr_capture as module
path=Path({str(tmp_path / 'caller.log')!r})
with path.open('wb') as target:
    os.dup2(target.fileno(),2)
    module.begin_stderr_capture()
    assert module._active is None
    os.write(2,b'CALLER-OWNED\\n')
assert 'CALLER-OWNED' in path.read_text()
""", tmp_path)


def test_log_write_failure_keeps_draining_without_terminal_fallback(tmp_path):
    output = child(f"""
import os
from pathlib import Path
from media_cli.ui import stderr_capture as module
def fail(*args):
    raise OSError('synthetic disk failure')
module._PrivateLog.write=fail
with module.StderrCapture(Path({str(tmp_path / 'native.log')!r})) as capture:
    os.write(2,b'NEVER-FALL-BACK-TO-FRAME'*5000)
assert capture.write_failed
os.write(2,b'RESTORED-AFTER-DISK-FAILURE\\n')
""", tmp_path)
    assert 'NEVER-FALL-BACK-TO-FRAME' not in output
    assert 'RESTORED-AFTER-DISK-FAILURE' in output


def test_high_descriptors_still_capture_native_stderr(tmp_path):
    output = child(f"""
import os
from pathlib import Path
from media_cli.ui.stderr_capture import StderrCapture
fds=[os.open('/dev/null',os.O_RDONLY) for _ in range(1100)]
path=Path({str(tmp_path / 'native.log')!r})
with StderrCapture(path):
    os.write(2,b'HIGH-FD-WARNING\\n')
assert 'HIGH-FD-WARNING' in path.read_text()
for fd in fds:os.close(fd)
""", tmp_path)
    assert 'HIGH-FD-WARNING' not in output


def test_close_does_not_flush_a_potentially_blocked_python_stream(tmp_path):
    child(f"""
import sys
from pathlib import Path
from media_cli.ui.stderr_capture import StderrCapture
capture=StderrCapture(Path({str(tmp_path / 'native.log')!r}))
capture.__enter__()
original=sys.stderr
class BlockedStream:
    def flush(self):raise AssertionError('Shutdown must not acquire a Python writer lock')
sys.stderr=BlockedStream()
try:
    capture.close()
finally:sys.stderr=original
""", tmp_path)


def test_standalone_screen_entry_retains_capture_until_restore(tmp_path):
    output = child(f"""
import os,sys
from pathlib import Path
from media_cli.state import paths
from media_cli.ui import display,stderr_capture
paths.LOG_DIR={str(tmp_path)!r}
sys.stdin.isatty=lambda:True
display.enter_alt_screen()
assert display.is_alt_screen_active()
assert stderr_capture._active is not None
os.write(2,b'STANDALONE-SCREEN-WARNING\\n')
display.restore_terminal()
assert 'STANDALONE-SCREEN-WARNING' in Path(paths.LOG_DIR,'native.log').read_text()
""", tmp_path)
    assert 'STANDALONE-SCREEN-WARNING' not in output


def test_new_capture_cannot_race_a_retiring_log_writer(tmp_path):
    child(f"""
import os,threading
from pathlib import Path
from media_cli.ui import stderr_capture as module
entered,release=threading.Event(),threading.Event()
original=module._PrivateLog.write
def delayed(self,text):
    entered.set()
    release.wait(5)
    original(self,text)
module._PrivateLog.write=delayed
path=Path({str(tmp_path / 'native.log')!r})
capture=module.StderrCapture(path)
capture.__enter__()
os.write(2,b'DELAYED-WARNING\\n')
assert entered.wait(2)
capture.close()
try:
    other=module.StderrCapture(path)
    try:
        other.__enter__()
    except RuntimeError:
        pass
    else:
        other.close()
        raise AssertionError('Concurrent log rotation allowed')
finally:
    release.set()
    capture._thread.join(2)
""", tmp_path)
