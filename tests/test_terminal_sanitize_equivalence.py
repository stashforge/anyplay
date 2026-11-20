import random
import re

from media_cli.core.terminal import sanitize_terminal_text, _ANSI_RE, _TERMINAL_STRING_RE


def previous(value):
    text = str(value or '')
    text = _TERMINAL_STRING_RE.sub('', text)
    text = _ANSI_RE.sub('', text)
    text = ''.join(' ' if ord(c) < 32 or 127 <= ord(c) <= 159 else c for c in text)
    return re.sub(r'\s+', ' ', text).strip()


def test_optimized_sanitizer_keeps_control_and_unicode_behavior():
    samples = [None, 0, '', '\x1b[31mTitle\x1b[0m', '\x1b]0;bad title\x07Safe',
               '\x1bPpayload\x1b\\Safe', '我推の子\u2003Title\nNext',
               ''.join(chr(i) for i in range(256))]
    rng = random.Random(42)
    alphabet = ''.join(chr(i) for i in range(256)) + '推しの子\u2003\u2028\u3000'
    samples.extend(''.join(rng.choices(alphabet, k=200)) for _ in range(100))
    for text in samples:
        assert sanitize_terminal_text(text) == previous(text)
