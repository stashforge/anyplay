"""User-facing preparation labels shared by catalogue entry paths."""
def catalog_loading_message(show, translation_type='sub'):
    show = show or {}
    if show.get('_torrent') or show.get('_provider') == 'torrent':
        return 'Fetching torrent file list…'
    return f'Loading {translation_type.upper()} episodes…'
