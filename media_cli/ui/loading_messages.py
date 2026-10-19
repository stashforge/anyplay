"""Short loading messages with a little personality."""

from __future__ import annotations

from .actions import ActionAvailability, ActionBindings
from .message_choices import MAX_MESSAGE_LENGTH


def preparation_feedback_messages(
    action_id: str, actions: ActionBindings | None = None,
) -> tuple[str, ...] | None:
    if action_id == "select":
        return (
            "Already working on your selection. Just a moment.",
            "Got your selection. I'm still getting it ready.",
            "Your selection is already on its way. Hang on a moment.",
        )
    if action_id == "move":
        cancel_labels = tuple(dict.fromkeys(
            f"{label} Arrow" if key in {"UP", "DOWN", "LEFT", "RIGHT"} else label
            for binding in (actions.items if actions is not None else ())
            if binding.action.id == "back" and binding.availability is ActionAvailability.AVAILABLE
            for key, label in zip(binding.keys, binding.key_labels, strict=True)
        ))
        if cancel_labels:
            keys = " or ".join(cancel_labels)
            return tuple(message for message in (
                f"Loading. {keys} to cancel.",
                "Navigation paused. You can cancel to keep browsing.",
                "Still loading. You can cancel and stay here.",
                f"Want to browse? {keys} cancels.",
                "Let this load, or cancel to keep browsing.",
            ) if len(message) <= MAX_MESSAGE_LENGTH)
        return (
            "Still loading. Navigation is paused.",
            "Hang on. Can't navigate during this step.",
            "This screen is busy. Navigation is paused.",
            "Loading in progress. Navigation is paused.",
        )
    return None


def result_wait_messages(action_id: str) -> tuple[str, ...]:
    if action_id == "select":
        return (
            "Still finding results. Nothing to pick just yet.",
            "The results are on their way. Pick one in a moment.",
            "One moment. Let me find something to open first.",
        )
    if action_id == "move":
        return (
            "The list is on its way. Nothing to move through yet.",
            "Still finding results. You can browse them in a moment.",
            "Hang on, the results need a moment to arrive.",
        )
    raise ValueError("Result wait feedback requires select or move")


def episode_indexing(count: int | None = None) -> tuple[str, ...]:
    try:
        total = int(count or 0)
    except (TypeError, ValueError):
        total = 0
    if total > 0:
        return (
            f"Indexing {total} episodes…",
            f"Getting {total} episodes ready…",
            f"One moment while the list of {total} episodes loads.",
        )
    return (
        "Indexing episodes…",
        "Getting the episode list ready…",
        "One moment while the episode list loads.",
    )


def local_file_scan() -> tuple[str, ...]:
    return (
        "Checking local files…",
        "Looking through your downloaded episodes…",
        "Finding episodes already on this device…",
    )
