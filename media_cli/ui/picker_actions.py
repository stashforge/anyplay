"""Native picker capabilities, shared with loading instead of guessed per provider."""

from .actions import ActionAvailability, ActionBindings, ResolvedAction
from .view_model import ViewAction

CORE_PICKER_ACTIONS = (
    ViewAction("select", "Select", ("ENTER", "RIGHT")),
    ViewAction("back", "Back", ("ESC", "LEFT")),
    ViewAction("quit", "Quit", ("CTRL_C",)),
)


def loading_bindings(source: ActionBindings) -> ActionBindings:
    return ActionBindings(
        tuple(
            ResolvedAction(binding.action)
            if binding.action.id in {"back", "quit"}
            else ResolvedAction(binding.action, ActionAvailability.BLOCKED, "Still loading")
            for binding in source.items
        )
    )


def picker_bindings(
    *,
    tabs: bool = False,
    info: bool = False,
    delete: bool = False,
    reverse: bool = False,
    refresh: bool = False,
    multiple: bool = False,
    help_available: bool = False,
) -> ActionBindings:
    actions: tuple[ViewAction, ...] = CORE_PICKER_ACTIONS + (
        ViewAction("move", "Move", ("UP", "DOWN", "HOME", "END", "PAGE_UP", "PAGE_DOWN")),
        ViewAction("edit", "Edit filter", ("BACKSPACE", "CTRL_U")),
        ViewAction("downloads", "Downloads", ("ALT_D",)),
    )
    optional = (
        (tabs, ViewAction("tab", "Switch tab", ("TAB", "BTAB", "CTRL_N", "CTRL_P"))),
        (info, ViewAction("info", "Information", ("CTRL_O",))),
        (delete, ViewAction("delete", "Delete", ("DELETE", "CTRL_D"))),
        (reverse, ViewAction("reverse", "Reverse", ("CTRL_R",))),
        (refresh, ViewAction("refresh", "Refresh", ("ALT_R", "F5"))),
        (multiple, ViewAction("mark", "Mark", ("SPACE",))),
        (help_available, ViewAction("help", "Help", ("?",))),
    )
    actions += tuple(action for enabled, action in optional if enabled)
    return ActionBindings(tuple(ResolvedAction(action) for action in actions))
