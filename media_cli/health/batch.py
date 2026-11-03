"""Coordinate sequential diagnostics without importing or invoking adapters."""

from copy import deepcopy
from datetime import datetime, timezone
import random

from .profiles import PROFILES


_LEVELS = ('catalogue', 'discovery', 'extraction', 'http', 'mpv')


def _scope(provider_id, requested):
    profile = PROFILES[provider_id]
    stage_status = 'not_verified'
    if requested is None:
        effective = profile['default']
        note = f"Provider default verification: {effective}."
    elif requested in profile['levels']:
        effective = requested
        note = f"Requested verification {requested} is supported."
    else:
        supported = [level for level in _LEVELS[:_LEVELS.index(requested) + 1]
                     if level in profile['levels']]
        effective = supported[-1] if supported else requested
        if not supported:
            stage_status = 'not_applicable'
        note = (f"Requested verification {requested} is not supported; "
                f"using deepest supported level {effective}." if supported else
                f"Requested verification {requested} has no supported level at or below it; "
                "none executed; diagnostic run not started.")
    return {
        'id': provider_id, 'name': profile['name'], 'status': 'not_checked',
        'verification': effective, 'scope_note': note, 'run_status': 'not_started',
        'stages': [{'name': 'diagnostic_run', 'status': stage_status,
                    'reason': note if stage_status == 'not_applicable' else 'Diagnostic run not started'}],
    }


def check_batch(provider_ids, *, run_provider, seed=None, verify=None,
                translation='sub', progress=None):
    """Combine caller-supplied single-provider reports, retaining their verdicts.

    The caller owns translation expansion, provider filtering, time budgets,
    persistence and presentation. Progress receives (index, total, provider_id).
    """
    provider_ids = list(dict.fromkeys(provider_ids))
    if any(pid not in PROFILES for pid in provider_ids):
        raise ValueError('Health diagnostics are not wired for this provider')
    if verify is not None and verify not in _LEVELS:
        raise ValueError('Unknown verification level')
    if seed is not None and (type(seed) is not int or not 0 <= seed < 2**63):
        raise ValueError('Seed must be a nonnegative 63-bit integer')
    if seed is None:
        seed = random.SystemRandom().randrange(2**63)
    providers = [_scope(pid, verify) for pid in provider_ids]
    levels = {provider['verification'] for provider in providers}
    report = {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'status': 'complete',
        'translation': translation,
        'seed': seed,
        'verification': 'mixed' if len(levels) > 1 else next(iter(levels), verify or 'catalogue'),
        'providers': providers,
    }
    for index, pending in enumerate(providers):
        if pending['stages'][0]['status'] == 'not_applicable':
            continue
        running = False
        try:
            if progress is not None:
                progress(index + 1, len(providers), pending['id'])
            running = True
            outcome = run_provider(pending['id'], seed=seed,
                                   verify=pending['verification'], translation=translation)
            rows = outcome['providers']
            if len(rows) != 1 or rows[0]['id'] != pending['id']:
                raise ValueError('Expected a single report for the requested provider')
            provider = deepcopy(rows[0])
            provider['verification'] = pending['verification']
            provider['scope_note'] = ' '.join(filter(None, (provider.get('scope_note'), pending['scope_note'])))
            provider['run_status'] = outcome['status']
            providers[index] = provider
            if outcome['status'] == 'cancelled':
                report['status'] = 'cancelled'
                break
            if outcome['status'] == 'incomplete':
                report['status'] = 'incomplete'
        except KeyboardInterrupt:
            report['status'] = 'cancelled'
            if running:
                pending['run_status'] = 'cancelled'
                pending['stages'][0]['reason'] = 'Diagnostic run interrupted; no report returned'
            break
        except Exception as exc:
            report['status'] = 'incomplete'
            pending['status'] = 'inconclusive'
            pending['run_status'] = 'incomplete'
            pending['stages'] = [{'name': 'diagnostic_run', 'status': 'inconclusive',
                                  'reason': type(exc).__name__}]
    return report
