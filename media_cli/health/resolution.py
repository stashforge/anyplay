"""Evidence for adapters that discover and extract sources in one operation."""

import random
import time

from .failures import record_local_requirement
from .scope import call_for_translation


def check_resolution(provider_id, selected, *, seed, report, provider, query, progress=None, http_checks=False, playback_checks=False, translation="sub"):
    stages = provider['stages']
    rng = random.Random(seed)
    pending = []
    for sample in selected:
        row = {key: sample[key] for key in ('title', 'mal_id', 'year', 'provider_title_id', 'anilist_id') if key in sample}
        row['release_year'] = row.pop('year')
        from .catalogue import age_group
        from datetime import datetime
        row.update(name='source_resolution', status='not_verified', translation=translation,
                   age_group=age_group(sample['year'], datetime.fromisoformat(report['started_at']).year),
                   episode=rng.choice(sample['episode_ids']), reason='Selected; not attempted')
        stages.append(row)
        pending.append(row)
    for index, stage in enumerate(pending, 1):
        if progress:
            progress(index, len(pending), stage['title'])
        if not stage.get('provider_title_id'):
            stage['reason'] = 'No verified provider title ID; combined resolution not attempted'
            continue
        reference = {'title_id': stage['provider_title_id'], 'mal_id': stage.get('mal_id'), 'anilist_id': stage.get('anilist_id'), 'sampling_seed': seed}
        before = time.monotonic()
        result = call_for_translation(query, provider_id, reference, stage['episode'], translation=translation)
        stage['elapsed_seconds'] = result.get('extraction_elapsed_seconds', time.monotonic()-before)
        if record_local_requirement(report, provider, stage, result):
            break
        mirrors = result.get('mirrors')
        valid = isinstance(mirrors, list) and bool(mirrors) and all(isinstance(m, dict) for m in mirrors)
        passed = result.get('status') == 'ok' and valid
        channels = sorted({m['channel'] for m in mirrors if isinstance(m, dict) and isinstance(m.get('channel'), str) and m['channel']}) if valid else []
        stage.update(status='passed' if passed else 'inconclusive', mirror_count=len(mirrors) if valid else 0,
                     mirrors=mirrors if valid else [],
                     **({"channel_names": channels, "channel_count": len(channels)} if channels else {}),
                     reason=('Discovery and extraction happen together. Source labels and any channel labels come from returned mirrors; attempted and failed source/channel counts are not exposed.' if passed else
                             'No mirrors returned; discovery versus extraction failure cannot be distinguished. ' + (result.get('reason') or '')))

        if type(result.get('source_count')) is int and result['source_count'] >= 0:
            stage['source_count'] = result['source_count']
            stage['source_names'] = result.get('source_names') or []
            stage['reason'] = f"{stage['source_count']} source descriptors returned; existing shared resolver used. " + stage['reason']
        if isinstance(result.get('source'), str):
            stage['source'] = result['source']
            stage['reason'] = 'One returned source sampled; other descriptors not tested. ' + stage['reason']
        unresolved = result.get('unresolved_count', 0)
        if type(unresolved) is int and unresolved >= 0:
            stage['unresolved_count'] = unresolved
        from .evidence import record_completed_probes
        common = {key: stage[key] for key in ('title', 'age_group', 'mal_id', 'anilist_id', 'provider_title_id', 'release_year', 'translation', 'episode', 'source') if key in stage}
        if record_completed_probes(report, provider, common, result,
                                   http=http_checks, playback=playback_checks):
            break
