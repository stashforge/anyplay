from typing import Optional

from ..core.models import EpisodeToken
from .patterns import EPISODE_PATTERNS


class EpisodeRecognizer:

    @classmethod
    def extract(cls, raw_title: str) -> tuple[str, Optional[EpisodeToken]]:
        clean_title = raw_title
        token = None

        for pattern in EPISODE_PATTERNS:
            match = pattern.search(clean_title)
            if match:
                # If it's a version tag (v2), just strip it and continue searching for the real episode
                if 'v' in match.group(0).lower() and 'ova' not in match.group(0).lower():
                    start, end = match.span()
                    clean_title = clean_title[:start] + clean_title[end:]
                    continue

                raw_label = match.group(1)
                is_special = False
                full_match = match.group(0).lower()

                if any(x in full_match for x in ['sp', 'ova', 'oad', 'ncop', 'nced']):
                    is_special = True

                token = EpisodeToken(
                    raw_label=raw_label,
                    absolute_number=float(raw_label),
                    is_special=is_special
                )

                start, end = match.span()
                clean_title = clean_title[:start] + clean_title[end:]
                break

        clean_title = clean_title.strip(' -_')
        return clean_title, token
