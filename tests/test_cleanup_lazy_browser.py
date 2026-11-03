"""Unused browser cleanup stays lightweight and rejects late service creation."""

import os
import subprocess
import sys
from pathlib import Path


def test_cleanup_does_not_import_unused_browser_stack_and_blocks_late_start(tmp_path):
    code = '''
import importlib.abc,sys
from pathlib import Path
from types import SimpleNamespace
from media_cli.cli import application
application.app.flush_anilist_writes=lambda:None
application.app._ipc_player=SimpleNamespace(quit=lambda:None)
application.app.kill_active_subprocesses=lambda:None
blocked=[]
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='media_cli.services.browser_access.integration':
            blocked.append(fullname)
            raise AssertionError('Unused browser integration imported during cleanup')
guard=Guard()
sys.meta_path.insert(0,guard)
application._cleanup()
assert not blocked,blocked
assert 'media_cli.services.browser_access.integration' not in sys.modules
sys.meta_path.remove(guard)
from media_cli.services.browser_access import integration
from media_cli.services.browser_access.profiles import BrowserAccessCancelled
try:
    integration._service(Path.home()/'browser',False,create=True)
except BrowserAccessCancelled:
    pass
else:
    raise AssertionError('Late import allowed browser access after shutdown')
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'HOME': str(tmp_path)}, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
