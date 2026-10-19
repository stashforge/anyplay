"""Indentation belongs to row types, never individual item records."""

import importlib
from dataclasses import FrozenInstanceError

import pytest

from media_cli.ui.view_model import Inherit, ViewItem


def component():
    return importlib.import_module("media_cli.ui.row_layout")


def test_component_exists():
    assert importlib.util.find_spec("media_cli.ui.row_layout") is not None


@pytest.mark.parametrize(
    "kind,enabled,expected",
    [
        ("item", True, "OPTION"),
        ("submenu", True, "OPTION"),
        ("item", False, "DISABLED"),
        ("submenu", False, "DISABLED"),
        ("heading", True, "HEADING"),
        ("heading", False, "HEADING"),
    ],
)
def test_row_type_uses_existing_item_semantics(kind, enabled, expected):
    module = component()
    item = ViewItem("id", "Label", kind=kind, enabled=enabled, details=Inherit)
    assert module.row_kind(item) is getattr(module.RowKind, expected)


def test_all_rows_of_each_type_share_the_same_indentation():
    module = component()
    layout = module.RowIndentation(option=1, disabled=4, heading=0)
    items = (
        ViewItem("a", "Option A", details=Inherit),
        ViewItem("b", "Option B", details=Inherit),
        ViewItem("c", "Disabled A", enabled=False, details=Inherit),
        ViewItem("d", "Disabled B", kind="submenu", enabled=False, details=Inherit),
        ViewItem("e", "Heading A", kind="heading", details=Inherit),
        ViewItem("f", "Heading B", kind="heading", details=Inherit),
    )
    assert tuple(layout.columns_for(module.row_kind(item)) for item in items) == (1, 1, 4, 4, 0, 0)
    assert not hasattr(items[0], "indent")


def test_default_indentation_does_not_change_existing_marker_gutter():
    module = component()
    assert all(module.DEFAULT_ROW_INDENTATION.columns_for(kind) == 0 for kind in module.RowKind)


def test_settings_are_explicit_and_immutable():
    module = component()
    with pytest.raises(TypeError):
        module.RowIndentation()
    layout = module.RowIndentation(option=1, disabled=2, heading=3)
    with pytest.raises(FrozenInstanceError):
        layout.option = 8


@pytest.mark.parametrize("field", ["option", "disabled", "heading"])
@pytest.mark.parametrize("value", [None, True, "2", 1.5, -1])
def test_invalid_indentation_fails_at_the_boundary(field, value):
    fields = dict(option=0, disabled=0, heading=0)
    fields[field] = value
    with pytest.raises((TypeError, ValueError), match=field):
        component().RowIndentation(**fields)


def test_wrong_item_and_row_kind_types_are_rejected():
    module = component()
    with pytest.raises(TypeError, match="ViewItem"):
        module.row_kind({"kind": "heading"})
    with pytest.raises(TypeError, match="RowKind"):
        module.DEFAULT_ROW_INDENTATION.columns_for("option")
