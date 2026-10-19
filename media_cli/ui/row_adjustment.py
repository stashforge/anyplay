"""Route outward filter-boundary arrows without knowing the row's setting."""


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
