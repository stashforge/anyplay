"""Pure cell-bounded query layout; editing, timing and terminal writes stay outside."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.terminal import char_width, display_width
from .styled_text import StyledText, TextRun


@dataclass(frozen=True)
class QueryField:
    text: str
    cursor: int
    cursor_visible: bool

    def __post_init__(self) -> None:
        TextRun(self.text, "query")
        if type(self.cursor) is not int:
            raise TypeError("QueryField.cursor requires an integer character offset")
        if not 0 <= self.cursor <= len(self.text):
            raise ValueError("QueryField.cursor must be within the query")
        if type(self.cursor_visible) is not bool:
            raise TypeError("QueryField.cursor_visible requires bool")


@dataclass(frozen=True)
class QueryViewport:
    content: StyledText
    start: int
    end: int
    cursor_column: int
    hidden_before: bool
    hidden_after: bool


@dataclass(frozen=True)
class _Glyph:
    start: int
    end: int
    text: str
    columns: int


def _glyphs(text: str) -> tuple[_Glyph, ...]:
    """Keep zero-width combining marks with their base using shared width rules.

    This is not an extended-grapheme engine: it follows the existing terminal
    utilities, including their emoji/variation-selector width limitations.
    """
    if not text:
        return ()
    starts = (0,) + tuple(index for index in range(1, len(text)) if char_width(text[index]))
    glyphs = []
    for start, end in zip(starts, starts[1:] + (len(text),), strict=True):
        piece = text[start:end]
        columns = display_width(piece)
        if not columns:
            piece = "\u25cc" + piece
            columns = 1
        glyphs.append(_Glyph(start, end, piece, columns))
    return tuple(glyphs)


def layout_query(query: QueryField, *, columns: int, start: int = 0) -> QueryViewport:
    """Fit an allocated cell budget, retaining a supplied UI-owned scroll offset.

    Cursor/start are Python character offsets; output cursor_column is measured
    in terminal cells. A cursor inside combining marks highlights their complete
    base glyph. A wide glyph that cannot fit uses a one-cell cursor placeholder.
    The caller owns edits, blink timing and reuse of the returned start offset.
    """
    if not isinstance(query, QueryField):
        raise TypeError("layout_query requires QueryField")
    if type(columns) is not int:
        raise TypeError("QueryViewport.columns requires integer cells")
    if columns < 1:
        raise ValueError("QueryViewport.columns must be positive")
    if type(start) is not int:
        raise TypeError("QueryViewport.start requires an integer character offset")
    if not 0 <= start <= len(query.text):
        raise ValueError("QueryViewport.start must be within the query")

    glyphs = _glyphs(query.text)
    cursor = next((index for index, glyph in enumerate(glyphs) if query.cursor < glyph.end), len(glyphs))
    if query.cursor == len(query.text):
        glyphs += (_Glyph(query.cursor, query.cursor, " ", 1),)
    first = min(next((index for index, glyph in enumerate(glyphs) if start < glyph.end), len(glyphs)), cursor)
    role = "query_cursor" if query.cursor_visible else "query"
    caret = glyphs[cursor]
    if caret.columns > columns:
        return QueryViewport(
            StyledText((TextRun(" ", role),)),
            caret.start,
            caret.start,
            0,
            caret.start > 0,
            caret.start < len(query.text),
        )

    used = sum(glyph.columns for glyph in glyphs[first : cursor + 1])
    while used > columns:
        used -= glyphs[first].columns
        first += 1
    last = cursor + 1
    while last < len(glyphs) and used + glyphs[last].columns <= columns:
        used += glyphs[last].columns
        last += 1
    before = "".join(glyph.text for glyph in glyphs[first:cursor])
    after = "".join(glyph.text for glyph in glyphs[cursor + 1 : last])
    content = StyledText(
        tuple(TextRun(text, kind) for text, kind in ((before, "query"), (caret.text, role), (after, "query")) if text)
    )
    source_start, source_end = glyphs[first].start, glyphs[last - 1].end
    return QueryViewport(
        content, source_start, source_end, display_width(before), source_start > 0, source_end < len(query.text)
    )
