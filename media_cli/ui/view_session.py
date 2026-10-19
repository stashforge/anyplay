"""Navigation and asynchronous presentation updates, independent of rendering."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import count
from threading import Lock
from typing import Literal, TypeAlias

from .actions import ActionBindings, ResolvedAction
from .footer import StatusKind
from .loading_lifecycle import LoadingLifecycle
from .loading_model import (
    CancelLoading,
    FailLoading,
    FinishLoading,
    LoadingInteraction,
    LoadingOwner,
    LoadingPlacement,
    LoadingSnapshot,
    LoadingTaskSpec,
    LoadingToken,
    _duration,
)
from .message_choices import MessageInput, normalize_messages
from .picker_state import PickerState
from .view_model import ImageSpec, ViewItem, ViewSpec, ViewState, selectable_items, visible_items

_LOG = logging.getLogger(__name__)
EventKind: TypeAlias = Literal["select", "focus", "query", "tab", "back", "quit", "action"]
EventValue: TypeAlias = str | tuple[str, ...] | None


def _identifier(value: object, owner: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{owner} requires a string")
    if not value.strip():
        raise ValueError(f"{owner} requires a nonblank string")


def _integer(value: object, owner: str, minimum: int = 0) -> None:
    if type(value) is not int:
        raise TypeError(f"{owner} requires an integer")
    if value < minimum:
        raise ValueError(f"{owner} must be at least {minimum}")


def _address(view_id: str, generation: int) -> None:
    _identifier(view_id, "view_id")
    _integer(generation, "generation", 1)


def _boolean(value: object, owner: str) -> None:
    if type(value) is not bool:
        raise TypeError(f"{owner} requires a boolean")


def _view(value: object) -> None:
    if not isinstance(value, ViewSpec):
        raise TypeError("view requires ViewSpec")


def _record_collection(value: object, owner: str) -> None:
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(value, Iterable):
        raise TypeError(f"{owner} requires a collection of records")


@dataclass
class ViewPosition:
    selected_id: str | None = None
    query: str = ""
    scroll: int = 0
    active_tab_id: str | None = None
    marked_ids: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.validate()
        self.marked_ids = set(self.marked_ids)

    def validate(self) -> None:
        if not isinstance(self.query, str):
            raise TypeError("position.query requires a string")
        _integer(self.scroll, "position.scroll")
        for name, value in (("selected_id", self.selected_id), ("active_tab_id", self.active_tab_id)):
            if value is not None:
                _identifier(value, f"position.{name}")
        if not isinstance(self.marked_ids, set):
            raise TypeError("position.marked_ids requires a set")
        for item_id in self.marked_ids:
            _identifier(item_id, "position.marked_ids")


@dataclass(frozen=True)
class UiEvent:
    kind: EventKind
    view_id: str
    generation: int
    item_id: str | None = None
    value: EventValue = None

    def __post_init__(self) -> None:
        _address(self.view_id, self.generation)
        if self.kind not in {"select", "focus", "query", "tab", "back", "quit", "action"}:
            raise ValueError(f"Unknown UI event kind: {self.kind!r}")
        if self.item_id is not None:
            _identifier(self.item_id, "event.item_id")
        if self.kind == "query":
            if not isinstance(self.value, str):
                raise TypeError("query event value requires a string")
        elif self.kind in {"tab", "action"}:
            _identifier(self.value, f"{self.kind} event value")
        elif self.kind == "select" and self.value is not None:
            if not isinstance(self.value, (tuple, list, set, frozenset)):
                raise TypeError("select event value requires a collection of IDs")
            for item_id in self.value:
                _identifier(item_id, "select event value")
            object.__setattr__(self, "value", tuple(self.value))
        elif self.value is not None:
            raise TypeError(f"{self.kind} event does not accept a value")


@dataclass(frozen=True)
class PushView:
    view: ViewSpec

    def __post_init__(self) -> None:
        _view(self.view)


@dataclass(frozen=True)
class BeginNavigation:
    view_id: str
    generation: int
    request_id: str
    message: MessageInput
    item_id: str | None = None

    def __post_init__(self) -> None:
        _address(self.view_id, self.generation)
        _identifier(self.request_id, "request_id")
        choices = normalize_messages(self.message, field="navigation.message")
        object.__setattr__(self, "message", choices[0] if isinstance(self.message, str) else choices)
        if self.item_id is not None:
            _identifier(self.item_id, "navigation.item_id")


@dataclass(frozen=True)
class CompleteNavigation:
    view_id: str
    generation: int
    request_id: str
    view: ViewSpec
    replace_current: bool = False

    def __post_init__(self) -> None:
        _address(self.view_id, self.generation)
        _identifier(self.request_id, "request_id")
        _view(self.view)
        _boolean(self.replace_current, "replace_current")


@dataclass(frozen=True)
class FailNavigation:
    view_id: str
    generation: int
    request_id: str
    message: str

    def __post_init__(self) -> None:
        _address(self.view_id, self.generation)
        _identifier(self.request_id, "request_id")
        _identifier(self.message, "navigation.message")
        normalize_messages(self.message, field="navigation.message")


@dataclass(frozen=True)
class ReplaceView:
    view_id: str
    generation: int
    view: ViewSpec
    new_activation: bool = False

    def __post_init__(self) -> None:
        _address(self.view_id, self.generation)
        _view(self.view)
        _boolean(self.new_activation, "new_activation")


@dataclass(frozen=True)
class PopView:
    pass


@dataclass(frozen=True)
class UpdateItems:
    view_id: str
    generation: int
    items: tuple[ViewItem, ...]
    replace_all: bool = False
    revision: int | None = None
    expected_images: tuple[tuple[str, ImageSpec | str], ...] = ()

    def __post_init__(self) -> None:
        _address(self.view_id, self.generation)
        _boolean(self.replace_all, "replace_all")
        if self.revision is not None:
            _integer(self.revision, "revision")
        _record_collection(self.items, "UpdateItems.items")
        _record_collection(self.expected_images, "UpdateItems.expected_images")
        object.__setattr__(self, "items", tuple(self.items))
        if not all(isinstance(item, ViewItem) for item in self.items):
            raise TypeError("UpdateItems.items requires ViewItem instances")
        ids = tuple(item.id for item in self.items)
        if len(ids) != len(set(ids)):
            raise ValueError("UpdateItems contains duplicate item IDs")
        guards: list[tuple[str, ImageSpec | str]] = []
        for pair in self.expected_images:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise TypeError("expected_images requires (item ID, image) pairs")
            item_id, image = pair
            _identifier(item_id, "expected_images item ID")
            if isinstance(image, str):
                _identifier(image, "expected_images image key")
            elif not isinstance(image, ImageSpec):
                raise TypeError("expected_images requires ImageSpec or image key")
            guards.append((item_id, image))
        if len(guards) != len({item_id for item_id, _ in guards}):
            raise ValueError("expected_images contains duplicate item IDs")
        object.__setattr__(self, "expected_images", tuple(guards))


@dataclass(frozen=True)
class UpdateStatus:
    view_id: str
    generation: int
    state: ViewState
    message: str = ""
    kind: StatusKind = StatusKind.INFO

    def __post_init__(self) -> None:
        _address(self.view_id, self.generation)
        if self.state not in {"ready", "loading", "empty", "failed"}:
            raise ValueError(f"Invalid view state: {self.state!r}")
        if not isinstance(self.message, str):
            raise TypeError("status.message requires a string")
        if not isinstance(self.kind, StatusKind):
            raise TypeError("status.kind requires StatusKind")


@dataclass(frozen=True)
class ExitSession:
    result: object = None


UiCommand: TypeAlias = (
    PushView
    | BeginNavigation
    | CompleteNavigation
    | FailNavigation
    | ReplaceView
    | PopView
    | UpdateItems
    | UpdateStatus
    | ExitSession
)
BackgroundCommand: TypeAlias = UpdateItems | UpdateStatus | ReplaceView | CompleteNavigation | FailNavigation
HandlerResult: TypeAlias = UiCommand | tuple[UiCommand, ...] | list[UiCommand] | None
EventHandler: TypeAlias = Callable[[UiEvent], HandlerResult]
_COMMAND_TYPES = (
    PushView,
    BeginNavigation,
    CompleteNavigation,
    FailNavigation,
    ReplaceView,
    PopView,
    UpdateItems,
    UpdateStatus,
    ExitSession,
)


def _ignore_event(event: UiEvent) -> None:
    return None


@dataclass
class _Frame:
    view: ViewSpec
    generation: int
    position: ViewPosition = field(default_factory=ViewPosition)
    auto_entered: bool = False
    picker_state: PickerState | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _FrameHandoff:
    source: _Frame
    destination: tuple[str, int]
    replace_current: bool
    request: BeginNavigation


class ViewSession:
    def __init__(
        self, initial_view: ViewSpec, handle_event: EventHandler | None = None, *, max_pending: int = 256,
        clock: Callable[[], float] = time.monotonic, loading_delay: float = 0.15,
    ) -> None:
        _view(initial_view)
        _integer(max_pending, "max_pending", 1)
        _duration(loading_delay, "session.loading_delay")
        if not callable(clock):
            raise TypeError("session.clock requires a callable")
        if handle_event is not None and not callable(handle_event):
            raise TypeError("handle_event requires a callable")
        self._generations = count(1)
        self._stack = [self._frame(initial_view)]
        self.handle_event: EventHandler = handle_event if handle_event is not None else _ignore_event
        self._pending: OrderedDict[tuple[Hashable, ...], BackgroundCommand] = OrderedDict()
        self._lock = Lock()
        self._max_pending = max_pending
        self.version = 0
        self.exited = False
        self.result: object = None
        self.navigation: BeginNavigation | None = None
        self._loading: LoadingLifecycle | None = None
        self._clock = clock
        self._loading_delay = loading_delay
        self.navigation_token: LoadingToken | None = None
        self._handoff: _FrameHandoff | None = None

    @property
    def loading(self) -> LoadingLifecycle:
        # Sessions may be assembled by a preparation worker before UI entry.
        # Bind the lifecycle only when the renderer first uses it on its owner.
        if self._loading is None:
            self._loading = LoadingLifecycle(clock=self._clock)
            if self._handoff is not None:
                self._begin_loading(self._handoff.request, self._handoff.source.view)
                if self.navigation_token is not None:
                    self._loading.apply(FinishLoading(self.navigation_token))
            else:
                self._start_loading()
        return self._loading

    @property
    def input_blocked(self) -> bool:
        return self.navigation is not None or self._handoff is not None

    def _clear_loading(self) -> None:
        if self._loading is not None and self.navigation_token is not None:
            self._loading.apply(CancelLoading(self.navigation_token))
        self.navigation_token = None
        self._handoff = None

    def _start_loading(self) -> None:
        if self._loading is None or self.navigation is None:
            return
        self._clear_loading()
        self._begin_loading(self.navigation, self.current)

    def _begin_loading(self, pending: BeginNavigation, view: ViewSpec) -> None:
        if self._loading is None:
            raise RuntimeError("Navigation loading requires a bound lifecycle")
        self.navigation_token = self._loading.start(LoadingTaskSpec(
            pending.request_id, LoadingOwner(pending.view_id, pending.generation), pending.message,
            LoadingInteraction.BLOCKING, LoadingPlacement.FOOTER,
            ActionBindings(tuple(ResolvedAction(action) for action in view.actions)),
            delay=self._loading_delay, transition=True,
        ))

    def loading_snapshot(self, *, feedback: bool = False) -> LoadingSnapshot | None:
        lifecycle = self.loading
        if self.navigation_token is None:
            return None
        snapshots = lifecycle.snapshots(self.navigation_token.owner, feedback=feedback)
        return snapshots[0] if snapshots else None

    def acknowledge_frame(self, view_id: str, generation: int) -> bool:
        _address(view_id, generation)
        if self._handoff is None or self._handoff.destination != (view_id, generation):
            return False
        lifecycle = self.loading
        if self.navigation_token is None:
            raise RuntimeError("Frame handoff requires an active loading token")
        lifecycle.acknowledge_frame(self.navigation_token)
        self.navigation_token = None
        self._handoff = None
        return True

    def _frame(self, view: ViewSpec) -> _Frame:
        frame = _Frame(view, next(self._generations), ViewPosition(active_tab_id=view.active_tab_id))
        self._reconcile(frame)
        return frame

    @property
    def current(self) -> ViewSpec:
        return self._stack[-1].view

    @property
    def generation(self) -> int:
        return self._stack[-1].generation

    @property
    def position(self) -> ViewPosition:
        return self._stack[-1].position

    @property
    def depth(self) -> int:
        return len(self._stack)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def item(self, item_id: str | None = None) -> ViewItem | None:
        selected = item_id if item_id is not None else self.position.selected_id
        return next((item for item in self.current.items if item.id == selected), None)

    def frame(self, view_id: str, generation: int) -> _Frame | None:
        return next(
            (frame for frame in self._stack if frame.view.id == view_id and frame.generation == generation), None
        )

    def _reconcile(self, frame: _Frame, previous_items: Sequence[ViewItem] = ()) -> None:
        frame.position.validate()
        visible = selectable_items(visible_items(frame.view, frame.position.query))
        valid = {item.id for item in visible}
        if frame.position.selected_id not in valid:
            old_index = next((i for i, item in enumerate(previous_items) if item.id == frame.position.selected_id), 0)
            frame.position.selected_id = visible[min(old_index, len(visible) - 1)].id if visible else None
        frame.position.marked_ids.intersection_update(item.id for item in selectable_items(frame.view.items))
        tab_ids = {tab.id for tab in frame.view.tabs}
        if frame.position.active_tab_id not in tab_ids:
            frame.position.active_tab_id = frame.view.active_tab_id

    def apply(self, command: UiCommand) -> bool:
        source_frame, previous = self._stack[-1], self.navigation
        accepted = self._apply(command)
        if not accepted:
            return False
        if isinstance(command, BeginNavigation):
            self._start_loading()
        elif isinstance(command, CompleteNavigation):
            if previous is None:
                raise RuntimeError("Accepted navigation completion requires a pending request")
            if self._loading is not None and self.navigation_token is not None:
                self._loading.apply(FinishLoading(self.navigation_token))
            self._handoff = _FrameHandoff(source_frame, (self.current.id, self.generation), command.replace_current, previous)
        elif isinstance(command, FailNavigation) and self._loading is not None and self.navigation_token is not None:
            self._loading.apply(FailLoading(self.navigation_token, command.message))
            self.navigation_token = None
            self._handoff = None
        elif (previous is not None and self.navigation is None) or self.exited or (
            self._handoff is not None and self._handoff.destination != (self.current.id, self.generation)
        ):
            self._clear_loading()
        return True

    def _apply(self, command: UiCommand) -> bool:
        if not isinstance(command, _COMMAND_TYPES):
            raise TypeError(f"Unknown UI command: {type(command).__name__}")
        if self.exited:
            return False
        self.position.validate()
        if isinstance(command, BeginNavigation):
            if command.view_id != self.current.id or command.generation != self.generation:
                return False
            self.navigation = replace(command, item_id=self.position.selected_id)
            _LOG.debug(
                "Navigation started: source=%s generation=%s request=%s",
                command.view_id,
                command.generation,
                command.request_id,
            )
        elif isinstance(command, (CompleteNavigation, FailNavigation)):
            pending = self.navigation
            if (
                pending is None
                or command.view_id != self.current.id
                or command.generation != self.generation
                or (command.view_id, command.generation, command.request_id)
                != (pending.view_id, pending.generation, pending.request_id)
            ):
                _LOG.debug(
                    "Navigation completion ignored: stale source=%s generation=%s request=%s",
                    command.view_id,
                    command.generation,
                    command.request_id,
                )
                return False
            if isinstance(command, CompleteNavigation):
                if command.view.state not in {"ready", "empty"}:
                    raise ValueError(f"Cannot activate {command.view.state} destination; keep navigation pending")
                destination = self._frame(command.view)
                if command.replace_current:
                    self._stack[-1] = destination
                else:
                    self._stack.append(destination)
                _LOG.debug(
                    "Navigation completed: source=%s destination=%s request=%s",
                    command.view_id,
                    command.view.id,
                    command.request_id,
                )
            else:
                self._stack[-1].view = replace(
                    self.current,
                    message=command.message,
                    message_kind=StatusKind.ERROR,
                    revision=self.current.revision + 1,
                )
                self._stack[-1].auto_entered = True
                _LOG.warning(
                    "Navigation failed: source=%s request=%s message=%s",
                    command.view_id,
                    command.request_id,
                    command.message,
                )
            self.navigation = None
        elif isinstance(command, ExitSession):
            self.exited, self.result = True, command.result
            self.navigation = None
        elif isinstance(command, PushView):
            if command.view.state == "loading":
                raise ValueError("Cannot push a loading destination; use pending navigation")
            self._stack.append(self._frame(command.view))
            self.navigation = None
        elif isinstance(command, PopView):
            if len(self._stack) < 2:
                return False
            self._stack[-2].position.validate()
            self._stack.pop()
            self.navigation = None
            self._stack[-1].auto_entered = True
            self._reconcile(self._stack[-1])
        elif isinstance(command, (ReplaceView, UpdateItems, UpdateStatus)):
            frame = self.frame(command.view_id, command.generation)
            if frame is None:
                return False
            frame.position.validate()
            old_items = frame.view.items
            if isinstance(command, ReplaceView):
                if command.view.state == "loading" and (
                    command.new_activation or command.view.id != frame.view.id or frame.view.state != "loading"
                ):
                    raise ValueError("Cannot replace a source with a loading destination; use pending navigation")
                if command.new_activation or command.view.id != frame.view.id:
                    frame.generation = next(self._generations)
                    frame.auto_entered = False
                    frame.position = ViewPosition(active_tab_id=command.view.active_tab_id)
                frame.view = replace(command.view, revision=frame.view.revision + 1)
                if self.navigation is not None and (
                    self.navigation.view_id != self.current.id or self.navigation.generation != self.generation
                ):
                    self.navigation = None
            elif isinstance(command, UpdateStatus):
                frame.view = replace(
                    frame.view,
                    state=command.state,
                    message=command.message,
                    message_kind=command.kind,
                    revision=frame.view.revision + 1,
                )
            else:
                if command.revision is not None and command.revision <= frame.view.revision:
                    return False
                expected = dict(command.expected_images)
                old_by_id = {item.id: item for item in old_items}
                for item_id, expected_image in expected.items():
                    current = old_by_id.get(item_id)
                    if current is None or current.image is None:
                        return False
                    if isinstance(expected_image, ImageSpec):
                        matches = current.image == expected_image
                    else:
                        matches = current.image.key == expected_image
                    if not matches:
                        return False
                if command.replace_all:
                    updated = tuple(command.items)
                else:
                    updates = {item.id: item for item in command.items if item.id in old_by_id}
                    if not updates:
                        return False
                    updated = tuple(updates.get(item.id, item) for item in old_items)
                frame.view = replace(frame.view, items=updated, revision=command.revision or frame.view.revision + 1)
            self._reconcile(frame, old_items)
            if (
                self.navigation is not None
                and frame is self._stack[-1]
                and self.navigation.item_id != frame.position.selected_id
            ):
                self.cancel_navigation()
        else:
            raise TypeError(f"Unknown UI command: {type(command).__name__}")
        self.version += 1
        return True

    def dispatch(self, event: UiEvent) -> bool:
        if not isinstance(event, UiEvent):
            raise TypeError("dispatch requires UiEvent")
        if event.view_id != self.current.id or event.generation != self.generation or self.exited:
            return False
        self.position.validate()
        # Reject unsupported interaction before cancelling work or changing focus.
        marked: set[str] | None = None
        if event.kind == "select" and event.value is not None:
            if not self.current.selection_policy.multiple or not isinstance(event.value, tuple):
                return False
            valid = {item.id for item in selectable_items(self.current.items)}
            marked = set(event.value)
            if not marked or not marked <= valid:
                return False
            if event.item_id is not None:
                item = self.item(event.item_id)
                if item is None or not item.selectable:
                    return False
        elif event.kind in {"select", "focus"}:
            item = self.item(event.item_id)
            if item is None or not item.selectable:
                return False
        elif event.kind == "tab":
            if event.value not in {tab.id for tab in self.current.tabs}:
                return False
        elif event.kind == "action":
            if event.value not in {action.id for action in self.current.actions}:
                return False
        if event.kind == "back" and self._handoff is not None:
            return self.cancel_navigation()
        if self.navigation is not None:
            if event.kind == "back":
                self.cancel_navigation()
                return True
            if event.kind == "select" and event.item_id == self.position.selected_id:
                return False
            if event.kind in {"select", "query", "tab", "quit"} or (
                event.kind == "focus" and event.item_id != self.position.selected_id
            ):
                self.cancel_navigation()
        if marked is not None:
            self.position.marked_ids = marked
        elif event.kind in {"select", "focus"}:
            item = self.item(event.item_id)
            if item is None or not item.selectable:
                return False
            self.position.selected_id = item.id
        elif event.kind == "tab":
            if not isinstance(event.value, str):
                raise TypeError("tab event value requires a string")
            self.position.active_tab_id = event.value
            self._stack[-1].auto_entered = False
        elif event.kind == "query":
            self._stack[-1].auto_entered = False
        if event.kind == "back" and self.depth > 1:
            return self.apply(PopView())
        result = self.handle_event(event)
        if result is None:
            return False
        commands = tuple(result) if isinstance(result, (list, tuple)) else (result,)
        if not all(isinstance(command, _COMMAND_TYPES) for command in commands):
            raise TypeError("Event handler must return UI commands or None")
        changed = False
        for command in commands:
            changed = self.apply(command) or changed
        return changed

    def cancel_navigation(self) -> bool:
        if self.navigation is None and self._handoff is None:
            return False
        if self.navigation is not None:
            _LOG.debug("Navigation cancelled: source=%s request=%s", self.navigation.view_id, self.navigation.request_id)
        if self._handoff is not None:
            if self._handoff.replace_current:
                self._stack[-1] = self._handoff.source
            else:
                self._stack.pop()
        self._clear_loading()
        self.navigation = None
        self._stack[-1].auto_entered = True
        self.version += 1
        return True

    def auto_event(self) -> UiEvent | None:
        frame = self._stack[-1]
        if (
            self.input_blocked
            or frame.auto_entered
            or frame.view.state != "ready"
            or not frame.view.selection_policy.auto_single
        ):
            return None
        items = selectable_items(frame.view.items)
        if len(items) != 1:
            return None
        frame.auto_entered = True
        return UiEvent("select", frame.view.id, frame.generation, items[0].id)

    def post(self, command: BackgroundCommand) -> None:
        """Nonblocking producer handoff; identical pending targets keep only the latest update."""
        key: tuple[Hashable, ...]
        if isinstance(command, UpdateItems):
            key = (
                "items",
                command.view_id,
                command.generation,
                command.replace_all,
                tuple(item.id for item in command.items),
                command.expected_images,
            )
        elif isinstance(command, (ReplaceView, UpdateStatus)):
            key = ("replace" if isinstance(command, ReplaceView) else "status", command.view_id, command.generation)
        elif isinstance(command, (CompleteNavigation, FailNavigation)):
            key = ("navigation", command.view_id, command.generation, command.request_id)
        else:
            raise TypeError("Background jobs may only update existing views")
        with self._lock:
            previous = self._pending.get(key)
            if isinstance(command, UpdateItems) and isinstance(previous, UpdateItems):
                if (
                    command.revision is not None
                    and previous.revision is not None
                    and command.revision <= previous.revision
                ):
                    return
            if key not in self._pending and len(self._pending) >= self._max_pending:
                raise BufferError("UI update queue is full")
            self._pending[key] = command

    def drain_updates(self) -> bool:
        with self._lock:
            commands = tuple(self._pending.values())
            self._pending.clear()
        changed = False
        for command in commands:
            changed = self.apply(command) or changed
        return changed
