import logging
from typing import Any, Optional, TypeVar

from ..parser.episodes import EpisodeRecognizer
from ..parser.languages import LanguageRecognizer
from ..parser.metadata import MetadataRecognizer
from ..parser.titles import TitleRecognizer
from .models import CanonicalAnime, EpisodeToken

logger = logging.getLogger(__name__)

T = TypeVar('T')

class AnimeBrain:
    @classmethod
    def process(cls, input_data: Any, format_specifier: type[T] = dict) -> T:
        if isinstance(input_data, dict):
            canonical_model = cls._parse_to_canonical(input_data)
        elif isinstance(input_data, str):
            canonical_model = cls._parse_to_canonical(input_data)
        else:
            raise TypeError("AnimeBrain expects a string or dictionary")

        if format_specifier is dict:
            return canonical_model.to_dict()
        elif format_specifier is CanonicalAnime:
            return canonical_model
        elif format_specifier is EpisodeToken:
            return canonical_model.episode
        else:
            return canonical_model

    @classmethod
    def _parse_to_canonical(cls, input_data: Any) -> CanonicalAnime:
        if isinstance(input_data, dict):
            return cls._parse_dict(input_data)
        return cls._parse_string(input_data)

    @classmethod
    def _parse_dict(cls, data: dict) -> CanonicalAnime:
        title_str = data.get("name") or data.get("title") or ""
        return cls._parse_string(title_str)

    @classmethod
    def _parse_string(cls, raw_title: str) -> CanonicalAnime:
        clean_text, tags = MetadataRecognizer.extract(raw_title)
        language_type, tags = LanguageRecognizer.evaluate(tags)
        clean_text, episode_token = EpisodeRecognizer.extract(clean_text)

        from .models import ReleaseType
        release_type = ReleaseType.UNKNOWN

        franchise, season, part = TitleRecognizer.extract(clean_text)

        return CanonicalAnime(
            raw_title=raw_title,
            franchise=franchise,
            episode=episode_token,
            season=season,
            part=part,
            release_type=release_type,
            language=language_type,
            tags=tags
        )

    @classmethod
    def find_exact_match(cls, target_title: dict, provider_results: list) -> Optional[dict]:
        """Deep cross-matching using AniList ID or Brain Canonical properties."""
        # 1. AniList ID Match
        target_ani = target_title.get("aniListId") or target_title.get("anilist_id")
        if target_ani:
            for res in provider_results:
                if str(res.get("aniListId") or res.get("anilist_id")) == str(target_ani):
                    return res

        # 2. String Match via Brain
        target_names = [target_title.get("name"), target_title.get("englishName"), target_title.get("nativeName")]
        target_names.extend(target_title.get("altNames") or [])
        target_names = [n for n in target_names if n]
        if not target_names:
            return None

        target_parsed_list = [cls.process(n, dict) for n in target_names]
        t_season = target_parsed_list[0].get("season") or 0
        t_part = target_parsed_list[0].get("part") or 0
        t_franchises = {p.get("franchise", "").lower().split(':')[0].strip() for p in target_parsed_list if p.get("franchise")}

        for res in provider_results:
            res_names = [res.get("name"), res.get("englishName"), res.get("nativeName")]
            res_names.extend(res.get("altNames") or [])
            res_names = [n for n in res_names if n]

            for r_name in res_names:
                res_parsed = cls.process(r_name, dict)
                r_franchise = res_parsed.get("franchise", "").lower().split(':')[0].strip()
                r_season = res_parsed.get("season") or 0
                r_part = res_parsed.get("part") or 0

                if r_franchise in t_franchises and r_season == t_season and r_part == t_part:
                    return res

        # 3. Soft Fallback: If no direct text match was found, assume the provider's search engine
        # returned the right show at index 0, provided the mathematically extracted Season and Part match!
        if provider_results:
            first_res = provider_results[0]
            res_parsed = cls.process(first_res.get("name") or "", dict)
            if (res_parsed.get("season") or 0) == t_season and (res_parsed.get("part") or 0) == t_part:
                return first_res

        return None
