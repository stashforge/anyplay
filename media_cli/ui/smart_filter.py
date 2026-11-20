"""Small shared filter grammar; callers supply their own typed field schema/data."""
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import operator
import re
import shlex
from .fuzzy import fuzzy_match


@dataclass(frozen=True)
class FilterField:
    name: str
    kind: str = 'text'
    aliases: tuple[str, ...] = ()


_COMPARE = {'=': operator.eq, '>': operator.gt, '>=': operator.ge,
            '<': operator.lt, '<=': operator.le}


def number(value, kind):
    text = str(value).strip().casefold()
    if kind == 'resolution':
        text = {'4k': '2160', '8k': '4320'}.get(text, text.removesuffix('p'))
    if kind == 'size':
        match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([kmgt]?i?b)?', text)
        if not match:
            raise ValueError('Use a size such as 800MB or 8GB')
        amount, unit = match.groups()
        unit = unit or 'b'
        power = {'b': 0, 'k': 1, 'm': 2, 'g': 3, 't': 4}[unit[0]]
        result = float(amount) * (1024 if 'i' in unit else 1000) ** power
        if not math.isfinite(result):
            raise ValueError('Size is too large')
        return result
    if not re.fullmatch(r'\d+(?:\.\d+)?', text):
        raise ValueError('Use a nonnegative number')
    result = float(text)
    if not math.isfinite(result):
        raise ValueError('Number is too large')
    return result


@dataclass(frozen=True)
class FilterPlan:
    text: str = ''
    clauses: tuple = ()
    error: str = ''
    highlight: str = ''

    def matches(self, text, values):
        if self.error or (self.text and fuzzy_match(self.text, text) is None):
            return False
        for field, op, expected in self.clauses:
            actual = values.get(field.name)
            if actual is None or actual == '' or actual == ():
                return False
            choices = actual if isinstance(actual, (tuple, list)) else (actual,)
            def matches_one(value):
                if field.kind == 'text':
                    return any(fuzzy_match(term, str(value)) is not None for term in expected)
                if field.kind == 'bool':
                    return isinstance(value, bool) and value == expected
                try:
                    candidate = number(value, field.kind)
                except (ValueError, TypeError):
                    return False
                return _COMPARE[op](candidate, expected)
            if not any(matches_one(value) for value in choices):
                return False
        return True


@lru_cache(maxsize=128)
def compile_filter(query, schema):
    fields = {alias.casefold(): field for field in schema
              for alias in (field.name, *field.aliases)}
    try:
        tokens = shlex.split(query)
    except ValueError:
        return FilterPlan(error='Close the quoted filter value')
    explicit = any(':' in token for token in tokens)
    plain, clauses, highlights = [], [], []
    for index, token in enumerate(tokens):
        editing_last = index == len(tokens) - 1 and not query.endswith(tuple(" \t\n"))
        if explicit and token.casefold() == 'and':
            continue
        if ':' not in token:
            plain.append(token)
            highlights.append(token)
            continue
        name, value = token.split(':', 1)
        field = fields.get(name.casefold())
        if field is None:
            return FilterPlan(error=f"Unknown filter '{name}'; Esc, then ? for fields")
        if not value or (field.kind not in ('text', 'bool') and value in _COMPARE):
            if editing_last:
                continue
            return FilterPlan(error=f'Enter a value for {field.name}')
        op = '='
        if field.kind not in ('text', 'bool'):
            match = re.fullmatch(r'(>=|<=|>|<|=)?(.+)', value)
            if not match:
                return FilterPlan(error=f'Enter a value for {field.name}')
            op, value = match.groups()
            op = op or '='
            if field.kind == 'size' and re.fullmatch(r'\d+(?:\.\d*)?(?:[kmgt]i?)?', value, re.IGNORECASE):
                if editing_last:
                    continue
                return FilterPlan(error='size: Add a unit such as MB or GB')
            try:
                expected = number(value, field.kind)
            except ValueError as error:
                return FilterPlan(error=f'{field.name}: {error}')
        elif field.kind == 'bool':
            lowered = value.casefold()
            if lowered not in ('true', 'false', 'yes', 'no', '1', '0'):
                return FilterPlan(error=f'{field.name}: use true or false')
            expected = lowered in ('true', 'yes', '1')
        else:
            if value.startswith(('>', '<', '=')):
                return FilterPlan(error=f'{field.name}: comparisons require a numeric field')
            expected = tuple(part.strip() for part in value.split(',') if part.strip())
            if not expected:
                if editing_last:
                    continue
                return FilterPlan(error=f'Enter a value for {field.name}')
            if field.name == 'title':
                highlights.extend(expected)
        clauses.append((field, op, expected))
    return FilterPlan(' '.join(plain), tuple(clauses), highlight=' '.join(highlights))


