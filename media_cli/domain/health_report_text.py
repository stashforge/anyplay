"""Plain-text presentation of recorded health evidence; never runs checks."""

from collections import Counter
from datetime import datetime

from ..core.terminal import display_width, sanitize_terminal_text, split_display_prefix

_STATUS = {'not_verified': 'Unverified', 'not_applicable': 'Not applicable'}
_NAMES = {'media_access': 'Media access', 'source_resolution': 'Source resolution'}
_REFERENCE = ('mal_id', 'anilist_id', 'provider_title_id', 'release_year', 'age_group')
_LABELS = {'mal_id': 'MAL ID', 'anilist_id': 'AniList ID', 'http_status': 'HTTP status',
           'headers_present': 'Headers supplied', 'type': 'Stream type',
           'source_names': 'Sources', 'channel_names': 'Returned channels'}
_CATALOGUE = {'search', 'title_identity', 'title_details', 'episode_catalogue', 'episode_coverage'}
_SAMPLE_STAGES = {'search', 'title_identity', 'title_details', 'episode_catalogue',
                  'source_discovery', 'source_resolution', 'mirror_extraction'}


def _label(value):
    return _LABELS.get(value, value.replace('_', ' ').capitalize())


def _status(value):
    return _STATUS.get(value, _label(value))


class _Text:
    def __init__(self, width):
        self.width = max(24, min(100, int(width)))
        self.lines = []

    def add(self, value='', indent=0):
        value = sanitize_terminal_text(value)
        if not value:
            self.lines.append('')
            return
        prefix = ' ' * indent
        available = self.width - indent
        while value:
            chunk, rest = split_display_prefix(value, available)
            if rest and ' ' in chunk:
                chunk, tail = chunk.rsplit(' ', 1)
                rest = tail + rest
            self.lines.append(prefix + chunk)
            value = rest.lstrip()

    def heading(self, title):
        self.add()
        self.add(title)
        self.add('-' * min(display_width(title), self.width))

    def field(self, key, value, indent=2):
        if isinstance(value, bool):
            value = 'Yes' if value else 'No'
        elif isinstance(value, list):
            value = ', '.join(str(item) for item in value) or 'None recorded'
        self.add(f'{_label(key)}: {value}', indent)


def _check_name(stage):
    if stage['name'] == 'media_access' and stage.get('track'):
        return _label(stage['track']) + ' access'
    return _NAMES.get(stage['name'], _label(stage['name']))


def _outcome(stage):
    if stage.get('required') is False:
        return 'Not required'
    if stage.get('issue_scope') == 'local':
        return f"Unavailable locally ({_status(stage['status'])})"
    return _status(stage['status'])


def _counts(stages):
    counts = Counter(_outcome(stage) for stage in stages)
    return ' · '.join(f'{count} {status}' for status, count in counts.items()) or 'Unverified — no evidence recorded'


def _is_media(stage):
    return stage['name'] == 'media_access' or stage['name'].startswith(('playback', 'decode'))


def _translation_scope(stages):
    translations = {stage.get('translation', '').lower() for stage in stages
                    if stage['name'] not in ('translation_support', 'translation_summary')}
    known = translations & {'sub', 'dub'}
    return {frozenset({'sub'}): 'Sub only', frozenset({'dub'}): 'Dub only',
            frozenset({'sub', 'dub'}): 'Sub and Dub'}.get(frozenset(known), 'Unspecified / unverified')


def _observations(text, stages):
    text.field('catalogue_confidence', _counts(s for s in stages if s['name'] in _CATALOGUE))
    text.field('observed_media / decoder', _counts(s for s in stages if _is_media(s)))


def _scope(text, stages):
    text.field('translation_scope', _translation_scope(stages))
    text.add('Translation labels describe recorded stage scope, not verified coverage; other translations remain unverified.', 2)
    types = list(dict.fromkeys(mirror['type'] for stage in stages
                              for mirror in stage.get('mirrors', []) if mirror.get('type')))
    text.field('recorded_stream_types', types or 'Unspecified / unverified')


