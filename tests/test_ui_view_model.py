from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType

import pytest

from media_cli.ui import view_model as vm
from media_cli.ui.view_model import (
    Blank,
    Content,
    DetailLines,
    ImageSpec,
    Inherit,
    ViewItem,
    ViewSpec,
    resolve_details,
    selectable_items,
    visible_items,
)


def test_details_fields_are_required_and_reject_raw_strings():
    with pytest.raises(TypeError):
        DetailLines()
    with pytest.raises(TypeError, match="title"):
        DetailLines("Title", "Secondary", "Metadata")


def test_content_is_immutable_and_formats_semantic_parts():
    content = vm.Content("Songs", vm.LabelValue("Artist", "Example"), vm.Count(2, "theme"))
    assert content.parts == ("Songs", vm.LabelValue("Artist", "Example"), vm.Count(2, "theme"))
    assert vm.render_content(content) == "Songs • Artist: Example • 2 themes"
    assert vm.render_content(vm.Content(vm.Count(0, "theme"), vm.Count(1, "theme"))) == "0 themes • 1 theme"
    with pytest.raises(FrozenInstanceError):
        content.parts = ("Changed",)


def test_action_keys_are_an_immutable_snapshot():
    keys = ["ENTER", "RIGHT"]
    action = vm.ViewAction("select", "open", keys)
    keys.append("CTRL_O")
    assert action.keys == ("ENTER", "RIGHT")


@pytest.mark.parametrize("parts", [(), ("",), ("  ",), (None,), (2,), (["Title"],)])
def test_content_rejects_missing_blank_and_invalid_parts(parts):
    with pytest.raises((TypeError, ValueError), match="Content"):
        vm.Content(*parts)


@pytest.mark.parametrize("value", [None, "", " ", 3, "Inherit", "Blank"])
def test_detail_lines_reject_untyped_fields(value):
    with pytest.raises(TypeError, match="secondary"):
        DetailLines(vm.Content("Title"), value, vm.Blank)


def test_required_details_are_keyword_only_for_screens_and_items():
    with pytest.raises(TypeError, match="details"):
        ViewSpec("screen", "Prompt")
    with pytest.raises(TypeError, match="details"):
        ViewItem("item", "Label")
    assert ViewSpec.__dataclass_fields__["details"].kw_only
    assert ViewItem.__dataclass_fields__["details"].kw_only


def test_resolution_inherits_per_field_and_distinguishes_intentional_blank():
    common = DetailLines(vm.Content("Screen"), vm.Content("Common secondary"), vm.Blank)
    partial = DetailLines(vm.Content("Item"), vm.Inherit, vm.Content(vm.Count(1, "theme")))
    resolved = vm.resolve_details(common, partial)
    assert (resolved.title, resolved.secondary, resolved.metadata) == ("Item", "Common secondary", "1 theme")
    assert vm.resolve_details(common, vm.Inherit) == vm.resolve_details(common)
    blank = vm.resolve_details(common, vm.Blank)
    assert (blank.title, blank.secondary, blank.metadata) == ("", "", "")
    assert vm.Inherit is not vm.Blank
    assert not isinstance(vm.Inherit, str)
    assert not isinstance(vm.Blank, str)


def test_common_details_cannot_inherit_even_when_item_overrides_them():
    common = DetailLines(vm.Content("Title"), vm.Inherit, vm.Blank)
    with pytest.raises(ValueError, match="secondary.*Inherit"):
        vm.resolve_details(common, vm.Blank)
    with pytest.raises(ValueError, match="screen.*secondary"):
        ViewSpec("screen", "Prompt", details=common)


def test_screen_and_item_errors_identify_the_owner():
    common = DetailLines(vm.Content("Title"), vm.Blank, vm.Blank)
    with pytest.raises(TypeError, match="screen.*details"):
        ViewSpec("screen", "Prompt", details=vm.Inherit)
    with pytest.raises(TypeError, match="item.*details"):
        ViewItem("item", "Label", details=None)
    with pytest.raises(ValueError, match="item.*kind"):
        ViewItem("item", "Label", details=vm.Inherit, kind="unknown")
    with pytest.raises(ValueError, match="screen.*state"):
        ViewSpec("screen", "Prompt", details=common, state="unknown")
    with pytest.raises(ValueError, match="cover.*state"):
        ImageSpec("cover", state="unknown")


