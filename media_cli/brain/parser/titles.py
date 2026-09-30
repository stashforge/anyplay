import re
from typing import Optional

from .patterns import PART_PATTERNS, SEASON_PATTERNS


class TitleRecognizer:
    EXTENSION_REGEX = re.compile(r'\.(mkv|mp4|avi|srt|ass)$', re.IGNORECASE)

    ROMAN_MAP = {
        'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10,
        'Ⅱ': 2, 'Ⅲ': 3, 'Ⅳ': 4, 'Ⅴ': 5, 'Ⅵ': 6, 'Ⅶ': 7, 'Ⅷ': 8, 'Ⅸ': 9, 'Ⅹ': 10
    }

    @classmethod
    def extract(cls, raw_title: str) -> tuple[str, Optional[int], Optional[int]]:
        clean_title = cls.EXTENSION_REGEX.sub('', raw_title)
        season_num = None
        part_num = None

        # Check all season patterns
        for pattern in SEASON_PATTERNS:
            match = pattern.search(clean_title)
            if match:
                val = match.group(1)
                if val.upper() in cls.ROMAN_MAP:
                    season_num = cls.ROMAN_MAP[val.upper()]
                else:
                    season_num = int(val)
                start, end = match.span()
                clean_title = clean_title[:start] + clean_title[end:]
                break # Stop after finding the first valid season

        # Check all part patterns
        for pattern in PART_PATTERNS:
            match = pattern.search(clean_title)
            if match:
                part_num = int(match.group(1))
                start, end = match.span()
                clean_title = clean_title[:start] + clean_title[end:]
                break

        clean_title = re.sub(r'\s+', ' ', clean_title).strip()
        clean_title = re.sub(r'^[-\s_]+|[-\s_]+$', '', clean_title)

        return clean_title, season_num, part_num
