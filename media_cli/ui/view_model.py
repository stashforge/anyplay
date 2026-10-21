"""Provider-independent data supplied to terminal views."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Literal, TypeVar

from .footer import Footer, FooterStatus, StatusKind
from .fuzzy import fuzzy_match
from .row_layout import DEFAULT_ROW_INDENTATION, RowIndentation
from .row_text import plain_row_text
from .styled_text import StyledText, TextRun


class _DetailChoice(Enum):
    INHERIT = auto()
    BLANK = auto()


Inherit = _DetailChoice.INHERIT
Blank = _DetailChoice.BLANK
_DETAIL_FIELDS = ("title", "secondary", "metadata")
_SEPARATOR = " \u2022 "
ViewState = Literal["ready", "loading", "empty", "failed"]
ImageState = Literal["resolving", "available", "unavailable", "failed"]
ItemKind = Literal["item", "submenu", "heading"]
_T = TypeVar("_T")


def _string(value: object, owner: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{owner} requires a string")
    return value


def _nonblank(value: object, owner: str) -> None:
    if not _string(value, owner).strip():
        raise ValueError(f"{owner} requires a nonblank string")


def _boolean(value: object, owner: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{owner} requires a boolean")


def _snapshot(values: Iterable[object], element_type: type[_T], owner: str) -> tuple[_T, ...]:
    if isinstance(values, (str, bytes, bytearray, Mapping)) or not isinstance(values, Iterable):
        raise TypeError(f"{owner} requires an iterable of {element_type.__name__} instances")
    result: list[_T] = []
    for index, value in enumerate(values):
        if not isinstance(value, element_type):
            raise TypeError(f"{owner}[{index}] requires {element_type.__name__}")
        result.append(value)
    return tuple(result)


@dataclass(frozen=True)
class LabelValue:
    """A labelled value rendered as ``label: value``."""

    label: str
    value: str

    def __post_init__(self) -> None:
        _nonblank(self.label, "LabelValue.label")
        _nonblank(self.value, "LabelValue.value")


@dataclass(frozen=True)
class Count:
    """A nonnegative count with a singular noun (the plural adds ``s``)."""

    value: int
    noun: str

    def __post_init__(self) -> None:
        if type(self.value) is not int:
            raise TypeError("Count.value requires an integer")
        if self.value < 0:
            raise ValueError("Count.value must be nonnegative")
        _nonblank(self.noun, "Count.noun")


@dataclass(frozen=True, init=False)
class Content:
    """Immutable semantic parts, joined with the shared detail separator."""

    parts: tuple[str | LabelValue | Count, ...]

    def __init__(self, *parts: str | LabelValue | Count) -> None:
        if not parts:
            raise ValueError("Content requires at least one part; use Blank for no content")
        for index, part in enumerate(parts):
            if isinstance(part, str):
                _nonblank(part, f"Content.parts[{index}]")
            elif not isinstance(part, (LabelValue, Count)):
                raise TypeError(f"Content.parts[{index}] requires str, LabelValue, or Count")
        object.__setattr__(self, "parts", tuple(parts))


def render_content(content: Content) -> str:
    """Format semantic content; terminal sanitizing belongs to presentation."""
    if not isinstance(content, Content):
        raise TypeError("render_content requires Content")
    parts: list[str] = []
    for part in content.parts:
        if isinstance(part, LabelValue):
            parts.append(f"{part.label}: {part.value}")
        elif isinstance(part, Count):
            parts.append(f"{part.value} {part.noun}{'' if part.value == 1 else 's'}")
        else:
            parts.append(part)
    return _SEPARATOR.join(parts)


@dataclass(frozen=True)
class DetailLines:
    """Three required lines; Blank suppresses a line and Inherit uses common content."""

    title: Content | StyledText | _DetailChoice
    secondary: Content | StyledText | _DetailChoice
    metadata: Content | StyledText | _DetailChoice

    def __post_init__(self) -> None:
        for name, value in zip(_DETAIL_FIELDS, (self.title, self.secondary, self.metadata), strict=True):
            if not isinstance(value, (Content, StyledText)) and value is not Inherit and value is not Blank:
                raise TypeError(f"DetailLines.{name} requires Content, StyledText, Inherit, or Blank")


@dataclass(frozen=True)
class ResolvedDetails:
    title: str | StyledText
    secondary: str | StyledText
    metadata: str | StyledText

    def __post_init__(self) -> None:
        for name, value in zip(_DETAIL_FIELDS, (self.title, self.secondary, self.metadata), strict=True):
            if not isinstance(value, StyledText):
                _string(value, f"ResolvedDetails.{name}")


def resolve_details(common: DetailLines, item: DetailLines | _DetailChoice | None = None) -> ResolvedDetails:
    """Resolve each field against common lines, retaining supplied text roles.

    Omit item (or pass Inherit) for common lines; Blank blanks the entire item.
    Common lines must never contain Inherit, even when an item overrides them.
    """
    if not isinstance(common, DetailLines):
        raise TypeError("common details requires DetailLines")
    common_lines = (common.title, common.secondary, common.metadata)
    for name, line in zip(_DETAIL_FIELDS, common_lines, strict=True):
        if line is Inherit:
            raise ValueError(f"common details.{name} cannot be Inherit")
    if item is not None and item is not Inherit and item is not Blank and not isinstance(item, DetailLines):
        raise TypeError("item details requires DetailLines, Inherit, or Blank")
    item_lines = (item.title, item.secondary, item.metadata) if isinstance(item, DetailLines) else (item, item, item)
    fields: dict[str, str | StyledText] = {}
    for name, common_line, value in zip(_DETAIL_FIELDS, common_lines, item_lines, strict=True):
        if value is None or value is Inherit:
            value = common_line
        fields[name] = (
            value if isinstance(value, StyledText) else render_content(value) if isinstance(value, Content) else ""
        )
    return ResolvedDetails(**fields)


@dataclass(frozen=True)
class ImageSpec:
    key: str
    state: ImageState = "resolving"
    url: str | None = None
    message: str = ""

    def __post_init__(self) -> None:
        _nonblank(self.key, "ImageSpec.key")
        _string(self.state, f"Image {self.key!r}.state")
        _string(self.message, f"Image {self.key!r}.message")
        if self.url is not None:
            _string(self.url, f"Image {self.key!r}.url")
        if self.state not in ("resolving", "available", "unavailable", "failed"):
            raise ValueError(f"Image {self.key!r}: invalid state {self.state!r}")
        if self.state == "available" and (self.url is None or not self.url.strip()):
            raise ValueError(f"Image {self.key!r}: an available image needs a nonblank URL")


@dataclass(frozen=True)
class ActionHint:
    """Row-specific footer presentation for an already-declared action."""

    action_id: str
    label: str
    visible: bool = field(default=True, kw_only=True)

    def __post_init__(self) -> None:
        _nonblank(self.action_id, "ActionHint.action_id")
        _nonblank(self.label, "ActionHint.label")
        TextRun(self.label, "actions")
        _boolean(self.visible, "ActionHint.visible")


@dataclass(frozen=True)
class ViewItem:
    id: str
    label: str | StyledText
    kind: ItemKind = "item"
    enabled: bool = True
    hint: str | StyledText = ""
    prefix: str | StyledText = ""
    section_id: str | None = None
    details: DetailLines | _DetailChoice = field(kw_only=True)
    image: ImageSpec | None = None
    info: str = ""
    reference: object = field(default=None, compare=False, repr=False)
    adjustable: bool = field(default=False, kw_only=True)
    action_hints: Sequence[ActionHint] = field(default=(), kw_only=True)
    footer_context: Sequence[str] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        _nonblank(self.id, "ViewItem.id")
        for name, value in (("label", self.label), ("hint", self.hint), ("prefix", self.prefix)):
            if not isinstance(value, StyledText):
                _string(value, f"Item {self.id!r}.{name}")
            plain_row_text(value)
        _string(self.info, f"Item {self.id!r}.info")
        _string(self.kind, f"Item {self.id!r}.kind")
        _boolean(self.enabled, f"Item {self.id!r}.enabled")
        _boolean(self.adjustable, f"Item {self.id!r}.adjustable")
        object.__setattr__(self, "footer_context", _snapshot(self.footer_context, str, f"Item {self.id!r}.footer_context"))
        for part in self.footer_context:
            TextRun(part, "context")
        object.__setattr__(self, "action_hints", _snapshot(self.action_hints, ActionHint, f"Item {self.id!r}.action_hints"))
        hint_ids = tuple(hint.action_id for hint in self.action_hints)
        if len(hint_ids) != len(set(hint_ids)):
            raise ValueError(f"Item {self.id!r}: Duplicate action hint IDs")
        if self.adjustable and not self.selectable:
            raise ValueError(f"Item {self.id!r}: adjustable rows must be selectable")
        if self.section_id is not None:
            _nonblank(self.section_id, f"Item {self.id!r}.section_id")
        if self.image is not None and not isinstance(self.image, ImageSpec):
            raise TypeError(f"Item {self.id!r}.image requires ImageSpec or None")
        if self.kind not in ("item", "submenu", "heading"):
            raise ValueError(f"Item {self.id!r}: invalid kind {self.kind!r}")
        if not isinstance(self.details, DetailLines) and self.details is not Inherit and self.details is not Blank:
            raise TypeError(f"Item {self.id!r}.details requires DetailLines, Inherit, or Blank")

    @property
    def label_text(self) -> str:
        return plain_row_text(self.label)

    @property
    def hint_text(self) -> str:
        return plain_row_text(self.hint)

    @property
    def prefix_text(self) -> str:
        return plain_row_text(self.prefix)

    @property
    def selectable(self) -> bool:
        return self.kind != "heading" and self.enabled


@dataclass(frozen=True)
class ViewTab:
    id: str
    label: str

    def __post_init__(self) -> None:
        _nonblank(self.id, "ViewTab.id")
        _string(self.label, f"Tab {self.id!r}.label")


@dataclass(frozen=True)
class ViewAction:
    id: str
    label: str
    keys: Sequence[str] = ()
    footer: bool = field(default=True, kw_only=True)

    def __post_init__(self) -> None:
        _nonblank(self.id, "ViewAction.id")
        _string(self.label, f"Action {self.id!r}.label")
        _boolean(self.footer, f"Action {self.id!r}.footer")
        object.__setattr__(self, "keys", _snapshot(self.keys, str, f"Action {self.id!r}.keys"))
        for index, key in enumerate(self.keys):
            _nonblank(key, f"Action {self.id!r}.keys[{index}]")


@dataclass(frozen=True)
class SelectionPolicy:
    multiple: bool = False
    auto_single: bool = False
    query_input: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("multiple", self.multiple),
            ("auto_single", self.auto_single),
            ("query_input", self.query_input),
        ):
            _boolean(value, f"SelectionPolicy.{name}")


@dataclass(frozen=True)
class ViewSpec:
    id: str
    prompt: str
    items: Sequence[ViewItem] = ()
    details: DetailLines = field(kw_only=True)
    image: ImageSpec | None = None
    tabs: Sequence[ViewTab] = ()
    active_tab_id: str | None = None
    actions: Sequence[ViewAction] = ()
    context: Sequence[str] = ()
    state: ViewState = "ready"
    message: str = ""
    selection_policy: SelectionPolicy = field(default_factory=SelectionPolicy)
    reverse_items: bool = True
    revision: int = 0
    message_kind: StatusKind = StatusKind.INFO
    row_indentation: RowIndentation = field(default=DEFAULT_ROW_INDENTATION, kw_only=True)
    help: tuple[tuple[str, str], ...] = field(default=(), kw_only=True)
    footer: Footer | None = field(default=None, kw_only=True)
    footer_statuses: Sequence[FooterStatus] = field(default=(), kw_only=True)
    total_count: int | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _nonblank(self.id, "ViewSpec.id")
        _string(self.prompt, f"Screen {self.id!r}.prompt")
        _string(self.message, f"Screen {self.id!r}.message")
        _string(self.state, f"Screen {self.id!r}.state")
        _boolean(self.reverse_items, f"Screen {self.id!r}.reverse_items")
        if type(self.revision) is not int:
            raise TypeError(f"Screen {self.id!r}.revision requires an integer")
        if self.revision < 0:
            raise ValueError(f"Screen {self.id!r}.revision must be nonnegative")
        if self.image is not None and not isinstance(self.image, ImageSpec):
            raise TypeError(f"Screen {self.id!r}.image requires ImageSpec or None")
        if not isinstance(self.selection_policy, SelectionPolicy):
            raise TypeError(f"Screen {self.id!r}.selection_policy requires SelectionPolicy")
        if not isinstance(self.row_indentation, RowIndentation):
            raise TypeError(f"Screen {self.id!r}.row_indentation requires RowIndentation")
        if self.footer is not None and not isinstance(self.footer, Footer):
            raise TypeError(f"Screen {self.id!r}.footer requires Footer or None")
        object.__setattr__(self, "footer_statuses", _snapshot(self.footer_statuses, FooterStatus,
                                                          f"Screen {self.id!r}.footer_statuses"))
        if not isinstance(self.help, tuple):
            raise TypeError(f"Screen {self.id!r}.help requires a tuple of (label, description) tuples")
        help_labels: set[str] = set()
        for index, entry in enumerate(self.help):
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise TypeError(f"Screen {self.id!r}.help[{index}] requires a two-item tuple")
            label, description = entry
            _nonblank(label, f"Screen {self.id!r}.help[{index}].label")
            _nonblank(description, f"Screen {self.id!r}.help[{index}].description")
            TextRun(label, "actions")
            TextRun(description, "details")
            if label in help_labels:
                raise ValueError(f"Screen {self.id!r}: Duplicate help label {label!r}")
            help_labels.add(label)
        if self.active_tab_id is not None:
            _nonblank(self.active_tab_id, f"Screen {self.id!r}.active_tab_id")
        if not isinstance(self.message_kind, StatusKind):
            raise TypeError(f"Screen {self.id!r}.message_kind requires StatusKind")
        if self.state not in ("ready", "loading", "empty", "failed"):
            raise ValueError(f"Screen {self.id!r}: invalid state {self.state!r}")
        try:
            resolve_details(self.details)
        except (TypeError, ValueError) as error:
            raise type(error)(f"Screen {self.id!r}: {error}") from error
        # Snapshot sequences so a producer cannot reorder the live view in place.
        object.__setattr__(self, "items", _snapshot(self.items, ViewItem, f"Screen {self.id!r}.items"))
        object.__setattr__(self, "tabs", _snapshot(self.tabs, ViewTab, f"Screen {self.id!r}.tabs"))
        object.__setattr__(self, "actions", _snapshot(self.actions, ViewAction, f"Screen {self.id!r}.actions"))
        object.__setattr__(self, "context", _snapshot(self.context, str, f"Screen {self.id!r}.context"))
        if self.total_count is not None:
            if type(self.total_count) is not int:
                raise TypeError(f"Screen {self.id!r}.total_count requires an integer or None")
            if self.total_count < sum(item.selectable for item in self.items):
                raise ValueError(f"Screen {self.id!r}.total_count cannot be less than its selectable rows")
        declared_actions = {action.id for action in self.actions}
        if any(item.adjustable for item in self.items):
            if not {"increase", "decrease"} <= declared_actions:
                raise ValueError(f"Screen {self.id!r}: adjustable rows require increase and decrease actions")
        for item in self.items:
            for hint in item.action_hints:
                if hint.action_id not in declared_actions:
                    raise ValueError(f"Screen {self.id!r}, item {item.id!r}: Unknown hint action {hint.action_id!r}")
            try:
                resolve_details(self.details, item.details)
            except (TypeError, ValueError) as error:
                raise type(error)(f"Screen {self.id!r}, item {item.id!r}: {error}") from error
        ids = tuple(item.id for item in self.items)
        if len(ids) != len(set(ids)):
            raise ValueError(f"Screen {self.id!r}: Duplicate item IDs {ids!r}")
        headings = {item.id for item in self.items if item.kind == "heading"}
        for item in self.items:
            if item.section_id is not None and item.section_id not in headings:
                raise ValueError(f"Screen {self.id!r}, item {item.id!r}: Unknown section reference {item.section_id!r}")
        tab_ids = tuple(tab.id for tab in self.tabs)
        if len(tab_ids) != len(set(tab_ids)):
            raise ValueError(f"Screen {self.id!r}: Duplicate tab IDs {tab_ids!r}")
        if self.active_tab_id is not None and self.active_tab_id not in tab_ids:
            raise ValueError(f"Screen {self.id!r}: Unknown active tab {self.active_tab_id!r}")
        command_has_keys: dict[str, bool] = {}
        key_owners: dict[str, str] = {}
        for action in self.actions:
            if action.id in command_has_keys and (not action.keys or not command_has_keys[action.id]):
                raise ValueError(f"Screen {self.id!r}: Duplicate action IDs {action.id!r} require nonempty key groups")
            # Compare bindings as the picker does, without changing display keys.
            keys = {key.upper() for key in action.keys}
            keys = {"BTAB" if key == "SHIFT_TAB" else key for key in keys}
            for key in keys:
                if key in key_owners:
                    raise ValueError(
                        f"Screen {self.id!r}: ambiguous key {key!r} in action groups "
                        f"{key_owners[key]!r} and {action.id!r}"
                    )
                key_owners[key] = action.id
            command_has_keys[action.id] = bool(keys)


def selectable_items(items: Iterable[ViewItem]) -> tuple[ViewItem, ...]:
    return tuple(item for item in items if item.selectable)


def visible_items(view: ViewSpec, query: str) -> tuple[ViewItem, ...]:
    if not query.strip():
        return tuple(view.items)
    matched = {item.id for item in view.items if item.kind != "heading" and fuzzy_match(query, item.label_text) is not None}
    sections = {item.section_id for item in view.items if item.id in matched}
    return tuple(item for item in view.items if item.id in matched or item.id in sections)
