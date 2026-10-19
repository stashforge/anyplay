"""Keep application preparation off the terminal's input/render thread."""

from __future__ import annotations

import logging
import sys
from copy import deepcopy

from ..ui.display import with_loading
from ..ui.transition import PreparationCancelled

_log = logging.getLogger(__name__)


def run_screen_controller(handler, flags, ui, ms, cfg, args, *extra, fallback):
    """Prepare detached application state; UI entry points marshal to the owner.

    A handler may synchronously request a picker, but its work before/between
    pickers runs on the preparation worker. Cancelling discards that handler's
    detached state rather than allowing late results to replace a newer screen.
    """
    if flags.plain_mode or not sys.stdin.isatty():
        return handler(flags, ui, ms, cfg, args, *extra)
    draft = []

    def prepare():
        next_ui, next_ms, next_cfg, next_args = deepcopy((ui, ms, cfg, args))
        draft.append(next_ui)
        result = handler(flags, next_ui, next_ms, next_cfg, next_args, *extra)
        return result, next_ui, next_ms, next_cfg, next_args

    try:
        result, next_ui, next_ms, next_cfg, next_args = with_loading("Preparing screen…", prepare)
    except PreparationCancelled:
        if draft:
            ui._resume_picker_position = getattr(draft[0], "_last_picker_position", None)
        ms.just_searched = False
        ms.just_picked_anime = False
        _log.debug("Screen preparation cancelled: handler=%s fallback=%s", handler.__name__, fallback)
        return fallback
    vars(ui).update(vars(next_ui))
    vars(ms).update(vars(next_ms))
    vars(args).update(vars(next_args))
    cfg.update(next_cfg)
    _log.debug("Screen preparation accepted: handler=%s destination=%s", handler.__name__, result)
    return result
