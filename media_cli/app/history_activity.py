"""Prepare a local activity snapshot once; view rendering never reads files."""
import os

from ..core.terminal import sanitize_terminal_text
from ..domain.history import format_relative_time
from ..providers.shared.models import title_provider_key
from ..state.preferences import resume_time


class HistoryActivity(dict):
    """Activity labels plus the same scan's structured download counts."""
    def __init__(self, counts):
        super().__init__()
        self.download_counts = counts


def history_activity(entries, preferences, downloads, *, now=None):
    counts = {}
    for stored in downloads.get('shows', {}).values():
        meta = stored.get('metadata') or {}
        identity = meta.get('_id') if meta.get('_id') is not None else meta.get('id')
        if identity is None:
            continue
        count = 0
        for paths in (stored.get('episode_files') or {}).values():
            if isinstance(paths, str):
                paths = [paths]
            if isinstance(paths, list) and any(isinstance(path, str) and os.path.isfile(path) for path in paths):
                count += 1
        counts[(title_provider_key(meta), str(identity))] = count
    result = HistoryActivity(counts)
    for entry in entries:
        show = entry.get('show') or {}
        identity = show.get('_id') if show.get('_id') is not None else show.get('id')
        provider = title_provider_key(show)
        episode = entry.get('episode')
        try:
            seconds = max(0, resume_time(preferences, identity, episode, provider))
        except (TypeError, ValueError, OverflowError):
            seconds = 0
        parts = []
        if seconds:
            label = entry.get('episode_label') or episode
            parts.append(f'Continue EP {label} · {seconds // 60:02}:{seconds % 60:02}')
        else:
            age = format_relative_time(entry.get('timestamp'), now=now)
            if age:
                parts.append(f'Last activity {age}')
        count = counts.get((provider, str(identity)), 0)
        if count:
            parts.append(f'{count} downloaded')
        result[(provider, str(identity), entry.get('translation_type', 'sub'))] = sanitize_terminal_text(' · '.join(parts))
    return result