def _sample_scope(text, stages):
    text.heading('SAMPLE SCOPE')
    _scope(text, stages)
    titles = {s['title'] for s in stages if s.get('title')}
    text.add(f'{len(titles)} recorded title labels; counts below are stage samples, not whole-library coverage.')
    groups = {}
    for stage in stages:
        if stage['name'] in _SAMPLE_STAGES and stage.get('title'):
            groups.setdefault(_check_name(stage), []).append(stage)
    for name, rows in groups.items():
        labels = {s['title'] for s in rows if s.get('title')}
        cohorts = Counter(s.get('age_group') or 'unspecified' for s in rows)
        cohort_counts = ', '.join(f'{key}: {count}' for key, count in cohorts.items())
        text.add(f'{name}: {len(rows)} stages; {len(labels)} title labels; cohorts {cohort_counts}', 2)


def _translation_results(text, stages):
    summaries = [stage for stage in stages if stage['name'] == 'translation_summary']
    if not summaries:
        return
    text.heading('TRANSLATION RESULTS')
    for stage in summaries:
        translation = _label(stage.get('translation') or 'unspecified')
        text.add(f"{translation} — {_outcome(stage)}: {stage.get('reason') or 'No reason recorded'}", 2)
        for key, value in stage.items():
            if key not in ('name', 'status', 'translation', 'reason'):
                text.field(key, value, 4)


def _digest(text, titles):
    text.heading('TITLE DIGEST')
    text.add('Recorded stage outcomes only; matching labels do not establish identity. Missing checks remain unverified.')
    if not titles:
        text.add('No title-specific evidence recorded.')
    for title, stages in titles.items():
        text.add()
        text.add(title)
        text.field('translation_scope', _translation_scope(stages))
        _observations(text, stages)
        checks = {}
        for stage in stages:
            if stage['name'] not in _CATALOGUE and stage['name'] != 'catalogue_summary':
                checks.setdefault(_check_name(stage), []).append(stage)
        for name, rows in checks.items():
            text.add(f'{name}: {_counts(rows)}', 2)


def _stage(text, stage, common):
    label = 'Not required' if stage.get('required') is False else _status(stage['status'])
    duration = f" ({stage['elapsed_seconds']:.2f}s)" if 'elapsed_seconds' in stage else ''
    text.add()
    text.add(f'{_check_name(stage)} — {label}{duration}')
    if stage.get('required') is False:
        text.field('recorded_status', _status(stage['status']))
    ignored = {'name', 'status', 'title', 'elapsed_seconds', 'mirrors'} | set(common)
    for key, value in stage.items():
        if key not in ignored and value != '':
            if key == 'mirror_index':
                text.field('mirror_number', value + 1)
            else:
                text.field(key, value)
    for number, mirror in enumerate(stage.get('mirrors', []), 1):
        text.add()
        text.add(f'Mirror {number}', 2)
        for key, value in mirror.items():
            text.field(key, value, 4)


def _title(text, title, stages):
    text.heading(title)
    common = {}
    # Search rows may predate identity resolution. All other title stages must
    # carry a reference before it can safely describe their shared title label.
    relevant = [stage for stage in stages if stage['name'] != 'search'] or stages
    for key in _REFERENCE:
        values = [stage[key] for stage in relevant if key in stage]
        if (len(values) == len(relevant) and values
                and all(stage.get(key, values[0]) == values[0] for stage in stages)):
            common[key] = values[0]
    categories = {}
    for stage in stages:
        name = stage['name']
        if name == 'media_access':
            category = stage.get('track', 'media').upper() + ' ACCESS'
        elif name.startswith('playback') or name.startswith('decode'):
            category = 'PLAYBACK'
        elif name.startswith(('source', 'mirror')):
            category = 'SOURCES AND MIRRORS'
        else:
            category = 'CATALOGUE AND DETAILS'
        categories.setdefault(category, []).append(stage)
    for category, entries in categories.items():
        text.add()
        text.add(category)
        for stage in entries:
            _stage(text, stage, common)
    if common:
        text.add()
        text.add('TITLE REFERENCES')
        for key, value in common.items():
            text.field(key, value)


