"""One frame/image owner for all standard list renderers and footer overlays."""
from ..core.terminal import absolute_terminal_frame
from . import terminal_images

_lines = None
_size = None
_image = None
_epoch = -1


def invalidate_text():
    """Repaint after screen handoff without invalidating its native image."""
    global _lines
    _lines = None


def invalidate_row(row):
    if _lines is not None and 1 <= row <= len(_lines):
        _lines[row - 1] = None


def compose(frame, rows, columns):
    global _lines, _size, _image, _epoch
    lines = frame.lines
    from .viewport import padding_for
    import os
    try:
        physical = os.get_terminal_size()
        viewport = (physical.columns, physical.lines, padding_for(physical.columns, physical.lines))
    except OSError:
        viewport = None
    size = (rows, columns, viewport)
    key = (frame.poster, frame.poster_row, frame.poster_column,
           frame.poster_width, frame.poster_height, size) if frame.poster else None
    resized = _size is not None and size != _size
    valid = _epoch == terminal_images.generation() and size == _size
    previous = _lines if valid else None
    old_image = _image if valid else None
    unchanged_image = bool(key and key == old_image)
    clear, overlay = '', ''
    if key != old_image or not valid:
        clear = terminal_images.clear_if_active()
        if previous is not None and old_image:
            previous = list(previous)
            for row in range(max(0, old_image[1] - 1), min(len(previous), old_image[1] - 1 + old_image[4])):
                previous[row] = None
        if key:
            overlay = f'\033[{frame.poster_row};{frame.poster_column + 1}H' + frame.poster
            terminal_images.mark_active()
    protected = (frame.poster_row, frame.poster_column, frame.poster_width, frame.poster_height) if unchanged_image else None
    text = absolute_terminal_frame(lines, rows, columns, previous, protected_image=protected)
    _lines, _size, _image = list(lines), size, key
    _epoch = terminal_images.generation()
    from ..state.theme import MINT_SCREEN_THEME
    canvas_clear = '\033[2J' if resized or (not valid and MINT_SCREEN_THEME.background is not None) else ''
    return clear + canvas_clear + text + overlay
