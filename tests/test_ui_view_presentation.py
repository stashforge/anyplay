import time
from dataclasses import replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.footer import Footer, FooterStatus, StatusKind, render_footer
from media_cli.ui.panels import C_ALT, C_META, C_RESET, C_TITLE
from media_cli.ui.poster import PosterManager
from media_cli.ui.view_model import Blank, Content, DetailLines, ImageSpec, Inherit, ViewAction, ViewItem, ViewSpec
from media_cli.ui.view_presentation import ViewPresentation
from media_cli.ui.view_session import PopView, PushView, ReplaceView, UpdateItems, ViewSession


class FakePosterManager:
    def __init__(self, state="loading", enabled=True):
        self.state = state
        self.active = enabled
        self.get_calls = []
        self.footer_calls = []

    def enabled(self):
        return self.active

    def loading_frame(self):
        return "~"

    def get(self, show):
        self.get_calls.append(show)
        show["_poster_status"] = self.state
        if self.state == "ready":
            show["_poster_raw"] = "POSTER"
            return "POSTER"
        return ""

    def footer_line(self, show, default_text, width, *, persistent=False):
        assert persistent
        self.footer_calls.append((show, default_text, width))
        assert self.get_calls[-1] is show
        status = {"loading": "Loading cover", "failed": "Cover failed"}.get(show["_poster_status"], "")
        if isinstance(default_text, Footer):
            if status:
                default_text = replace(default_text, statuses=default_text.statuses + (FooterStatus(status),))
            return render_footer(default_text, width)
        return " • ".join(part for part in (status, default_text) if part)

    def needs_tick(self, show):
        return show.get("_poster_status") == "loading"


def setup(image=None, **kwargs):
    manager = FakePosterManager()
    item = ViewItem("a", "Label", image=image, details=Inherit)
    clock = kwargs.pop("clock", time.monotonic)
    loading_delay = kwargs.pop("loading_delay", 0.0)
    kwargs.setdefault("details", DetailLines(Blank, Blank, Blank))
    session = ViewSession(ViewSpec("root", "Prompt", (item,), **kwargs), clock=clock, loading_delay=loading_delay)
    return ViewPresentation(session, manager), session, manager


def update_item(session, **kwargs):
    item = replace(session.current.items[0], **kwargs)
    session.apply(UpdateItems(session.current.id, session.generation, (item,)))


@pytest.mark.parametrize("state, expected", [
    ("loading", "Loading..."), ("empty", "No results"),
    ("failed", "Unable to load results"), ("ready", "No results"),
])
def test_empty_views_have_status_fallback_without_placeholder_items(state, expected):
    session = ViewSession(ViewSpec("root", "Prompt", state=state, details=DetailLines(Blank, Blank, Blank)))
    presentation = ViewPresentation(session, FakePosterManager())
    assert presentation.status_text() == expected
    assert expected in strip_ansi(presentation.details()).splitlines()[-1]
    assert expected in presentation.plain_details()[-1]
    assert len(presentation.details().splitlines()) == 4


@pytest.mark.parametrize("state", ["loading", "ready", "failed", "no_chafa"])
def test_footer_and_poster_share_persistent_state(state):
    presentation, session, manager = setup(ImageSpec("cover", "available", "https://example.test/a"))
    manager.state = state
    show = presentation.show_for("a")
    assert presentation.poster("a") == ("POSTER" if state == "ready" else "")
    header = presentation.details("a", 120)
    assert manager.get_calls[0] is manager.get_calls[-1] is manager.footer_calls[-1][0] is show
    assert presentation.show_for("a") is show
    assert presentation.selected_show_id == show["_id"]
    assert presentation.needs_tick() == (state == "loading")
    assert presentation.status_text("a") == {
        "loading": "Loading cover", "ready": "", "failed": "Cover failed", "no_chafa": "Cover needs chafa",
    }[state]
    if state in {"loading", "failed"}:
        assert presentation.status_text("a") in strip_ansi(header)


@pytest.mark.parametrize("state,label", [("resolving", "Fetching cover"), ("unavailable", "Cover unavailable"), ("failed", "Cover failed")])
def test_nonavailable_images_never_start_manager_work(state, label):
    presentation, _, manager = setup(ImageSpec("cover", state))
    assert label in presentation.details("a", 100)
    assert presentation.poster("a") == ""
    assert presentation.status_text("a") == label
    assert presentation.needs_tick() == (state == "resolving")
    assert not manager.get_calls and not manager.footer_calls
    if state == "resolving":
        assert "~ Fetching cover" in presentation.details("a", 100)


