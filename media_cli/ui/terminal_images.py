"""Small lifecycle helper for terminal-native image overlays."""

from __future__ import annotations

from .picker_render import clear_terminal_images

_active = False


def mark_active() -> None:
    """Remember that the current terminal screen contains a native image."""
    global _active
    _active = True


def clear_now() -> str:
    """Return the terminal-image clear sequence and reset image state."""
    global _active
    _active = False
    return clear_terminal_images()


def clear_if_active() -> str:
    """Return a clear sequence only when a previous screen drew an image."""
    global _active
    if not _active:
        return ""
    _active = False
    return clear_terminal_images()


def replace_for_frame(*, has_image: bool) -> str:
    """Prepare deletion of the old image and ownership for the frame being written.

    Emit the returned prefix together with the ready frame and its native image.
    """
    global _active
    clear = clear_if_active()
    _active = has_image
    return clear
