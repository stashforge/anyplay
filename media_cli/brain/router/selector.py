
from .registry_loader import ProviderCapabilities


class ProviderSelector:
    """
    Ranks and selects providers based on query heuristics and user preferences.
    """

    @classmethod
    def rank_providers(
        cls,
        query: str,
        providers: dict[str, ProviderCapabilities],
        prefer_type: str = None,
        prefer_lang: str = "sub"
    ) -> list[str]:
        """
        Returns a sorted list of provider IDs (best match first).
        """
        scored_providers = []

        # Simple heuristic: if the query contains common Chinese pinyin/words, bump donghua preference
        is_likely_donghua = any(word in query.lower() for word in ['douluo', 'wanmei', 'shijie', 'cangqiong', 'fanren', 'tian'])

        if is_likely_donghua and not prefer_type:
            prefer_type = "donghua"

        for pid, p in providers.items():
            if p.status != "active":
                continue

            score = 0

            # 1. Type Match (Anime vs Donghua vs Movie)
            if prefer_type:
                if p.type == prefer_type:
                    score += 50
                elif prefer_type == "donghua" and p.type == "anime":
                    # Some anime providers (like AllAnime) have good donghua catalogs
                    if "donghua" in p.features:
                        score += 30
            else:
                if p.type == "anime":
                    score += 10 # Default to anime providers for generic queries

            # 2. Language Match
            if prefer_lang in p.languages:
                score += 20

            # 3. Feature Bonuses
            if "search" in p.features:
                score += 10
            if "fallback_servers" in p.features:
                score += 5

            scored_providers.append((score, pid))

        # Sort descending by score
        scored_providers.sort(reverse=True, key=lambda x: x[0])

        return [pid for score, pid in scored_providers]
