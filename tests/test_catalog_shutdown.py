import subprocess
import sys
from pathlib import Path


def test_cancelled_catalog_does_not_keep_interpreter_alive():
    script = """
import threading
from media_cli.services import catalog
from media_cli.ui.transition import run_preparation, PreparationCancelled
started = threading.Event()
def fetch(*args, **kwargs):
    started.set()
    threading.Event().wait(60)
catalog._perform_catalog_fetch = fetch
try:
    run_preparation(lambda: catalog.ensure_episode_ids({'_id': 'one', '_provider': 'allanime'}, 'sub'),
        tick=lambda: None, cancelled=started.is_set)
except PreparationCancelled:
    print('CANCELLED', flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode == 0, result.stderr
    assert "CANCELLED" in result.stdout