def test_replacing_screen_details_revalidates_inherited_fields():
    common = DetailLines(vm.Content("Title"), vm.Blank, vm.Blank)
    view = ViewSpec("screen", "Prompt", (ViewItem("row", "Row", details=vm.Inherit),), details=common)
    with pytest.raises(ValueError, match="screen.*title"):
        replace(view, details=DetailLines(vm.Inherit, vm.Blank, vm.Blank))
    assert vm.resolve_details(view.details, view.items[0].details).title == "Title"


@pytest.mark.parametrize("count", [-1, True, 1.5, "2"])
def test_counts_reject_invalid_values(count):
    with pytest.raises((TypeError, ValueError), match="Count"):
        vm.Count(count, "theme")


@pytest.mark.parametrize("label,value", [("", "Name"), ("Artist", ""), ("Artist", None)])
def test_label_value_requires_nonblank_strings(label, value):
    with pytest.raises((TypeError, ValueError), match="LabelValue"):
        vm.LabelValue(label, value)


def test_duplicate_labels_have_distinct_selection_ids():
    view = ViewSpec(
        "songs",
        "Select song",
        (ViewItem("a", "Same", details=Inherit), ViewItem("b", "Same", details=Inherit)),
        details=DetailLines(Blank, Blank, Blank),
    )
    assert [i.id for i in selectable_items(view.items)] == ["a", "b"]


def test_duplicate_ids_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        ViewSpec(
            "songs",
            "Songs",
            (ViewItem("a", "One", details=Inherit), ViewItem("a", "Two", details=Inherit)),
            details=DetailLines(Blank, Blank, Blank),
        )


def test_invalid_section_reference_is_rejected():
    with pytest.raises(ValueError, match="section"):
        ViewSpec(
            "songs",
            "Songs",
            (ViewItem("a", "One", section_id="absent", details=Inherit),),
            details=DetailLines(Blank, Blank, Blank),
        )


def test_heading_only_match_does_not_make_an_orphan():
    view = ViewSpec(
        "songs",
        "Songs",
        (
            ViewItem("g", "Opening Themes", kind="heading", details=Inherit),
            ViewItem("a", "Idol", section_id="g", details=Inherit),
            ViewItem("h", "Endings", kind="heading", details=Inherit),
            ViewItem("b", "Mephisto", section_id="h", details=Inherit),
        ),
        details=DetailLines(Blank, Blank, Blank),
    )
    assert [i.id for i in visible_items(view, "idl")] == ["g", "a"]
    assert visible_items(view, "Opening") == ()
    assert [i.id for i in selectable_items(view.items)] == ["a", "b"]


def test_disabled_actions_and_headings_are_not_selectable():
    items = (
        ViewItem("g", "Heading", kind="heading", details=Inherit),
        ViewItem("a", "Play", enabled=False, details=Inherit),
        ViewItem("b", "Open", kind="submenu", details=Inherit),
    )
    assert [i.id for i in selectable_items(items)] == ["b"]


def test_empty_view_and_pending_cover_have_explicit_states():
    view = ViewSpec(
        "songs", "Songs", state="loading", message="Fetching songs", details=DetailLines(Blank, Blank, Blank)
    )
    assert not view.items
    assert visible_items(view, "anything") == ()
    assert ImageSpec("cover", state="resolving").url is None
    assert resolve_details(DetailLines(Content("Title"), Blank, Blank)).secondary == ""


