"""Runtime contract for shared picker position state."""

from types import SimpleNamespace

import pytest

from media_cli.ui import picker, plain_picker
from media_cli.ui.picker_state import PickerState


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query", None),
        ("query", 1),
        ("selected_index", True),
        ("selected_index", 1.0),
        ("scroll", False),
        ("scroll", "1"),
        ("marked_indices", [1]),
        ("marked_indices", {True}),
        ("marked_indices", {1.0}),
        ("query_cursor", True),
        ("query_cursor", "1"),
        ("cursor_activity", "active"),
    ],
)
def test_picker_state_rejects_malformed_types(field: str, value: object) -> None:
    with pytest.raises(TypeError):
        PickerState(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("selected_index", -1),
        ("scroll", -1),
        ("marked_indices", {-1}),
        ("query_cursor", -1),
    ],
)
def test_picker_state_rejects_negative_positions(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        PickerState(**{field: value})


def test_picker_state_copies_supplied_marks_and_remains_mutable() -> None:
    marks = {1, 3}
    state = PickerState(marked_indices=marks)

    marks.add(5)
    assert state.marked_indices == {1, 3}
    assert state.marked_indices is not marks

    state.query = "updated"
    state.selected_index = 2
    state.scroll = 4
    state.marked_indices.add(6)
    state.validate()

    assert (state.query, state.selected_index, state.scroll) == ("updated", 2, 4)
    assert state.marked_indices == {1, 3, 6}
    assert marks == {1, 3, 5}


def test_validate_rejects_invalid_mutated_state() -> None:
    state = PickerState()
    state.selected_index = -1

    with pytest.raises(ValueError):
        state.validate()


@pytest.mark.parametrize("entrypoint", ["plain", "tui-fallback"])
@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("query", None, TypeError),
        ("selected_index", -1, ValueError),
        ("scroll", True, TypeError),
        ("marked_indices", {-1}, ValueError),
    ],
)
def test_picker_entrypoints_validate_mutated_state_before_io(
    monkeypatch: pytest.MonkeyPatch, entrypoint: str, field: str, value: object, error: type[Exception]
) -> None:
    state = PickerState()
    setattr(state, field, value)
    calls: list[str] = []

    if entrypoint == "plain":

        def invoke() -> object:
            return plain_picker.plain_pick(
                None,
                None,
                "Pick",
                ["One"],
                state=state,
                input_fn=lambda _prompt: calls.append("input") or "1",
                output_fn=lambda _text: calls.append("output"),
            )
    else:
        monkeypatch.setattr(plain_picker, "plain_pick", lambda *args, **kwargs: calls.append("fallback"))

        def invoke() -> object:
            return picker.tui_pick(SimpleNamespace(plain_mode=True), SimpleNamespace(), "Pick", ["One"], state=state)

    with pytest.raises(error):
        invoke()
    assert calls == []


@pytest.mark.parametrize("entrypoint", ["plain", "tui-fallback"])
def test_picker_entrypoints_reject_wrong_state_object(monkeypatch: pytest.MonkeyPatch, entrypoint: str) -> None:
    calls: list[str] = []
    if entrypoint == "plain":

        def invoke() -> object:
            return plain_picker.plain_pick(
                None,
                None,
                "Pick",
                ["One"],
                state=object(),
                input_fn=lambda _prompt: calls.append("input") or "1",
                output_fn=lambda _text: calls.append("output"),
            )
    else:
        monkeypatch.setattr(plain_picker, "plain_pick", lambda *args, **kwargs: calls.append("fallback"))

        def invoke() -> object:
            return picker.tui_pick(SimpleNamespace(plain_mode=True), SimpleNamespace(), "Pick", ["One"], state=object())

    with pytest.raises(TypeError, match="state must be a PickerState"):
        invoke()
    assert calls == []


def test_tui_boundary_preserves_valid_positive_out_of_range_position(monkeypatch: pytest.MonkeyPatch) -> None:
    state = PickerState(selected_index=99, scroll=101, marked_indices={99})
    monkeypatch.setattr(plain_picker, "plain_pick", lambda *args, **kwargs: -3)

    assert picker.tui_pick(SimpleNamespace(plain_mode=True), SimpleNamespace(), "Pick", ["One"], state=state) == -3
    assert (state.selected_index, state.scroll, state.marked_indices) == (99, 101, {99})
