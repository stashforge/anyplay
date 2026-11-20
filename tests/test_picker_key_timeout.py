import os
import time
import pytest
from media_cli.ui.picker_render import get_key


@pytest.mark.parametrize('raw', [b'\x1b[', b'\x1bO', b'\x1b[1;', b'\x1b['+b'1'*50])
def test_incomplete_sequence_cannot_block_ui(raw):
    reader, writer = os.pipe()
    try:
        os.write(writer, raw)
        started = time.monotonic()
        assert get_key(reader) == 'UNKNOWN'
        assert time.monotonic() - started < .3
    finally:
        os.close(reader)
        os.close(writer)


@pytest.mark.parametrize('raw,key', [(b'\x1b[A','UP'), (b'\x1b[1;2C','SHIFT_RIGHT'),
                                     (b'\x1bOB','DOWN'), (b'\x1br','ALT_R'), (b'\x1b','ESC')])
def test_complete_keys_keep_existing_meaning(raw, key):
    reader, writer = os.pipe()
    try:
        os.write(writer, raw)
        assert get_key(reader) == key
    finally:
        os.close(reader)
        os.close(writer)
