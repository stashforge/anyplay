"""Presentation projection for non-selectable list section headings.

The source selection retains original identities and navigation; headings exist
only in the rendered projection, so they cannot receive input or count as results.
"""
from copy import copy


def grouped_presentation(state, groups, *, current='', name_fn=None):
    result = copy(state)
    entries, visible, selected = [], [], -1
    matching = set(state.visible())
    for heading, indexes in groups:
        indexes = [index for index in indexes if index in matching]
        if not indexes:
            continue
        visible.append(len(entries))
        entries.append(('section:' + heading, {'name': '-- ' + heading, 'heading': True}))
        for index in indexes:
            key, meta = state.entries[index]
            name = name_fn(key, meta) if name_fn else meta.get('name', key)
            if index == state.selected:
                selected = len(entries)
            visible.append(len(entries))
            entries.append((key, dict(meta, name=name, hint='')))
    result.entries, result.selected = entries, selected
    result.visible = lambda: visible
    result.result_total = len(state.entries)
    result.result_count = len(matching)
    return result
