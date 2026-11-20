"""Expanded temporary editor view and minimal, order-preserving config edits."""
import os
import re
import tempfile

_ASSIGNMENT = re.compile(r'^\s*([^#;\s][^=:]*?)\s*[=:]')


def assignments(text):
    return {match[1].strip(): line for line in text.splitlines()
            if (match := _ASSIGNMENT.match(line))}


def expanded_view(original):
    from .config import DEFAULT_CONFIG, CONFIG_CHOICES, CONFIG_MINIMUMS, serialize_conf
    active = assignments(original)
    result = ['# Expanded editor: uncomment settings to enable them.',
              '# Existing keys update in place; new keys append to your real config.',
              '# Comment/delete an active setting to remove its override.', '']
    represented = set()
    for line in serialize_conf(DEFAULT_CONFIG).splitlines():
        match = _ASSIGNMENT.match(line)
        if not match:
            result.append(line)
            continue
        key = match[1].strip()
        represented.add(key)
        if key in CONFIG_CHOICES:
            result.append('# Choices: ' + ', '.join(CONFIG_CHOICES[key]))
        if key in CONFIG_MINIMUMS:
            result.append(f'# Minimum: {CONFIG_MINIMUMS[key]}')
        result.append(active.get(key, '# ' + line))
    extras = [line for key, line in active.items() if key not in represented]
    if extras:
        result.extend(['', '# Custom settings', *extras])
    return '\n'.join(result) + '\n'


def merge_edits(original, edited):
    from .config import parse_conf_text
    before, after = parse_conf_text(original), parse_conf_text(edited)
    changed = {key for key in before.keys() | after.keys()
               if key not in before or key not in after or before[key] != after[key]}
    if not changed:
        return original
    edited_lines = assignments(edited)
    result = []
    written = set()
    for line in original.splitlines(keepends=True):
        match = _ASSIGNMENT.match(line)
        key = match[1].strip() if match else None
        if key not in changed:
            result.append(line)
        elif key in after and key not in written:
            result.append(edited_lines[key] + '\n')
            written.add(key)
    new = [line for key, line in edited_lines.items() if key in changed and key not in before]
    if new:
        if result and not result[-1].endswith('\n'):
            result[-1] += '\n'
        result.extend(line + '\n' for line in new)
    return ''.join(result)


def write_text(path, text):
    """Atomic private write, preserving the old file on failure."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.config.', suffix='.conf', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