@pytest.mark.parametrize("value", [None, 7, True, [], "", " \t"])
@pytest.mark.parametrize(
    "model,field",
    [
        (ImageSpec("cover"), "key"),
        (ViewItem("row", "", details=Inherit), "id"),
        (vm.ViewTab("tab", ""), "id"),
        (vm.ViewAction("action", ""), "id"),
        (ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank)), "id"),
        (ViewItem("row", "", details=Inherit), "section_id"),
        (ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank)), "active_tab_id"),
    ],
)
def test_ids_require_nonblank_strings(model, field, value):
    if value is None and field in ("section_id", "active_tab_id"):
        return
    with pytest.raises(ValueError if isinstance(value, str) else TypeError, match=field):
        replace(model, **{field: value})


@pytest.mark.parametrize("value", [None, 7, True, []])
@pytest.mark.parametrize(
    "model,field",
    [
        (ViewItem("row", "", details=Inherit), "label"),
        (ViewItem("row", "", details=Inherit), "hint"),
        (ViewItem("row", "", details=Inherit), "prefix"),
        (ViewItem("row", "", details=Inherit), "info"),
        (vm.ViewTab("tab", ""), "label"),
        (vm.ViewAction("action", ""), "label"),
        (ImageSpec("cover"), "message"),
        (ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank)), "prompt"),
        (ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank)), "message"),
        (vm.ResolvedDetails("", "", ""), "title"),
        (vm.ResolvedDetails("", "", ""), "secondary"),
        (vm.ResolvedDetails("", "", ""), "metadata"),
    ],
)
def test_display_fields_require_strings(model, field, value):
    with pytest.raises(TypeError, match=field):
        replace(model, **{field: value})


@pytest.mark.parametrize("value", [None, 0, 1, "false", []])
@pytest.mark.parametrize(
    "model,field",
    [
        (ViewItem("row", "", details=Inherit), "enabled"),
        (vm.SelectionPolicy(), "multiple"),
        (vm.SelectionPolicy(), "auto_single"),
        (vm.SelectionPolicy(), "query_input"),
        (ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank)), "reverse_items"),
    ],
)
def test_flags_require_booleans(model, field, value):
    with pytest.raises(TypeError, match=field):
        replace(model, **{field: value})


@pytest.mark.parametrize("value", [None, True, False, 1.5, "1", -1])
def test_revision_requires_a_nonnegative_integer(value):
    with pytest.raises(ValueError if type(value) is int else TypeError, match="revision"):
        ViewSpec("screen", "", revision=value, details=DetailLines(Blank, Blank, Blank))


@pytest.mark.parametrize(
    "field,value",
    [
        ("image", {}),
        ("image", "cover"),
        ("items", ["row"]),
        ("items", [vm.ViewTab("tab", "")]),
        ("tabs", ["tab"]),
        ("tabs", [ViewItem("row", "", details=Inherit)]),
        ("actions", ["action"]),
        ("actions", [vm.ViewTab("tab", "")]),
        ("context", [None]),
        ("context", [1]),
        ("context", "text"),
        ("items", ""),
        ("tabs", ""),
        ("actions", ""),
        ("selection_policy", {}),
        ("selection_policy", None),
        ("message_kind", "info"),
    ],
)
def test_view_rejects_malformed_nested_fields(field, value):
    view = ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank))
    with pytest.raises(TypeError, match=field):
        replace(view, **{field: value})


@pytest.mark.parametrize("value", ["cover", {}, False])
def test_item_rejects_malformed_image(value):
    with pytest.raises(TypeError, match="image"):
        ViewItem("row", "", image=value, details=Inherit)


@pytest.mark.parametrize("value", [True, 1, [], {}])
def test_image_url_requires_a_string_or_none(value):
    with pytest.raises(TypeError, match="url"):
        ImageSpec("cover", url=value)


@pytest.mark.parametrize("value", [None, "", " ", "\t\n"])
def test_available_image_requires_a_nonblank_url(value):
    with pytest.raises(ValueError, match="URL"):
        ImageSpec("cover", "available", value)


@pytest.mark.parametrize("value", ["opaque-image-reference", " image-reference "])
def test_available_image_url_is_not_parsed_or_rewritten(value):
    assert ImageSpec("cover", "available", value).url == value