def render_health_report(data, *, width=80):
    text = _Text(width)
    text.add('PROVIDER HEALTH REPORT')
    try:
        started = datetime.fromisoformat(data['started_at'].replace('Z', '+00:00'))
        started = started.astimezone().strftime('%d %b %Y, %H:%M:%S %Z')
    except (ValueError, TypeError, AttributeError, OverflowError):
        started = data['started_at']
    text.field('started', started)
    text.field('run_state', data['status'])
    text.field('verification_requested', data['verification'])
    text.add()
    text.add('Only recorded samples were checked. HTTP access does not prove playback or subtitle rendering; unlisted checks remain unverified.')
    if not data['providers']:
        text.add()
        text.add('No providers were checked.')
    for provider in data['providers']:
        text.heading(f"{provider['name'] or provider['id']} ({provider['id']})")
        stages = [stage for stage in provider['stages'] if stage['name'] != 'translation_summary']
        verification = provider.get('verification', data['verification'])
        verdict = _status(provider['status'])
        observed = [s for s in stages if s['status'] == 'passed'
                    and s.get('required') is not False and _is_media(s)]
        checked = ('media_access' if verification == 'http' else
                   'playback' if verification == 'mpv' else None)
        if provider['status'] == 'working' and any(s['name'] == checked for s in observed):
            level = {'http': 'HTTP checked', 'mpv': 'decode checked'}[verification]
            verdict += ' — ' + level + ' (sampled titles)'
        text.field('recorded_verdict', verdict)
        for key, label in (('verification', 'effective_verification'),
                           ('run_status', 'provider_run_state'),
                           ('translation', 'translation_requested'), ('scope_note', 'scope_note')):
            if key in provider:
                text.field(label, provider[key])
        _observations(text, stages)
        text.add('These independent summaries count recorded stage outcomes; they do not change the recorded verdict or establish complete title coverage.')
        if provider.get('local_issues'):
            text.field('local_availability', f"{len(provider['local_issues'])} unavailable local requirements; see findings")
        _sample_scope(text, stages)
        _translation_results(text, provider['stages'])
        text.heading('CHECK SUMMARY')
        checks = {}
        for stage in stages:
            counts = checks.setdefault(_check_name(stage), Counter())
            counts[_outcome(stage)] += 1
        if not checks:
            text.add('No stages were checked.')
        label_width = max((display_width(name) for name in checks), default=0)
        for name, counts in checks.items():
            result = ' · '.join(f'{count} {status}' for status, count in counts.items())
            if text.width >= 60 and label_width + 2 + display_width(result) <= text.width:
                name = sanitize_terminal_text(name)
                result = sanitize_terminal_text(result)
                text.lines.append(name + ' ' * (label_width - display_width(name) + 2) + result)
            else:
                text.add(name)
                text.add(result, 2)
        text.heading('FINDINGS')
        issues = provider.get('local_issues', [])
        if issues:
            text.add('Local requirements — not a provider failure:')
            for issue in issues:
                text.add(f"{issue['kind']} / {issue['stage']}: {issue['reason']}", 2)
        findings = {}
        for stage in stages:
            if stage['status'] not in ('failed', 'inconclusive', 'blocked') or stage.get('required') is False:
                continue
            key = (_check_name(stage), stage['status'], stage.get('reason', 'No reason recorded'))
            findings.setdefault(key, []).append(stage.get('title') or 'Provider-wide check')
        for (name, status, reason), titles in findings.items():
            text.add()
            unit = 'check' if len(titles) == 1 else 'checks'
            text.add(f'{name} — {_status(status)} ({len(titles)} {unit})')
            text.field('affected', list(dict.fromkeys(titles)))
            text.field('reason', reason)
        if not findings and not issues:
            text.add('No failed, blocked or inconclusive checks recorded. See the summary for checks left unverified.')
        titles = {}
        global_stages = []
        for stage in stages:
            if stage.get('title'):
                titles.setdefault(stage['title'], []).append(stage)
            else:
                global_stages.append(stage)
        _digest(text, titles)
        text.heading('TITLE DETAILS')
        if not titles:
            text.add('No title-specific evidence recorded.')
        for title, stages in titles.items():
            _title(text, title, stages)
        if global_stages:
            text.heading('PROVIDER-WIDE CHECKS')
            for stage in global_stages:
                _stage(text, stage, {})
    text.heading('RUN INFORMATION')
    for key in ('run_id', 'started_at', 'saved_at', 'status', 'verification', 'seed', 'schema_version'):
        if key in data:
            text.field(key, data[key])
    return '\n'.join(text.lines) + '\n'
