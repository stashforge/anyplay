"""Presentation position shared by the existing terminal pickers.

Selection and marks refer to underlying option indexes. Adapters own stable
identity mapping when replacing or reordering a view's options.
"""

from dataclasses import dataclass, field

from .query_cursor import QueryCursor


@dataclass
class PickerState:
    query: str = ""
    selected_index: int = 0
    scroll: int = 0
    marked_indices: set[int] = field(default_factory=set)
    query_cursor: int | None = None
    cursor_activity: QueryCursor | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.validate()
        self.marked_indices = set(self.marked_indices)

    def validate(self) -> None:
        """Raise when this mutable position state no longer satisfies its contract."""
        if not isinstance(self.query, str):
            raise TypeError("query must be a string")
        if self.query_cursor is not None:
            if type(self.query_cursor) is not int:
                raise TypeError("query_cursor must be an integer or None")
            if self.query_cursor < 0:
                raise ValueError("query_cursor must be nonnegative")
        if self.cursor_activity is not None and not isinstance(self.cursor_activity, QueryCursor):
            raise TypeError("cursor_activity requires QueryCursor or None")
        if type(self.selected_index) is not int:
            raise TypeError("selected_index must be an integer")
        if self.selected_index < 0:
            raise ValueError("selected_index must be nonnegative")
        if type(self.scroll) is not int:
            raise TypeError("scroll must be an integer")
        if self.scroll < 0:
            raise ValueError("scroll must be nonnegative")
        if not isinstance(self.marked_indices, set):
            raise TypeError("marked_indices must be a set of integers")
        for index in self.marked_indices:
            if type(index) is not int:
                raise TypeError("marked_indices must contain only integers")
            if index < 0:
                raise ValueError("marked_indices must contain only nonnegative integers")
