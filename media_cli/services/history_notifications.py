"""Scheduled observations and deduplicated desktop notifications.

Caller holds the scheduler lock. Tracker schedules never prove provider availability.
"""

from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time


log = logging.getLogger(__name__)

DESKTOP_ID = 'org.anyplay.CLI'


def notification_desktop_entry():
    """Give desktop shells an application identity and notification-history entry."""
    root = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    path = root / 'applications' / (DESKTOP_ID + '.desktop')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with path.open('x') as output:
                output.write('[Desktop Entry]\nType=Application\nName=Anyplay\n'
                             'Comment=Anyplay media and release notifications\n'
                             'Exec=anyplay\nIcon=multimedia-player\nTerminal=true\nNoDisplay=true\n')
        return DESKTOP_ID
    except OSError as error:
        log.warning('Could not register notification identity: %s', error)
        return None


def notify(title, body):
    executable = shutil.which('notify-send')
    if not executable:
        log.warning("Notification delivery failed: notify-send is not installed")
        return False
    desktop = notification_desktop_entry()
    options = ["--hint=string:desktop-entry:" + desktop] if desktop else []
    try:
        result = subprocess.run(
            [executable, '--app-name=Anyplay', "--urgency=normal", "--expire-time=10000", "--print-id", *options, '--', title, body],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=5, check=False)
        if result.returncode:
            log.warning("Notification delivery failed (exit %s): %s", result.returncode, result.stderr.strip()[:400])
        else:
            log.info("Desktop accepted notification id=%s identity=%s (popup visibility is controlled by desktop)", result.stdout.strip(), desktop or "unregistered")
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as error:
        log.warning("Notification delivery failed: %s", error)
        return False