def test_item_overrides_view_and_missing_item_uses_view_defaults():
    details = DetailLines(Content("View"), Content("Secondary"), Content("Metadata"))
    image = ImageSpec("view", "available", "https://example.test/view")
    presentation, session, _ = setup()
    session.apply(ReplaceView("root", session.generation, replace(session.current, details=details, image=image)))
    assert presentation.plain_details("a")[:3] == ["View", "Secondary", "Metadata"]
    assert presentation.show_for("a")["thumbnail"] == image.url
    update_item(session, details=DetailLines(Content("Item"), Blank, Blank), image=ImageSpec("item", "unavailable"))
    assert presentation.plain_details("a")[:3] == ["Item", "", ""]
    assert presentation.show_for("a")["thumbnail"] == ""
    assert presentation.selected("missing") is None
    assert presentation.plain_details("missing")[:3] == ["View", "Secondary", "Metadata"]
    assert presentation.show_for("missing")["thumbnail"] == image.url
    assert presentation.selected().id == "a"


@pytest.mark.parametrize("key,url", [("new", "https://example.test/a"), ("cover", "https://example.test/b")])
def test_image_identity_changes_discard_stale_state_even_when_identity_returns(key, url):
    original = ImageSpec("cover", "available", "https://example.test/a")
    presentation, session, manager = setup(original)
    old = presentation.show_for()
    old["_poster_raw"] = "OLD"
    update_item(session, image=ImageSpec(key, "available", url))
    current = presentation.show_for()
    assert current is not old and current["_id"] != old["_id"]
    assert "_poster_raw" not in current
    old["_poster_status"] = "ready"
    presentation.details("a", 80)
    assert manager.footer_calls[-1][0] is current
    update_item(session, image=original)
    assert presentation.show_for() is not old


def test_generation_isolation_and_parent_restoration():
    presentation, session, _ = setup(ImageSpec("cover", "unavailable"))
    parent = presentation.show_for()
    session.apply(PushView(session.current))
    child = presentation.show_for()
    assert child is not parent and child["_id"] != parent["_id"]
    session.apply(PopView())
    assert presentation.show_for() is parent
    update_item(session, label="Renamed")
    assert presentation.show_for() is parent
    assert parent["name"] == "Renamed"


@pytest.mark.parametrize("width", [1, 2, 8, 24, 80])
def test_exact_four_lines_colors_and_terminal_width(width):
    presentation, _, _ = setup(
        ImageSpec("cover", "resolving"),
        details=DetailLines(Content("Long title 界" * 12), Content("Second\nline"), Content("Meta\033[31mdata")),
        context=("Context",),
    )
    lines = presentation.details("a", width).split("\n")
    assert len(lines) == 4
    for line, color in zip(lines, (C_TITLE, C_ALT, C_META, "\033[38;5;244m")):
        assert line.startswith(color) and line.endswith(C_RESET)
        assert display_width(line) <= max(1, width - 1)


def test_context_and_only_declared_action_keys():
    presentation, _, manager = setup(
        ImageSpec("cover", "available", "https://example.test/a"),
        context=("Artist", "Songs"),
        actions=(ViewAction("play", "Play", ("Enter",)), ViewAction("info", "Info", ("i", "F1")), ViewAction("save", "Save")),
    )
    presentation.details("a", 200)
    assert strip_ansi(render_footer(manager.footer_calls[-1][1], 200)) == "Artist • Songs • Enter=Play • i/F1=Info • Save • Esc=back"


def test_plain_details_and_info_are_direct_safe_and_never_fetch():
    presentation, session, manager = setup(
        ImageSpec("cover", "resolving"),
        details=DetailLines(Content("\033[31mTitle\033[0m"), Content("Secondary"), Content("Metadata")),
        context=("Artist", "Songs"), state="loading", message="Finding songs",
    )
    assert presentation.plain_details("a") == ["Title", "Secondary", "Metadata", "Artist • Songs • Finding songs • Fetching cover"]
    assert presentation.info("a") == "Title\nSecondary\nMetadata"
    update_item(session, info="Explicit information\nAlready supplied")
    assert presentation.info("a") == "Explicit information\nAlready supplied"
    assert not manager.get_calls and not manager.footer_calls


def test_disabled_images_skip_loading_and_ticks():
    presentation, _, manager = setup(ImageSpec("cover", "resolving"))
    manager.active = False
    assert "Fetching" not in presentation.details("a", 80)
    assert presentation.status_text("a") == ""
    assert not presentation.needs_tick()
    assert presentation.poster("a") == ""
    assert not manager.get_calls