@pytest.mark.parametrize("state", ["resolving", "unavailable", "failed"])
def test_nonavailable_images_can_have_blank_urls(state):
    assert ImageSpec("cover", state, " ").url == " "


@pytest.mark.parametrize("value", [None, "ENTER", [None], [7]])
def test_action_keys_require_a_collection_of_strings(value):
    with pytest.raises(TypeError, match="keys"):
        vm.ViewAction("open", "", value)


@pytest.mark.parametrize("field", ["items", "tabs", "actions", "context", "keys"])
@pytest.mark.parametrize("value", [{}, bytearray(), MappingProxyType({})])
def test_snapshot_fields_reject_empty_mappings_and_bytearrays(field, value):
    with pytest.raises(TypeError, match=field):
        if field == "keys":
            vm.ViewAction("open", "", value)
        else:
            ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank), **{field: value})


@pytest.mark.parametrize(
    "container", [list, tuple, lambda values: (value for value in values)], ids=["list", "tuple", "generator"]
)
@pytest.mark.parametrize(
    "field,values",
    [
        ("items", (ViewItem("row", "", details=Inherit),)),
        ("tabs", (vm.ViewTab("tab", ""),)),
        ("actions", (vm.ViewAction("open", "", ("ENTER",)),)),
        ("context", ("", "context")),
        ("keys", ("ENTER", "RIGHT")),
    ],
)
def test_snapshot_fields_preserve_legitimate_iterable_inputs(field, values, container):
    source = container(values)
    if field == "keys":
        model = vm.ViewAction("open", "", source)
    else:
        model = ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank), **{field: source})
    assert getattr(model, field) == values
    assert isinstance(getattr(model, field), tuple)


@pytest.mark.parametrize(
    "model,field",
    [
        (ImageSpec("cover"), "state"),
        (ViewItem("row", "", details=Inherit), "kind"),
        (ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank)), "state"),
    ],
)
@pytest.mark.parametrize("value", [None, 1, [], "unknown"])
def test_literal_choices_are_validated_at_runtime(model, field, value):
    with pytest.raises(ValueError if isinstance(value, str) else TypeError, match=field):
        replace(model, **{field: value})


@pytest.mark.parametrize(
    "field,values",
    [
        ("tabs", [vm.ViewTab("same", "One"), vm.ViewTab("same", "Two")]),
        ("actions", [vm.ViewAction("same", "One"), vm.ViewAction("same", "Two")]),
    ],
)
def test_duplicate_tab_ids_and_unbound_action_ids_are_rejected(field, values):
    with pytest.raises(ValueError, match="Duplicate.*IDs"):
        ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank), **{field: values})


def test_repeated_commands_keep_distinct_labels_and_disjoint_display_keys():
    actions = [vm.ViewAction("back", "search", ["LEFT"]), vm.ViewAction("back", "back", ["ESC"])]
    view = ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank), actions=actions)
    assert [(action.id, action.label, action.keys) for action in view.actions] == [
        ("back", "search", ("LEFT",)),
        ("back", "back", ("ESC",)),
    ]


@pytest.mark.parametrize(
    "first,second",
    [
        ((), ("LEFT",)),
        (("LEFT",), ()),
        (("LEFT",), ("LEFT",)),
        (("Left", "ESC"), ("left",)),
        (("SHIFT_TAB",), ("btab",)),
    ],
)
def test_repeated_commands_require_nonempty_disjoint_key_groups(first, second):
    with pytest.raises(ValueError, match="back"):
        ViewSpec(
            "screen",
            "",
            details=DetailLines(Blank, Blank, Blank),
            actions=(
                vm.ViewAction("back", "search", first),
                vm.ViewAction("back", "back", second),
            ),
        )