def load_state(root):
    try:
        data = json.loads((Path(root) / 'history-notifications.json').read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(root, state):
    path = Path(root) / 'history-notifications.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False))
    temporary.chmod(0o600)
    temporary.replace(path)


def identity(show):
    from ..core.anilist import get_show_anilist_id
    aid = get_show_anilist_id(show)
    if aid:
        return f'al:{aid}'
    return f"{show.get('_provider') or show.get('provider')}:{show.get('_id')}"


def observe_release(state, entry, *, send=notify, defer=False, first_only=False, recover=False):
    """Only actual, successful provider catalogue observations count."""
    from ..domain.history import history_available_episode_count
    from ..domain.titles import get_show_display_title
    show = entry.get('show', {})
    key = f"{show.get('_provider') or show.get('provider')}:{show.get('_id')}:{entry.get('translation_type', 'sub')}"
    # checked_at is persisted proof; the runtime loaded flag is intentionally stripped.
    if show.get('_provider_catalog_refresh_error') or not show.get('_provider_catalog_checked_at'):
        log.info("Release observation skipped %s: no successful provider catalogue", key)
        return
    available = history_available_episode_count(entry)
    if available is None:
        log.info("Release observation skipped %s: availability unknown", key)
        return
    seen = state.setdefault('releases', {})
    previous = seen.get(key, seen.get(identity(show)))  # Read legacy baselines once.
    count = float(available)
    try:
        progress = float(entry.get('progress') if entry.get('progress') is not None else entry.get('episode') or 0)
    except (TypeError, ValueError):
        log.warning("Release observation skipped %s: progress is not numeric", key)
        return
    event = show.get('_release_notification') or {}
    confirmed_event = (event.get('translation') == entry.get('translation_type', 'sub')
                       and event.get('available') == count and event.get('previous', count) < count)
    pending = state.setdefault('pending_releases', {})
    delivered = state.setdefault('delivered_releases', {})
    event_key = f'{key}:{count:g}'
    eligible = confirmed_event or (previous is not None and count > previous)
    if recover and entry.get("was_caught_up") and 0 < count - progress <= 3:
        eligible = True
        if event.get('available') != count:
            show['_release_notification'] = {
                'previous': progress, 'available': count, 'progress': progress,
                'translation': entry.get('translation_type', 'sub'),
                'detected_at': int(time.time()),
            }
        log.info("Manual missed-release recovery considered %s: available=%s progress=%s", key, count, progress)
    first_key = f'first:{identity(show)}'
    if first_only:
        eligible = first_key not in delivered and count >= 1 and (previous == 0 or (confirmed_event and event.get("previous") == 0) or entry.get("_notify_first_expected", False))
    duplicate = f'{identity(show)}:{entry.get("translation_type", "sub")}:{count:g}'
    if eligible and event_key not in delivered and duplicate not in delivered:
        pending.setdefault(event_key, {'name': get_show_display_title(show), 'available': available, 'duplicate': duplicate, 'first_key': first_key if first_only else None})
        log.info("Release detected %s: previous=%s available=%s progress=%s", key, previous, count, progress)
    else:
        reason = 'already delivered' if event_key in delivered else 'baseline' if previous is None else 'unchanged or outside first-release rule'
        log.info("Release observation %s: %s (previous=%s available=%s progress=%s)", key, reason, previous, count, progress)
    seen[key] = count
    if not defer:
        deliver_releases(state, send=send)


def deliver_releases(state, *, send=notify):
    pending = state.setdefault('pending_releases', {})
    delivered = state.setdefault('delivered_releases', {})
    if not pending:
        return
    unique = {}
    for key, item in pending.items():
        duplicate = item.get('duplicate', key)
        if duplicate not in delivered:
            unique[duplicate] = item
    if unique:
        title = 'New episode available' if len(unique) == 1 else f'{len(unique)} shows have new episodes'
        body = '\n'.join(f"{item['name']} · Episode {item['available']}" for item in unique.values())
        if not send(title, body):
            log.warning("Release notifications pending retry: %s", ', '.join(pending))
            return
    for key, item in list(pending.items()):
        delivered[key] = time.time()
        delivered[item.get('duplicate', key)] = time.time()
        if item.get('first_key'):
            delivered[item['first_key']] = time.time()
        pending.pop(key)
        log.info("Release notification accepted by desktop %s", key)


def scheduled_alerts(state, now, *, send=notify):
    delivered = state.setdefault('airing_notified', {})
    for key, item in state.get('schedules', {}).items():
        at = item.get('at', 0)
        event = f"{item.get('episode')}:{at}"
        # Do not announce stale schedules after the laptop has slept for days.
        if item.get('first_only') and item.get('episode') != 1:
            continue
        if at <= now < at + 3600 and delivered.get(key) != event:
            if send('Scheduled to air', f"{item['name']} · Episode {item['episode']} (AniList schedule)"):
                delivered[key] = event


def check_anilist(state, config, history, *, now=None, send=notify, defer=False):
    from ..core import anilist, storage
    from ..domain.history import refresh_history_entry_provider_catalog
    from ..domain.titles import get_show_display_title
    now = time.time() if now is None else now
    token = config.get('anilist_token')
    if not token:
        return
    import hashlib
    account = hashlib.sha256(token.encode()).hexdigest()
    if state.get('account') != account:
        for field in ('schedules', 'airing_notified', 'linked', 'anilist_attempted_at'):
            state.pop(field, None)
        state['account'] = account
    # Deliver saved events before querying: nextAiringEpisode moves forward after airing.
    scheduled_alerts(state, now, send=send)
    interval = config.get('history_refresh_minutes', 60) * 60
    if now - state.get('anilist_attempted_at', 0) < interval:
        return
    state['anilist_attempted_at'] = now
    shows = []
    for status in ('CURRENT', 'REPEATING', 'PLANNING'):
        for item in anilist.fetch_anilist_list(token, status, force_refresh=True):
            item = dict(item)
            item["_notification_planning"] = status == "PLANNING"
            shows.append(item)
    local = {identity(row.get('show', {})): row for row in history}
    schedules = state.setdefault('schedules', {})
    visited = set()
    for show in shows:
        aid = str(show.get('_id') or '')
        if not aid:
            continue
        key = f'al:{aid}'
        if key in visited:
            continue
        visited.add(key)
        planning = show.get('_notification_planning', False)
        at, episode = show.get('_next_airing_at'), show.get('_next_airing_ep')
        if at and episode and (not planning or episode == 1):
            schedules[key] = {'at': float(at), 'episode': episode, 'name': get_show_display_title(show), 'first_only': planning}
        if planning and schedules.get(key, {}).get("episode") != 1:
            schedules.pop(key, None)
        if planning and f"first:{key}" in state.get("delivered_releases", {}):
            continue
        if key in local:
            # Local history is checked by the main worker; do not repeat its request.
            continue
        match = storage.get_al_match(aid)
        if not match:
            continue
        entry = deepcopy(state.setdefault('linked', {}).get(key) or {
            'show': match, 'translation_type': config.get('translation_type', 'sub')})
        entry['show']['_anilist_id'] = int(aid)
        entry['episode'] = show.get('_anilist_progress') or 0
        entry['_notify_first_expected'] = bool(planning and schedules.get(key, {}).get('episode') == 1 and schedules[key].get('at', now + 1) <= now)
        if refresh_history_entry_provider_catalog(entry, enrich=False):
            observe_release(state, entry, send=send, defer=defer, first_only=planning)
            state['linked'][key] = entry
    scheduled_alerts(state, now, send=send)
