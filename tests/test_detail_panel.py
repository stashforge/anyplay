"""The isolated detail panel styles resolved content without screen knowledge."""

import importlib

import pytest

from media_cli.core.terminal import display_width, sanitize_terminal_text, strip_ansi
from media_cli.ui.footer import Footer, FooterStatus, StatusKind, render_footer
from media_cli.ui.view_model import Blank, Content, DetailLines, Inherit, resolve_details


def render(**overrides):
    presenter = importlib.import_module("media_cli.ui.detail_panel")
    fields = dict(primary="Primary", secondary="Secondary", details="Details", footer=Footer(), width=80)
    fields.update(overrides)
    return presenter.render_detail_panel(**fields)


def test_component_exists():
    assert importlib.util.find_spec("media_cli.ui.detail_panel") is not None


def test_four_lines_keep_existing_colors_and_delegate_footer():
    footer = Footer(context=("Context",), actions=("Enter=open",))
    lines = render(footer=footer)
    assert isinstance(lines, tuple)
    assert lines == (
        "\033[1;97mPrimary\033[0m",
        "\033[38;5;248mSecondary\033[0m",
        "\033[38;5;248mDetails\033[0m",
        render_footer(footer, 80),
    )


def test_existing_resolution_supplies_common_overrides_and_deliberate_blanks():
    common = DetailLines(Content("Common"), Content("Supporting"), Content("Attributes"))
    item = DetailLines(Inherit, Blank, Content("Override"))
    resolved = resolve_details(common, item)
    lines = render(primary=resolved.title, secondary=resolved.secondary, details=resolved.metadata)
    assert tuple(strip_ansi(line) for line in lines) == ("Common", "", "Override", "")


@pytest.mark.parametrize("width", [1, 2, 5, 12, 80])
def test_every_line_respects_display_width_including_wide_and_combining_text(width):
    text = "\u754c\u754ce\u0301 long content"
    footer = Footer(context=(text,))
    lines = render(primary=text, secondary=text, details=text, footer=footer, width=width)
    assert len(lines) == 4
    assert all(display_width(line) <= max(1, width - 1) for line in lines)


def test_all_resolved_text_is_sanitized_without_interpreting_domain_content():
    text = "\033[31mName\033[0m\nnext\tpart\033]0;title\007"
    lines = render(primary=text, secondary=text, details=text)
    assert tuple(strip_ansi(line) for line in lines[:3]) == (sanitize_terminal_text(text),) * 3


def test_loading_footer_does_not_change_other_line_colors_or_muted_context():
    footer = Footer(statuses=(FooterStatus("Loading", StatusKind.LOADING, "*"),), context=("Context",))
    lines = render(footer=footer)
    assert lines[:3] == render()[:3]
    assert lines[3] == render_footer(footer, 80)
    assert "\033[36m* Loading\033[0m" in lines[3]
    assert "\033[38;5;244mContext\033[0m" in lines[3]


@pytest.mark.parametrize("field", ["primary", "secondary", "details", "footer", "width"])
def test_every_field_is_required(field):
    presenter = importlib.import_module("media_cli.ui.detail_panel")
    fields = dict(primary="Primary", secondary="Secondary", details="Details", footer=Footer(), width=80)
    del fields[field]
    with pytest.raises(TypeError):
        presenter.render_detail_panel(**fields)


@pytest.mark.parametrize("field", ["primary", "secondary", "details"])
@pytest.mark.parametrize("value", [None, 0, Content("Unresolved"), Inherit, Blank])
def test_unresolved_or_wrong_content_is_rejected(field, value):
    with pytest.raises(TypeError, match=field):
        render(**{field: value})


@pytest.mark.parametrize("value", [None, "Footer", ("Footer",)])
def test_footer_requires_existing_semantic_footer(value):
    with pytest.raises(TypeError, match="footer"):
        render(footer=value)


@pytest.mark.parametrize("value", [None, True, "80", 2.5])
def test_width_requires_integer_columns(value):
    with pytest.raises(TypeError, match="width"):
        render(width=value)


@pytest.mark.parametrize("width", [0, -1])
def test_width_requires_positive_columns(width):
    with pytest.raises(ValueError, match="width"):
        render(width=width)
