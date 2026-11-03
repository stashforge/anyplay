"""Diagnostic translation scope without importing provider implementations."""


def call_for_translation(query, *args, translation='sub'):
    from .limits import check_budget
    check_budget()
    # Preserve the established injectable Sub callback contract.
    return query(*args) if translation == 'sub' else query(*args, translation=translation)


def selected_translations(provider_id, requested):
    from ..provider_manifest import PROVIDER_REGISTRY
    if requested not in ('sub', 'dub', 'both'):
        raise ValueError('Translation must be sub, dub or both')
    requested = ('sub', 'dub') if requested == 'both' else (requested,)
    languages = PROVIDER_REGISTRY.get(provider_id, {}).get('languages')
    if not languages or not any(value in ('sub', 'dub') for value in languages):
        # Song and general media catalogues do not use anime Sub/Dub semantics.
        return ('sub',), tuple(value for value in requested if value != 'sub')
    return tuple(value for value in requested if value in languages), tuple(value for value in requested if value not in languages)
