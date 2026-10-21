"""Route outward filter-boundary arrows without knowing the row's setting."""

from collections.abc import Sequence
from typing import TypeVar

_T = TypeVar("_T")


def cycle_value(current: _T, values: Sequence[_T], direction: int = 1) -> _T:
    """Step through the declared values, wrapping at either end."""
    if not values:
        raise ValueError("Cycling requires at least one value")
    if type(direction) is not int or direction not in (-1, 1):
        raise ValueError("Cycling direction must be -1 or 1")
    index = values.index(current)
    return values[(index + direction) % len(values)]


def adjustment_direction(key: str, query: str, cursor: int) -> int | None:
    if not isinstance(key, str) or not isinstance(query, str):
        raise TypeError("Adjustment key and query require strings")
    if type(cursor) is not int:
        raise TypeError("Adjustment cursor requires an integer")
    if not 0 <= cursor <= len(query):
        raise ValueError("Adjustment cursor must be inside the query")
    if key == "LEFT" and cursor == 0:
        return -1
    if key == "RIGHT" and cursor == len(query):
        return 1
    return None
