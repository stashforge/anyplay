"""Small theme-aware table projection shared by list screens.

Widths are chosen from the full data set, so filtering never shifts columns.
Optional trailing columns disappear only when their minimum widths cannot fit.
"""
from dataclasses import dataclass
from ..core.terminal import display_width, sanitize_terminal_text, truncate_display
from .styled_text import StyledText, TextRun, render_styled_text


def _cell_text(value):
    return sanitize_terminal_text(value.text if isinstance(value, StyledText) else value)


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    minimum: int = 4
    role: str = 'row_normal'
    optional: bool = True
    align: str = 'left'


class Table:
    def __init__(self, columns, rows, width, *, gap=3):
        self.gap = gap
        self.columns = list(columns)
        self.widths = [max(display_width(column.label), column.minimum,
                           max((display_width(_cell_text(row.get(column.key, ''))) for row in rows), default=0))
                       for column in self.columns]
        width = max(1, width)
        while len(self.columns) > 1 and sum(c.minimum for c in self.columns) + gap*(len(self.columns)-1) > width:
            index = next((i for i in range(len(self.columns)-1,0,-1) if self.columns[i].optional), None)
            if index is None:
                break
            self.columns.pop(index)
            self.widths.pop(index)
        while sum(self.widths) + gap*(len(self.columns)-1) > width:
            candidates = [i for i,c in enumerate(self.columns) if self.widths[i] > c.minimum]
            if not candidates:
                break
            index = max(candidates, key=lambda i:self.widths[i]-self.columns[i].minimum)
            self.widths[index] -= 1
        if len(self.columns) == 1:
            self.widths[0] = min(self.widths[0],width)

    def render(self, row, *, theme, query='', header=False):
        from .row_text import render_row_text
        output = []
        for column,width in zip(self.columns,self.widths):
            content = column.label if header else row.get(column.key, '')
            value = truncate_display(_cell_text(content),width)
            role = 'screen.column' if header else column.role
            styled = content if isinstance(content, StyledText) and not header else StyledText((TextRun(value,role),))
            padding = max(0, width-display_width(value))
            if column.align == 'right':
                output.append(' '*padding)
            output.append(render_styled_text(styled,width=width+1,theme=theme) if header else
                          render_row_text(styled,columns=width,query=query,theme=theme))
            output.append(' '*((padding if column.align != 'right' else 0)+self.gap))
        return ''.join(output[:-1])