@pytest.fixture
def cover_loading_reads(monkeypatch):
    from media_cli.ui.background_loading import BackgroundLoadingStatus

    reads = []

    class ObservedStatus(BackgroundLoadingStatus):
        def read(self, active, **kwargs):
            reads.append((self._spec.owner.id, active))
            return super().read(active, **kwargs)

    monkeypatch.setattr("media_cli.ui.view_presentation.BackgroundLoadingStatus", ObservedStatus)
    return reads


@pytest.mark.parametrize("state", ["available", "unavailable", "failed", "disabled"])
def test_cover_resolution_task_retires_when_resolution_ends(cover_loading_reads, state):
    presentation, session, manager = setup(ImageSpec("cover", "resolving", message="Artist portrait"))
    assert "~ Fetching cover: Artist portrait" in strip_ansi(presentation.details("a", 120))
    assert len(cover_loading_reads) == 1
    owner, active = cover_loading_reads[0]
    assert active is True
    if state == "disabled":
        manager.active = False
    else:
        url = "https://example.test/a" if state == "available" else None
        update_item(session, image=ImageSpec("cover", state, url))
    presentation.details("a", 120)
    assert cover_loading_reads == [(owner, True), (owner, False)]


def test_cover_resolution_switch_retires_old_task_before_new_one(cover_loading_reads):
    presentation, session, _ = setup(ImageSpec("first", "resolving"))
    presentation.details("a", 120)
    old_owner = cover_loading_reads[0][0]
    update_item(session, image=ImageSpec("second", "resolving"))
    presentation.details("a", 120)
    assert cover_loading_reads[:2] == [(old_owner, True), (old_owner, False)]
    assert cover_loading_reads[2][1] is True
    assert cover_loading_reads[2][0] != old_owner


def test_plain_cover_status_does_not_start_loading_lifecycle(cover_loading_reads):
    presentation, _, _ = setup(ImageSpec("cover", "resolving"))
    assert "Fetching cover" in presentation.plain_details("a")[-1]
    assert cover_loading_reads == []


@pytest.mark.parametrize("end", ["ready", "failed", "navigation"])
def test_default_empty_view_loading_uses_shared_task_and_retires(cover_loading_reads, end):
    from media_cli.ui.view_session import BeginNavigation

    view = ViewSpec("root", "Results", state="loading", details=DetailLines(Blank, Blank, Blank))
    session = ViewSession(view, loading_delay=0)
    presentation = ViewPresentation(session, FakePosterManager())
    assert "~ Loading..." in strip_ansi(presentation.details(width=120))
    assert len(cover_loading_reads) == 1
    owner = cover_loading_reads[0][0]
    if end == "navigation":
        session.apply(BeginNavigation("root", session.generation, "open", "Fetching item"))
    else:
        session.apply(ReplaceView("root", session.generation, replace(view, state=end)))
    presentation.details(width=120)
    assert cover_loading_reads == [(owner, True), (owner, False)]


def test_default_loading_plain_status_starts_no_task(cover_loading_reads):
    view = ViewSpec("root", "Results", state="loading", details=DetailLines(Blank, Blank, Blank))
    presentation = ViewPresentation(ViewSession(view), FakePosterManager())
    assert presentation.plain_details()[-1] == "Loading..."
    assert cover_loading_reads == []


@pytest.mark.parametrize("with_items", [False, True])
def test_custom_loading_message_updates_same_task_then_retires(monkeypatch, with_items):
    from media_cli.ui.background_loading import BackgroundLoadingStatus
    from media_cli.ui.view_session import UpdateStatus

    adapters = []

    class ObservedStatus(BackgroundLoadingStatus):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            adapters.append(self)

    monkeypatch.setattr("media_cli.ui.view_presentation.BackgroundLoadingStatus", ObservedStatus)
    view = ViewSpec(
        "root", "Results", (ViewItem("a", "Item", details=Inherit),) if with_items else (),
        state="loading", message="Finding songs", details=DetailLines(Blank, Blank, Blank),
    )
    session = ViewSession(view)
    presentation = ViewPresentation(session, FakePosterManager())
    assert "~ Finding songs" in strip_ansi(presentation.details(width=120))
    assert len(adapters) == 1
    token = adapters[0]._token
    assert token is not None
    session.apply(UpdateStatus("root", session.generation, "loading", "Checking songs"))
    assert "~ Checking songs" in strip_ansi(presentation.details(width=120))
    assert len(adapters) == 1
    assert adapters[0]._token == token
    session.apply(UpdateStatus("root", session.generation, "ready", "Done"))
    assert "Done" in strip_ansi(presentation.details(width=120))
    assert adapters[0]._token is None


