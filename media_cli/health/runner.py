"""Collect provider diagnostics without saving, paging or changing provider state."""

from datetime import datetime, timezone
import random

from .profiles import PROFILES
from .scope import selected_translations
from .limits import diagnostic_budget


def check_provider(provider_id, *, seed=None, verify=None, translation='sub', progress=None,
                   announce=None, budget_seconds=900):
    from . import catalogue as c
    from .http_media import HTTP_BUDGET

    profile = PROFILES[provider_id]
    level = verify or profile['default']
    if level not in profile['levels']:
        raise ValueError('Verification level is not supported by this provider')
    selected, unsupported = selected_translations(provider_id, translation)
    seed = random.SystemRandom().randrange(2**63) if seed is None else seed
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError('Seed must be a nonnegative 63-bit integer')
    started = datetime.now(timezone.utc).isoformat()
    http = level in ('http', 'mpv')
    playback = level == 'mpv'
    name = profile['name']
    scope_note = {'movies': 'Movie titles sampled; TV season and episode paths are not checked.',
                  'torrent': 'Search only; torrent files, peers and transfer are not checked.',
                  'animethemes': 'Search only; song/video playback is not checked.'}.get(provider_id, '')
    search_only = profile.get('search_only', False)
    if announce:
        scope = ' + '.join(value.capitalize() for value in selected) or 'no supported translation'
        if search_only:
            announce(f'{name}: search only — {len(c.HEALTH_SAMPLES[provider_id])} searches; no media checks or torrent transfer. Scope: catalogue, not anime translation coverage.')
        else:
            announce(f'{name} catalogue check: {len(c.HEALTH_SAMPLES[provider_id])} searches, up to 6 title-details and 6 episode catalogues per translation. Scope: {scope}. Run budget: {budget_seconds:g}s.')
            announce(f'Catalogue requests up to {c.SEARCH_TIMEOUT}s; up to 3 sampled episodes, at most 3 named sources per episode; extraction up to {c.EXTRACTION_TIMEOUT}s per source.' + (f' HTTP sampling up to {HTTP_BUDGET}s per source.' if http else ' Media access and playback are not checked.'))
        if playback:
            announce('mpv: one quality per sampled source, up to 3 seconds of media and 40 seconds wall time; no window or sound.')
    parts = []
    with diagnostic_budget(budget_seconds):
        for channel in selected:
            def update(stage, index, total, title):
                if progress:
                    progress(f'{channel.capitalize()} · {stage}', index, total, title)
            if profile['combined']:
                deeper = dict(resolution_query=c.resolve_mpv_isolated if playback else c.resolve_http_isolated if http else c.resolve_isolated,
                              http_checks=http, playback_checks=playback,
                              resolution_progress=lambda *a: update('resolving + HTTP + decoding' if playback else 'resolving + HTTP' if http else 'resolving sources and mirrors', *a))
            else:
                deeper = dict(source_query=c.sources_isolated, source_progress=lambda *a: update('source discovery', *a),
                              extraction_query=c.mpv_isolated if playback else c.http_isolated if http else c.extract_isolated,
                              extraction_progress=lambda *a: update('extracting + HTTP + decoding' if playback else 'extracting + HTTP' if http else 'extracting', *a),
                              http_checks=http, playback_checks=playback)
            if search_only:
                deeper = {}
            data = c.check_catalogue(provider_id, c.HEALTH_SAMPLES[provider_id], seed=seed, translation=channel,
                episode_query=None if search_only else c.episodes_isolated,
                episode_progress=lambda *a: update('episode catalogues', *a),
                details_query=None if search_only else c.details_isolated,
                details_progress=lambda *a: update('title details', *a),
                progress=lambda *a: update('searching', *a), **deeper)
            parts.append((channel, data))
            if data['status'] == 'cancelled':
                break
    result = {'started_at': started, 'status': 'complete', 'verification': level,
              'translation': translation, 'seed': seed, 'providers': []}
    if parts and len(parts) == 1 and not unsupported and len(selected) == 1:
        # Retain the exact single-run verdict and evidence.
        result.update(parts[0][1])
        result['translation'] = translation
        for provider in result['providers']:
            provider.update(verification=level, translation=translation, run_status=result['status'])
            if scope_note:
                provider['scope_note'] = scope_note
        return result
    provider = {'id': provider_id, 'name': name, 'status': 'not_checked', 'stages': [],
                'verification': level, 'translation': translation, 'run_status': 'complete'}
    if scope_note:
        provider['scope_note'] = scope_note
    result['providers'].append(provider)
    verdicts = []
    for channel, data in parts:
        if data['status'] == 'cancelled':
            result['status'] = 'cancelled'
        elif data['status'] == 'incomplete' and result['status'] == 'complete':
            result['status'] = 'incomplete'
        for item in data['providers']:
            provider['stages'].extend(item['stages'])
            verdicts.append(item['status'])
            if item.get('local_issues'):
                provider.setdefault('local_issues', []).extend(item['local_issues'])
            provider['stages'].append({'name': 'translation_summary', 'translation': channel,
                'status': 'passed' if item['status'] == 'working' else 'inconclusive',
                'reason': f"{channel.capitalize()}: {item['status'].replace('_', ' ')}; {level} checks, recorded samples only"})
    for channel in unsupported:
        provider['stages'].append({'name': 'translation_support', 'translation': channel,
            'status': 'not_applicable', 'reason': f'{channel.capitalize()} is not listed as a supported translation in the provider registry; no requests made'})
    attempted = {channel for channel, _ in parts}
    for channel in set(selected) - attempted:
        provider['stages'].append({'name': 'translation_summary', 'translation': channel,
            'status': 'not_verified', 'reason': 'Not started because the run was cancelled'})
    if verdicts:
        if all(value == 'working' for value in verdicts) and result['status'] == 'complete':
            provider['status'] = 'working'
        elif any(value in ('working', 'partly_working') for value in verdicts):
            provider['status'] = 'partly_working'
        else:
            provider['status'] = 'inconclusive'
    provider['run_status'] = result['status']
    return result
