"""
Terminal banner and episode-header printers for Anyplay.

Extracted from ``app.py`` to keep cosmetic / startup output separate from
orchestration logic.
"""

from __future__ import annotations

from ..constants import APP_NAME
from ..domain.titles import extract_title_parts as _extract_title_parts
from .terminal_output import write_terminal


def print_app_banner() -> None:
    """Clear the screen and print the startup banner."""
    write_terminal("\033[2J\033[H")
    C, B, R = "\033[36m", "\033[1m", "\033[0m"
    line = f"{C}{'─' * 48}{R}"
    write_terminal(f"{line}\n{C}{B}▶ {APP_NAME} — Stream Player{R}\n{line}\n")


def print_episode_header(title: str, ep: object, total: int) -> None:
    """Print a "now playing" header above the player status area.

    Parameters
    ----------
    title:
        Full display title of the show (may include season/type tags).
    ep:
        Episode identifier (string or int).
    total:
        Total number of episodes in the catalog.
    """
    TITLE_C, R, SUB_C = "\033[1;38;2;145;185;245m", "\033[0m", "\033[38;2;170;180;195m"
    LABEL_C = "\033[1;38;2;190;180;235m"
    clean, sn, stype = _extract_title_parts(title)
    info_bits = []
    if sn:
        info_bits.append(f"season {sn}")
    if stype:
        info_bits.append(stype)
    ep_str = f"episode {ep} / {total}"
    if info_bits:
        bullet = "\u2022"
        ep_str += f"  {bullet}  {f' {bullet} '.join(info_bits)}"
    write_terminal(f"\n{LABEL_C}now playing{R}\n{TITLE_C}{clean}{R}\n{SUB_C}{ep_str}{R}\n\n")