def test_available_plain_mode_does_not_fetch_and_empty_details_stay_empty():
    presentation, _, manager = setup(ImageSpec("cover", "available", "https://example.test/a"))
    assert presentation.plain_details("a") == ["", "", "", ""]
    assert not manager.get_calls
    assert presentation.details("a", 80).split("\n")[:3] == [f"{color}{C_RESET}" for color in (C_TITLE, C_ALT, C_META)]


def test_resolution_completion_and_withdrawal_do_not_keep_ready_image():
    presentation, session, manager = setup(ImageSpec("cover", "resolving"))
    resolving = presentation.show_for()
    update_item(session, image=ImageSpec("cover", "available", "https://example.test/a"))
    manager.state = "ready"
    assert presentation.poster() == "POSTER"
    available = presentation.show_for()
    assert available is not resolving
    assert presentation.status_text() == ""
    update_item(session, image=ImageSpec("cover", "unavailable", "https://example.test/a"))
    assert presentation.show_for() is not available
    assert presentation.poster() == ""
    assert presentation.status_text() == "Cover unavailable"


def test_metadata_update_preserves_ready_state_and_duplicate_labels_have_distinct_objects():
    presentation, session, manager = setup(ImageSpec("cover", "available", "https://example.test/a"))
    manager.state = "ready"
    presentation.poster()
    original = presentation.show_for()
    update_item(session, details=DetailLines(Content("Updated"), Blank, Blank))
    assert presentation.show_for() is original
    assert presentation.show_for()["_poster_raw"] == "POSTER"
    duplicate = replace(session.current.items[0], id="b")
    session.apply(UpdateItems("root", session.generation, (*session.current.items, duplicate), replace_all=True))
    assert presentation.show_for("b") is not original
    session.position.selected_id = "b"
    assert presentation.selected_show_id == presentation.show_for("b")["_id"]


def test_disabled_available_image_never_calls_manager():
    presentation, _, manager = setup(ImageSpec("cover", "available", "https://example.test/a"))
    manager.active = False
    assert presentation.poster() == ""
    presentation.details(width=80)
    assert not presentation.needs_tick()
    assert not manager.get_calls and not manager.footer_calls


def test_explicit_action_bindings_replace_only_the_corresponding_default():
    presentation, _, _ = setup(actions=(ViewAction("quit", "quit", ("Esc",)),))
    footer = strip_ansi(presentation.details(width=100).split("\n")[3])
    assert footer == "Esc=quit"


def test_image_messages_are_plain_safe_and_keep_explicit_failure_label():
    presentation, _, _ = setup(ImageSpec("cover", "failed", message="\033[31mTimed out\033[0m\nRetry later"))
    assert presentation.status_text() == "Cover failed: Timed out Retry later"
    assert presentation.plain_details() == ["", "", "", "Cover failed: Timed out Retry later"]


def test_empty_view_renders_view_details_without_inventing_image_status():
    manager = FakePosterManager()
    session = ViewSession(ViewSpec("empty", "Empty", details=DetailLines(Content("Title"), Blank, Blank), state="empty", message="No results"))
    presentation = ViewPresentation(session, manager)
    assert presentation.selected() is None
    assert presentation.plain_details() == ["Title", "", "", "No results"]
    assert len(presentation.details(width=80).split("\n")) == 4
    assert presentation.poster() == ""
    assert not presentation.needs_tick()
    assert not manager.get_calls


@pytest.mark.parametrize("key,label", [
    ("ENTER", "Enter"), ("RIGHT", "Right"), ("ESC", "Esc"),
    ("LEFT", "Left"), ("CTRL_R", "Ctrl+R"), ("SHIFT_TAB", "Shift+Tab"),
    ("i", "i"), ("F1", "F1"), ("Enter", "Enter"),
])
def test_protocol_key_labels_are_human_readable(key, label):
    presentation, _, _ = setup(actions=(ViewAction("action", "Action", (key,)),))
    footer = strip_ansi(presentation.details(width=160).split("\n")[3])
    if key in {"LEFT", "RIGHT"}:
        assert f"{label}=Action" not in footer
    else:
        assert f"{label}=Action" in footer
    if key in {"LEFT", "ESC"}:
        assert f"{label}=back" not in footer


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("width", [8, 100])
def test_loading_root_message_animates_without_portrait(enabled, width):
    manager = FakePosterManager(enabled=enabled)
    session = ViewSession(ViewSpec("root", "Artist", state="loading", message="Fetching artist performances", details=DetailLines(Blank, Blank, Blank)))
    presentation = ViewPresentation(session, manager)
    lines = presentation.details(width=width).split("\n")
    assert len(lines) == 4
    assert strip_ansi(lines[3]).startswith("~ ")
    assert all(display_width(line) <= width - 1 for line in lines)
    assert lines[3].startswith("\033[38;5;244m")
    assert presentation.needs_tick()
    assert presentation.status_text() == "Fetching artist performances"
    assert presentation.plain_details()[-1] == "Fetching artist performances"
    assert not manager.get_calls
    session.apply(ReplaceView("root", session.generation, replace(session.current, state="empty", message="No performances")))
    assert "~" not in presentation.details(width=width)
    assert not presentation.needs_tick()


