"""Bounded, session-only presentation positions for independently reopened views."""

from dataclasses import dataclass

from .picker_state import PickerState
from .view_model import visible_items


@dataclass(frozen=True)
class SavedViewPosition:
    selected_id: str | None
    query: str
    scroll: int
    query_cursor: int | None
    active_tab_id: str | None


class ViewPositionMemory:
    def __init__(self, ui, session, key, restore):
        self.ui, self.key = ui, key
        self.frame = session.frame(session.current.id, session.generation)
        self.pending = False
        self.saved = None
        if key is None:
            return
        if not isinstance(key, tuple) or not key or any(not isinstance(part, str) for part in key):
            raise TypeError("position_key requires a nonempty tuple of strings")
        if not isinstance(restore, bool):
            raise TypeError("restore_position requires a boolean")
        if not hasattr(ui, "_view_positions"):
            ui._view_positions = {}
        if not restore:
            ui._view_positions.pop(key, None)
        self.saved = ui._view_positions.get(key)
        self.pending = self.saved is not None

    def restore(self, session):
        if not self.pending or session.current.id != self.frame.view.id:
            return
        if not any(item.selectable for item in session.current.items):
            return
        if session.current.state == "loading" and self.saved.selected_id not in {item.id for item in session.current.items}:
            return
        saved, position = self.saved, self.frame.position
        visible = tuple(item for item in visible_items(session.current, saved.query) if item.selectable)
        valid = {item.id for item in visible}
        position.query = saved.query
        position.selected_id = saved.selected_id if saved.selected_id in valid else (visible[0].id if visible else None)
        position.scroll = min(saved.scroll, max(0, len(visible) - 1))
        if saved.active_tab_id in {tab.id for tab in session.current.tabs}:
            position.active_tab_id = saved.active_tab_id
        self.frame.picker_state = PickerState(
            query=saved.query,
            scroll=position.scroll,
            query_cursor=min(saved.query_cursor, len(saved.query)) if saved.query_cursor is not None else None,
        )
        self.frame.auto_entered = True
        self.pending = False

    def save(self):
        if self.key is None or self.pending or not any(item.selectable for item in self.frame.view.items):
            return
        position, picker = self.frame.position, self.frame.picker_state
        if picker is None:
            return
        memory = self.ui._view_positions
        memory.pop(self.key, None)
        memory[self.key] = SavedViewPosition(
            position.selected_id, position.query, position.scroll, picker.query_cursor, position.active_tab_id
        )
        while len(memory) > 32:
            memory.pop(next(iter(memory)))
