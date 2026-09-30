import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

class FallbackOrchestrator:
    """
    Takes a ranked list of provider IDs and executes a task against them sequentially.
    If a provider fails (throws an exception) or returns empty data, it transparently
    falls back to the next provider in the ranked list.
    """

    @classmethod
    def execute(
        cls,
        ranked_provider_ids: list[str],
        provider_instances: dict,
        task: Callable[[Any], Any]
    ) -> Any:
        """
        Executes a callable `task(provider_instance)` across the ranked list.
        Returns the first successful, non-empty result.
        """
        errors = []

        for pid in ranked_provider_ids:
            if pid not in provider_instances:
                logger.warning(f"Provider '{pid}' is not instantiated. Skipping.")
                continue

            provider = provider_instances[pid]

            try:
                # print(f"  [Router] Attempting Provider: {pid}") # For debugging UI
                result = task(provider)

                # If result is empty (e.g. empty list of sources or search results), treat as failure
                if not result:
                    errors.append(f"{pid}: Returned empty data.")
                    continue

                # Success!
                return {"provider_id": pid, "data": result}

            except Exception as e:
                errors.append(f"{pid}: Exception - {str(e)}")
                continue

        # If we exhausted all providers
        raise Exception(f"All providers failed in the fallback loop. Errors: {errors}")
