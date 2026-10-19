"""Reusable picker help content."""


def picker_help(
        select_label="Select",
        back_label="Go back",
        quit_label="Quit",
        tab_label=None,
        shift_tab_label=None,
        delete_label=None,
        reverse_label=None,
        info_label=None):
    help_items = {
        "↑ / ↓": "Move selection",
        "Home / End": "Top/bottom item; query start/end when filtering",
        "Page Up / Down": "Move one visible page up/down",
        "Type": "Filter visible items",
        "Backspace": "Edit filter",
        "Ctrl+U": "Clear filter",
        "Enter": select_label,
        "Right Arrow": f"{select_label}; moves cursor while editing a filter",
        "Left Arrow": f"{back_label} without a filter; otherwise move filter cursor",
        "Esc": quit_label,
        "?": "Show or hide this help",
        "Ctrl+C": "Exit program",
    }
    if info_label:
        help_items["Ctrl+O"] = info_label
    if tab_label:
        help_items["Tab"] = tab_label
        help_items["Ctrl+N"] = tab_label
    if shift_tab_label:
        help_items["Shift+Tab"] = shift_tab_label
        help_items["Ctrl+P"] = shift_tab_label
    if reverse_label:
        help_items["Ctrl+R"] = reverse_label
    if delete_label:
        help_items["Delete"] = delete_label
        help_items["Ctrl+D"] = delete_label
    return help_items



def search_input_help(esc_label="Cancel"):
    return {
        "Type": "Edit search query",
        "\u2190 / \u2192": "Move cursor",
        "\u2191 / \u2193": "Browse search history",
        "Backspace": "Delete character",
        "Ctrl+U": "Clear query",
        "Enter": "Search",
        "Esc": esc_label,
        "?": "Show or hide this help",
        "Ctrl+C": "Exit program",
    }
