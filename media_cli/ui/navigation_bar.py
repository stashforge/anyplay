"""Core NavigationBar composition from supplied semantic pieces."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.terminal import display_width
from .navigation_counter import CounterMode, NavigationCounter
from .navigation_tabs import NavigationTabs
from .query_viewport import QueryField, layout_query
from .scroll_indicators import ScrollIndicators
from .styled_text import StyledText, TextRun, fit_styled_text, render_styled_text
from .theme import DEFAULT_THEME, Theme


@dataclass(frozen=True)
class NavigationBar:
    prompt: StyledText
    query: StyledText | QueryField | None = None
    counters: tuple[NavigationCounter, ...] = ()
    rule: bool = True
    tabs: NavigationTabs | None = field(default=None, kw_only=True)
    marker: StyledText | None = field(default=None, kw_only=True)
    scroll: ScrollIndicators | None = field(default=None, kw_only=True)
    query_start: int = field(default=0, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, StyledText):
            raise TypeError("NavigationBar.prompt requires StyledText")
        if self.query is not None and not isinstance(self.query, (StyledText, QueryField)):
            raise TypeError("NavigationBar.query requires StyledText, QueryField or None")
        if not isinstance(self.counters, tuple) or any(
            not isinstance(value, NavigationCounter) for value in self.counters
        ):
            raise TypeError("NavigationBar.counters requires an immutable tuple of NavigationCounter values")
        if not isinstance(self.rule, bool):
            raise TypeError("NavigationBar.rule requires bool")
        for name, value, kind in (
            ("tabs", self.tabs, NavigationTabs),
            ("marker", self.marker, StyledText),
            ("scroll", self.scroll, ScrollIndicators),
        ):
            if value is not None and not isinstance(value, kind):
                raise TypeError(f"NavigationBar.{name} requires {kind.__name__} or None")
        if type(self.query_start) is not int:
            raise TypeError("NavigationBar.query_start requires integer character offset")
        limit = len(self.query.text) if isinstance(self.query, QueryField) else 0
        if not 0 <= self.query_start <= limit:
            raise ValueError("NavigationBar.query_start must be within its QueryField")


@dataclass(frozen=True)
class NavigationLayout:
    content: StyledText
    query_start: int
    cursor_column: int | None


def _text(content: StyledText) -> str:
    return "".join(run.text for run in content.runs)


def _join(parts: tuple[StyledText, ...], separator: str = " ", role: str = "separator") -> StyledText:
    runs: list[TextRun] = []
    for part in parts:
        if not _text(part):
            continue
        if runs:
            runs.append(TextRun(separator, role))
        runs.extend(part.runs)
    return StyledText(tuple(runs))


def _head(prompt: StyledText, active: StyledText, marker: StyledText) -> StyledText:
    return _join((_join((prompt, active), " \u00b7 "), marker))


def _body(
    head: StyledText, query: StyledText, counters: tuple[NavigationCounter, ...], scroll: StyledText
) -> StyledText:
    counts = _join(tuple(counter.content() for counter in counters), " | ", "counter")
    return _join((_join((_join((head, query)), counts), "  "), scroll))


def layout_navigation_bar(bar: NavigationBar, *, width: int, theme: Theme = DEFAULT_THEME) -> NavigationLayout:
    """Allocate once, then use normal truncation for any remaining overflow."""
    if not isinstance(bar, NavigationBar):
        raise TypeError("render_navigation_bar requires NavigationBar")
    if not isinstance(theme, Theme):
        raise TypeError("NavigationBar.theme requires Theme")
    if type(width) is not int:
        raise TypeError("NavigationBar.width requires integer columns")
    if width < 1:
        raise ValueError("NavigationBar.width must be positive")
    budget = max(1, width - 1)
    empty = StyledText(())
    active_tab = bar.tabs.active if bar.tabs is not None else None
    active = StyledText((TextRun(active_tab.label, "tab_active"),)) if active_tab else empty
    marker = bar.marker or empty
    scroll = bar.scroll.content() if bar.scroll is not None else empty
    query_min = StyledText((TextRun(" ", "query"),)) if isinstance(bar.query, QueryField) else empty
    if isinstance(bar.query, StyledText) and _text(bar.query):
        query_min = fit_styled_text(bar.query, columns=1)

    # Validate source roles even when allocation later removes their content.
    sources = (bar.prompt, marker, active, scroll) + tuple(counter.content() for counter in bar.counters)
    if isinstance(bar.query, StyledText):
        sources += (bar.query,)
    for source in sources:
        for run in source.runs:
            theme.resolve_style(run.role)

    counters = bar.counters
    prompt = bar.prompt
    required = display_width(_text(_body(_head(prompt, active, marker), query_min, counters, scroll)))
    if required > budget:
        prompt_width = display_width(_text(prompt))
        prompt = fit_styled_text(prompt, columns=max(min(1, prompt_width), prompt_width - (required - budget)))
    head = _head(prompt, active, marker)
    if display_width(_text(_body(head, query_min, counters, scroll))) > budget:
        counters = tuple(counter for counter in counters if counter.mode is not CounterMode.SELECTED)
    if display_width(_text(_body(head, query_min, counters, scroll))) > budget:
        scroll = empty

    # If optional hints yielded, give their freed cells back to the label before
    # filling any remaining space with query content or decorative rule.
    required = display_width(_text(_body(_head(bar.prompt, active, marker), query_min, counters, scroll)))
    prompt_width = display_width(_text(bar.prompt))
    prompt = fit_styled_text(bar.prompt, columns=max(min(1, prompt_width), prompt_width - max(0, required - budget)))
    head = _head(prompt, active, marker)

    fixed = display_width(_text(_body(head, query_min, counters, scroll))) - display_width(_text(query_min))
    query_columns = max(1, budget - fixed)
    query_start, cursor_column = bar.query_start, None
    query = empty
    if isinstance(bar.query, QueryField):
        viewport = layout_query(bar.query, columns=query_columns, start=bar.query_start)
        query, query_start = viewport.content, viewport.start
        cursor_column = display_width(_text(head)) + (1 if _text(head) else 0) + viewport.cursor_column
    elif isinstance(bar.query, StyledText):
        query = fit_styled_text(bar.query, columns=query_columns)
    content = _body(head, query, counters, scroll)
    body = _text(content)
    remaining = budget - display_width(body)
    runs = list(content.runs)
    rule_gap = 2 if _text(scroll) else 1
    if bar.rule and remaining > (rule_gap if body else 0):
        if body:
            runs.append(TextRun(" " * rule_gap, "separator"))
            remaining -= rule_gap
        runs.append(TextRun("\u2500" * remaining, "rule"))
    result = fit_styled_text(StyledText(tuple(runs)), columns=budget)
    bounded = _text(result)
    if bounded != body and remaining < 0 and cursor_column is not None:
        if cursor_column >= display_width(bounded[:-1]):
            cursor_column = None
    return NavigationLayout(result, query_start, cursor_column)


def render_navigation_bar(bar: NavigationBar, *, width: int, theme: Theme = DEFAULT_THEME) -> str:
    result = layout_navigation_bar(bar, width=width, theme=theme)
    return render_styled_text(result.content, width=width, theme=theme)
