"""Shared selection and slash-filter editing state for list screens."""
from dataclasses import dataclass

from .fuzzy import fuzzy_match

@dataclass
class ListSelection:
    entries: list[tuple[str, dict]]
    current: str
    selected: int = 0
    query: str = ""
    filtering: bool = False
    cursor: int = 0
    bottom: bool = True
    scroll_start: int = 0

    def visible(self):
        return [i for i, (key, meta) in enumerate(self.entries)
                if fuzzy_match(self.query, f"{meta.get('name', key)} {meta.get('hint', '')}") is not None]

    def key(self, key):
        if key in ("CTRL_C", "EOF", ""):
            return -2
        if key == "ESC":
            if self.filtering:
                self.query, self.cursor, self.filtering = "", 0, False
            else:
                return -1
        elif key == "/" and not self.filtering:
            self.filtering = True
        elif key in ("LEFT", "RIGHT") and self.filtering:
            self.cursor = max(0, min(len(self.query), self.cursor + (-1 if key == "LEFT" else 1)))
        elif key in ("UP", "DOWN", "HOME", "END") and not (self.filtering and key in ("HOME", "END")):
            visible = self.visible()
            if visible:
                pos = visible.index(self.selected) if self.selected in visible else 0
                if key in ("HOME", "END"):
                    pos = 0 if key == "HOME" else len(visible) - 1
                else:
                    pos = (pos + (-1 if key == "UP" else 1)) % len(visible)
                self.selected = visible[pos]
        elif key == "ENTER":
            if self.selected in self.visible():
                return self.selected
        elif self.filtering:
            if key == "CTRL_U":
                self.query, self.cursor = "", 0
            elif key in ("HOME", "END"):
                self.cursor = 0 if key == "HOME" else len(self.query)
            elif key == "BACKSPACE" and self.cursor:
                self.query = self.query[:self.cursor - 1] + self.query[self.cursor:]
                self.cursor -= 1
            elif key == "DELETE":
                self.query = self.query[:self.cursor] + self.query[self.cursor + 1:]
            elif len(key) == 1 and key.isprintable():
                self.query = self.query[:self.cursor] + key + self.query[self.cursor:]
                self.cursor += 1
            visible = self.visible()
            if visible and self.selected not in visible:
                self.selected = visible[0]
        return None

