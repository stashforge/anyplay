"""Provider list presentation: fixed chrome and configurable result anchoring."""
from .list_selection import ListSelection
from .fuzzy import fuzzy_match

from ..core.terminal import sanitize_terminal_text
from .screen_chrome import run_framed_screen
from ..state.theme import MINT_SCREEN_THEME


def category(meta):
    value = str(meta.get("type") or "anime").lower()
    return {"anime": "Anime", "donghua": "Donghua", "movies": "Movies / Shows", "movie": "Movies / Shows", "torrent": "Torrents"}.get(value, value.title())


def provider_info(key, meta, current):
    """Registry descriptions, not a live availability or quality verdict."""
    from urllib.parse import urlsplit
    name = str(meta.get("name") or key)
    langs = " / ".join(str(lang).title() for lang in meta.get("languages", ()) if lang != "video")
    kind = category(meta)
    description = meta.get("description") or f"Browse {kind.lower()} titles."
    if kind == "Tracker":
        detail = "Playback through a matched streaming provider."
        status = "Account linked" if meta.get("account_linked") else "Search available · Link account for library lists"
    else:
        engine = {"api": "API", "hybrid": "Hybrid", "scraper": "Site scraper"}.get(meta.get("engine"), "")
        domains = meta.get("domains") or ()
        domain = urlsplit(domains[0]).hostname if domains else ""
        detail = " · ".join(part for part in (engine, domain) if part)
        if not detail:
            detail = "Choose a provider for this search session."
        status = "Active for this session" if key == current else "Enter to select for this session"
    return (name, kind + (" · " + langs if langs else ""), str(description), detail, status)


def render_provider_info(key, meta, current, *, width=80, theme=MINT_SCREEN_THEME):
    """Semantic accents belong to the panel, not arbitrary whole-row colors."""
    from .styled_text import StyledText, TextRun, render_styled_text
    values = provider_info(key, meta, current)
    result = []
    for index, value in enumerate(values):
        role = "primary" if index == 0 else "secondary"
        if index == 4 and (value == "Active for this session" or value == "Account linked"):
            role = "success"
        runs = (TextRun(sanitize_terminal_text(value), role),)
        if index == 1:
            kind, separator, languages = value.partition(" · ")
            runs = (TextRun(kind, "info.category"),) + ((TextRun(separator, "secondary"), TextRun(languages, "library.value")) if separator else ())
        if index == 3 and meta.get("engine") in {"api", "hybrid", "scraper"} and category(meta) != "Tracker":
            method, separator, domain = value.partition(" · ")
            runs = (TextRun(method, "engine." + meta["engine"]),) + ((TextRun(separator, "secondary"), TextRun(domain, "library.value")) if separator else ())
        result.append(render_styled_text(StyledText(runs), width=width, theme=theme))
    return result


class ProviderSelection(ListSelection):
    def visible(self):
        query = self.query.casefold()
        matches = [i for i, (key, meta) in enumerate(self.entries)
                   if fuzzy_match(query, f"{meta.get('name', key)} {category(meta)} {' '.join(meta.get('languages',()))} {'songs' if category(meta) == 'Music' else 'trackers' if category(meta) == 'Tracker' else ''}") is not None]
        order = {name: index for index, name in enumerate(('Tracker', 'Music', 'Anime', 'Donghua', 'Movies / Shows', 'Torrents'))}
        return sorted(matches, key=lambda i: order.get(category(self.entries[i][1]), 99))

    def lines(self, width, height, _elapsed=0, *, theme=MINT_SCREEN_THEME):
        from .list_screen import list_screen_frame
        from copy import copy
        from .table import Column, Table
        from .styled_text import StyledText, TextRun
        presentation = copy(self)
        rows = [dict(name=StyledText((TextRun(meta.get('name',key), 'row_normal'),)
                                    + ((TextRun(' ✔', 'table.active'),) if key == self.current else ())),
                     type=category(meta), language=' / '.join(str(lang).title() for lang in meta.get('languages',())) or '—')
                for key,meta in self.entries]
        table = Table((Column('name','name',10,optional=False), Column('type','type',8,role='table.secondary'),
                       Column('language','translation',11,role='table.secondary')),rows,max(1,width-3))
        presentation.entries = [(key,dict(meta,name=rows[i]['name'].text,hint='',
            render_name=lambda _focus,_space,i=i:table.render(rows[i],theme=theme,query=self.query)))
            for i,(key,meta) in enumerate(self.entries)]
        presentation.visible = self.visible
        active = next((meta.get("name", key) for key, meta in self.entries if key == self.current), self.current)
        info = []
        if self.selected in self.visible():
            key, meta = self.entries[self.selected]
            info = render_provider_info(key, meta, self.current, width=width, theme=theme)
        frame = list_screen_frame(presentation, "Providers", width=width, height=height, info=info,
                                 columns_header=table.render({},theme=theme,header=True),
                                 badge=active + " ✔", placeholder="Filter providers…", empty="No matching providers",
                                 shortcuts=(("enter", "select"), ("↑↓", "move"), ("/", "filter"), ("?", "help"), ("esc", "back")), theme=theme)
        self.scroll_start = presentation.scroll_start
        return frame.lines


def run_provider_selection(entries, current, *, layout="bottom"):
    state = ProviderSelection(entries, current, bottom=layout != "top")
    state.selected = next((i for i, (key, _meta) in enumerate(entries) if key == current), 0)
    def key(value):
        if value == "?" and not state.filtering:
            from .key_guide import list_key_groups, show_key_guide
            from .input_result import KeyResult
            show_key_guide(list_key_groups({"Enter": "Choose provider for this session", "↑ / ↓": "Move selection",
                "Esc": "Clear filter or return to Search"}))
            return KeyResult.CONSUMED
        return state.key(value)
    return run_framed_screen(state.lines, key)