def test_loading_view_and_resolving_image_share_one_spinner():
    presentation, _, _ = setup(ImageSpec("cover", "resolving"), state="loading", message="Fetching performances")
    footer = strip_ansi(presentation.details(width=160).split("\n")[3])
    assert footer.count("~") == 1
    assert "~ Fetching performances" in footer
    assert "Fetching cover" in footer


def test_info_sanitizes_each_line_and_preserves_blank_lines_and_trailing_newline():
    presentation, session, manager = setup()
    update_item(session, info="\033[31mArtist\033[0m\n\n\033]0;Title\007Profile\ttext\r\n\033[2JCredits\x00\n")
    assert presentation.info() == "Artist\n\nProfile text\nCredits\n"
    assert not manager.get_calls


@pytest.mark.parametrize('status,label', [('loading', 'Loading cover'), ('failed', 'Cover failed')])
def test_real_poster_footer_keeps_old_status_until_image_is_ready(tmp_path, monkeypatch, status, label):
    presentation, _, _ = setup(ImageSpec('cover', 'available', 'https://example.test/a'))
    manager = PosterManager(
        enabled=lambda: True, cache_dir=lambda: str(tmp_path),
        hovered_show_id=lambda: presentation.selected_show_id,
        request_redraw=lambda: None, loading_frame=lambda: '~',
    )
    # Keep the real footer/timing logic, isolating image I/O from this test.
    monkeypatch.setattr(manager, 'get', lambda show: show.get('_poster_raw', ''))
    presentation.poster_manager = manager
    show = presentation.show_for()
    manager.set_status(show, status)
    show['_poster_status_time'] = time.time() - 60
    assert label not in strip_ansi(manager.footer_line(show, 'Navigation', 120))
    assert label in strip_ansi(presentation.details(width=120))
    assert presentation.needs_tick() == (status == 'loading')
    show['_poster_raw'] = 'READY'
    manager.set_status(show, 'ready')
    assert label not in strip_ansi(presentation.details(width=120))
    assert not presentation.needs_tick()


@pytest.mark.parametrize("enabled", [True, False])
def test_pending_navigation_keeps_details_and_poster_and_animates_status(enabled):
    from media_cli.ui.view_session import BeginNavigation

    presentation, session, manager = setup(
        ImageSpec("cover", "available", "https://example.test/a"),
        details=DetailLines(Content("Selected title"), Content("Selected artist"), Content("Selected metadata")),
        message="Source status",
    )
    manager.active = enabled
    manager.state = "ready"
    show = presentation.show_for()
    session.apply(BeginNavigation("root", session.generation, "request", "Fetching destination"))
    assert presentation.status_text() == "Fetching destination"
    assert presentation.needs_tick()
    assert presentation.plain_details()[:3] == ["Selected title", "Selected artist", "Selected metadata"]
    assert "~ Fetching destination" in strip_ansi(presentation.details(width=160))
    assert presentation.show_for() is show
    assert presentation.poster() == ("POSTER" if enabled else "")


def test_partial_inheritance_and_whole_blank_use_the_same_resolution_in_every_output():
    presentation, session, _ = setup(details=DetailLines(Content("Screen"), Content("Artist"), Content("Metadata")))
    update_item(session, details=DetailLines(Content("Item"), Inherit, Blank))
    assert presentation.plain_details()[:3] == ["Item", "Artist", ""]
    assert presentation.info() == "Item\nArtist\n"
    assert strip_ansi(presentation.details()).split("\n")[:3] == ["Item", "Artist", ""]
    update_item(session, details=Blank)
    assert presentation.plain_details()[:3] == ["", "", ""]
    assert presentation.info() == "\n\n"
    assert strip_ansi(presentation.details()).split("\n")[:3] == ["", "", ""]
