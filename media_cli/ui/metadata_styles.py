"""Semantic metadata colors shared by library cards and framed title headers."""
from .styled_text import StyledText, TextRun

_STATUS_ROLES = {
    "AIRING": "library.airing", "RELEASING": "library.airing", "ONGOING": "library.airing",
    "FINISHED": "library.finished", "COMPLETED": "library.finished", "ENDED": "library.finished",
    "NOT YET AIRED": "library.pending", "NOT_YET_AIRED": "library.pending", "HIATUS": "library.pending",
    "CANCELLED": "library.cancelled", "CANCELED": "library.cancelled",
}
_ROLES = {"details": "library.value", "muted": "library.progress",
          "metadata.separator": "library.separator", "metadata.airing": "library.airing"}


def metadata_content(parts):
    runs = []
    for text, role in parts:
        if role == "metadata.status":
            role = _STATUS_ROLES.get(text.upper(), "secondary")
        else:
            role = _ROLES.get(role, role)
        runs.append(TextRun(text, role))
    return _grouped_metadata(runs)


def legacy_metadata(text):
    """Project legacy plain metadata onto the same semantic roles as typed views."""
    runs = []
    for index, part in enumerate(text.split(' • ')):
        if index:
            runs.append(TextRun(' • ', 'library.separator'))
        if part.upper() in _STATUS_ROLES:
            runs.append(TextRun(part, _STATUS_ROLES[part.upper()]))
        elif part.startswith(('Watched ', 'EP ')):
            label, value = part.split(' ', 1)
            runs.extend((TextRun(label + ' ', 'library.progress'), TextRun(value, 'library.value')))
        else:
            runs.append(TextRun(part, 'library.value'))
    return _grouped_metadata(runs)


def _grouped_metadata(runs):
    """Quiet labels and spacing within groups; bullets between groups."""
    import re
    groups = [[]]
    for run in runs:
        if run.role == 'library.separator' and '•' in run.text:
            groups.append([])
        else:
            text = run.text
            if text.strip() == 'Watched':
                groups[-1].append(TextRun(text, 'library.label'))
            elif text.startswith(('Watched ', 'Avail ')):
                label, value = text.split(' ', 1)
                groups[-1].extend((TextRun(label + ' ', 'library.label'), TextRun(value, 'library.value')))
            else:
                groups[-1].append(run)

    def category(group):
        value = ''.join(run.text for run in group).strip()
        if value.startswith(('Watched', 'Avail', 'EP ', 'AL EP ', 'Local EP ')):
            return 'progress'
        if re.match(r'^(?:19|20)\d{2}\b', value) or value.startswith('★'):
            return 'facts'
        if any(run.role in _STATUS_ROLES.values() or run.role.startswith('anilist.') for run in group):
            return 'identity'
        if value in {'TV', 'TV_SHORT', 'MOVIE', 'ONA', 'OVA', 'SPECIAL', 'MUSIC', 'ANIME', 'SUB', 'DUB', 'RAW'}:
            return 'identity'
        return value

    result, previous = [], None
    for group in groups:
        if not group:
            continue
        current = category(group)
        if result:
            result.append(TextRun('  ' if current == previous else ' • ', 'library.separator'))
        result.extend(group)
        previous = current
    return StyledText(tuple(result))