TORRENT_FIELDS = (
    FilterField('title'), FilterField('size', 'size'),
    FilterField('seeds', 'number', ('seeders',)),
    FilterField('leechers', 'number'), FilterField('res', 'resolution', ('resolution',)),
    FilterField('source'), FilterField('codec'), FilterField('audio'), FilterField('subs'),
    FilterField('trusted', 'bool'),
)


def filter_placeholder(schema):
    return '  '.join(field.name + ':' for field in schema)


def filter_hint(query, schema, options=None):
    """Short examples for the field currently being edited."""
    match = _active_value(query)
    if not match:
        candidates = field_completions(query, schema)
        return '  '.join(candidates) if len(candidates) > 1 else ''
    field = next((field for field in schema
                  if match[1].casefold() in (field.name, *field.aliases)), None)
    if field is None:
        return ''
    name = match[1]
    if field.kind == 'size':
        values = ('800MB', '<800MB', '>1GB')
    elif field.kind == 'resolution':
        values = ('1080p', '>=720p')
    elif field.kind == 'number':
        values = ('2020', '>=2008', '<2025') if field.name == 'year' else ('0', '>0', '>=10') if field.name in ('watched', 'downloaded') else ('20', '>20', '<100')
    elif field.kind == 'bool':
        values = ('true', 'false')
    else:
        typed = query[match.end(1) + 1:]
        if field.name == 'title':
            return 'title:"search text"' if not typed else ''
        prefix, available = value_completions(query, schema, options)
        if prefix and len(available) == 1:
            return ''  # The unique value is already visible as inline completion.
        return name + ':' + ','.join(available) if available else ''
    return '  '.join(name + ':' + value for value in values)


def field_completions(query, schema):
    match = re.search(r'(?:^|\s)([a-zA-Z_]+)$', query)
    if not match:
        return ()
    prefix = match[1].casefold()
    return tuple(name + ':' for field in schema for name in (field.name, *field.aliases)
                 if name.startswith(prefix))


def value_completions(query, schema, options=None):
    match = _active_value(query)
    if not match:
        return '', ()
    field = next((field for field in schema if field.kind == 'text' and field.name != 'title'
                  and match[1].casefold() in (field.name, *field.aliases)), None)
    if field is None:
        return '', ()
    parts = match[2].split(',')
    prefix = parts[-1].strip('"')
    selected = {part.strip('"').casefold() for part in parts[:-1]}
    values = tuple(value for value in (options or {}).get(field.name, ())
                   if value.casefold().startswith(prefix.casefold()) and value.casefold() not in selected)
    return prefix, values


def field_completion(query, schema, options=None):
    prefix, values = value_completions(query, schema, options)
    if prefix and len(values) == 1:
        return values[0][len(prefix):]
    candidates = field_completions(query, schema)
    if len(candidates) != 1:
        return ''
    return candidates[0][len(query.rsplit(None, 1)[-1]):]


def filter_value_options(items, schema):
    """Collect returned categorical values once per view, never during painting."""
    fields = {field.name for field in schema if field.kind == 'text' and field.name != 'title'}
    result = {name: set() for name in fields}
    for item in items:
        for name, raw in item.filter_values:
            if name not in fields or raw is None:
                continue
            for value in raw if isinstance(raw, (tuple, list)) else (raw,):
                if str(value).strip():
                    result[name].add(str(value))
    return {name: tuple(sorted({value.casefold(): (value.casefold() if name == 'provider' else value)
                                for value in sorted(values, reverse=True)}.values(), key=str.casefold))
            for name, values in result.items()}


def _active_value(query):
    return re.search(r'(?:^|\s)([\w]+):((?:[^"\s]|"[^"]*"|"[^"]*$)*)$', query)


def accept_field_completion(query, schema, options=None):
    prefix, values = value_completions(query, schema, options)
    if prefix and len(values) == 1:
        match = _active_value(query)
        start = match.start(2) + match[2].rfind(',') + 1
        value = values[0]
        return query[:start] + (json.dumps(value, ensure_ascii=False) if any(c.isspace() for c in value) else value)
    return query + field_completion(query, schema, options)
