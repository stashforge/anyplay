"""Activity-driven query cursor feedback, independent of keys and rendering."""

from dataclasses import dataclass


@dataclass
class QueryCursor:
    active_since: float | None

    def quiet(self) -> None:
        self.active_since = None

    def edit(self, now: float) -> None:
        self.active_since = now

    def phase(self, now: float, *, filtering: bool = False) -> tuple[bool, bool]:
        if self.active_since is None:
            return (True, int(now * 2.0) % 2 == 1) if filtering else (False, False)
        idle = max(0.0, now - self.active_since)
        if idle >= 3.0 and not filtering:
            return False, False
        if idle < 0.7:
            return True, True
        return True, int((idle - 0.7) * 2.0) % 2 == 1
