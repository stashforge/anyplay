"""Render optional-dependency hints using existing semantic footer roles."""

from ..services.requirements import dependency_hint_messages
from .footer import Footer, FooterStatus, StatusKind, render_footer


def render_dependency_hint_footer(payload: dict | None, width: int) -> str:
    messages = dependency_hint_messages(payload)
    if not messages:
        return ""
    return render_footer(Footer(
        statuses=tuple(FooterStatus(message, StatusKind.WARNING) for message in messages),
        actions=("Esc=back",),
    ), width)