@pytest.mark.parametrize(
    "first,second",
    [
        (("ENTER",), ("ENTER",)),
        (("Enter", "RIGHT"), ("enter",)),
        (("shift_tab",), ("BTAB",)),
        (("btab",), ("SHIFT_TAB",)),
    ],
)
def test_keys_cannot_be_bound_to_different_commands(first, second):
    with pytest.raises(ValueError, match="key"):
        ViewSpec(
            "screen",
            "",
            details=DetailLines(Blank, Blank, Blank),
            actions=(
                vm.ViewAction("select", "open", first),
                vm.ViewAction("back", "back", second),
            ),
        )


def test_key_normalization_preserves_stored_spelling_and_unambiguous_groups():
    actions = (
        vm.ViewAction("tab", "switch", ("shift_tab", "BTAB")),
        vm.ViewAction("back", "search", ("Left",)),
        vm.ViewAction("back", "back", ("esc",)),
        vm.ViewAction("help", "help"),
    )
    view = ViewSpec("screen", "", details=DetailLines(Blank, Blank, Blank), actions=actions)
    assert view.actions == actions
    assert [action.keys for action in view.actions] == [("shift_tab", "BTAB"), ("Left",), ("esc",), ()]


def test_repeated_command_checks_every_previous_key_group():
    with pytest.raises(ValueError, match="key"):
        ViewSpec(
            "screen",
            "",
            details=DetailLines(Blank, Blank, Blank),
            actions=(
                vm.ViewAction("back", "search", ("LEFT",)),
                vm.ViewAction("back", "back", ("ESC",)),
                vm.ViewAction("back", "return", ("left",)),
            ),
        )


@pytest.mark.parametrize("key", ["", " ", "\t\n"])
def test_action_keys_cannot_be_blank(key):
    with pytest.raises(ValueError, match="keys"):
        vm.ViewAction("back", "back", ("ESC", key))


def test_sequences_snapshot_lists_and_keep_opaque_references():
    payload = {"business": [1, 2]}
    item = ViewItem("row", "", details=Inherit, reference=payload)
    items = [item]
    tabs = [vm.ViewTab("tab", "")]
    actions = [vm.ViewAction("open", "", ["ENTER"])]
    context = ["", " "]
    view = ViewSpec(
        "screen",
        "",
        items,
        details=DetailLines(Blank, Blank, Blank),
        tabs=tabs,
        actions=actions,
        context=context,
        active_tab_id="tab",
    )
    for source in (items, tabs, actions, context):
        source.clear()
    assert view.items == (item,)
    assert view.tabs == (vm.ViewTab("tab", ""),)
    assert view.actions == (vm.ViewAction("open", "", ("ENTER",)),)
    assert view.context == ("", " ")
    assert item.reference is payload
    assert item == replace(item, reference=object())
    with pytest.raises(FrozenInstanceError):
        view.context = ()


@pytest.mark.parametrize("state", ["ready", "loading", "empty", "failed"])
@pytest.mark.parametrize("with_items", [False, True])
def test_state_does_not_constrain_item_count(state, with_items):
    items = [ViewItem("row", " ", details=Blank)] if with_items else []
    view = ViewSpec("screen", " ", items, state=state, revision=1, details=DetailLines(Blank, Blank, Blank))
    assert view.state == state
    assert len(view.items) == int(with_items)


def test_public_view_state_alias_and_constructor_order():
    from inspect import signature
    from typing import get_args

    assert get_args(vm.ViewState) == ("ready", "loading", "empty", "failed")
    assert tuple(signature(ViewItem).parameters) == (
        "id",
        "label",
        "kind",
        "enabled",
        "hint",
        "prefix",
        "section_id",
        "image",
        "info",
        "reference",
        "details",
        "adjustable",
        "action_hints",
        "footer_context",
    )
    assert tuple(signature(ViewSpec).parameters) == (
        "id",
        "prompt",
        "items",
        "image",
        "tabs",
        "active_tab_id",
        "actions",
        "context",
        "state",
        "message",
        "selection_policy",
        "reverse_items",
        "revision",
        "message_kind",
        "details",
        "row_indentation", "help", "footer", "footer_statuses", "total_count",
    )
