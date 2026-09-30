
from ..core.models import LanguageType


class LanguageRecognizer:
    """
    Evaluates tags extracted by MetadataRecognizer to identify audio/subtitle languages.
    """

    SUB_KEYWORDS = {"sub", "engsub", "english sub", "subbed", "espsub", "pt-br"}
    DUB_KEYWORDS = {"dub", "engdub", "english dub", "dubbed"}
    RAW_KEYWORDS = {"raw", "raws"}
    DUAL_KEYWORDS = {"dual audio", "multi-sub", "multi-audio", "dual-audio"}

    @classmethod
    def evaluate(cls, tags: list[str]) -> tuple[LanguageType, list[str]]:
        """
        Returns the deduced LanguageType and the remaining unrecognized tags.
        """
        language = LanguageType.UNKNOWN
        remaining_tags = []

        has_sub = False
        has_dub = False

        for tag in tags:
            t_lower = tag.lower()
            matched = False

            # Direct matches
            if any(k in t_lower for k in cls.DUAL_KEYWORDS):
                language = LanguageType.DUB # Dual audio implies both, but usually treated as DUB preference
                has_sub = True
                has_dub = True
                matched = True
            elif any(k in t_lower for k in cls.SUB_KEYWORDS):
                has_sub = True
                matched = True
            elif any(k in t_lower for k in cls.DUB_KEYWORDS):
                has_dub = True
                matched = True
            elif any(k in t_lower for k in cls.RAW_KEYWORDS):
                language = LanguageType.RAW
                matched = True

            if not matched:
                remaining_tags.append(tag)

        # Determine final enum if not explicitly RAW or DUAL
        if language == LanguageType.UNKNOWN:
            if has_sub and has_dub:
                language = LanguageType.DUB # In CLI context, dual is usually treated as a DUB source
            elif has_dub:
                language = LanguageType.DUB
            elif has_sub:
                language = LanguageType.SUB

        return language, remaining_tags
