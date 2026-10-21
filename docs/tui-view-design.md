# Shared Terminal UI Design

This design includes stage-specific progress notes. [Current Tasks](TASKS.md)
owns present completion status; do not treat earlier migration lists as current.

Status: shared foundation implemented; Artist is the first migrated consumer.
Existing non-migrated callers retain the compatibility picker interface.

## Intent

Give Anyplay a reusable terminal UI that accepts structured views, renders
them consistently, and reports interactions to application controllers.
New menus must not require changes to the picker renderer. Preserve the
current appearance, keyboard behavior, responsiveness, and plain mode.
Pause further AnimeThemes features until this foundation is established.

No commits, pushes, or version changes are included in this work.

## Audit Findings

- `ui/picker.py` already handles rendering, filtering, disabled rows, tabs,
  multiple selection, confirmation, and terminal image overlays.
- Its public interface has more than 30 arguments, including independent
  label lists, index-based callbacks, hints, headers, and image callbacks.
- Application modules implement nesting through separate picker calls and
  state strings. `context.py` has several individual previous-state fields,
  rather than a reusable navigation stack.
- `ui/plain_picker.py` duplicates selection policies and derives navigation
  text by inspecting rendered ANSI strings. It receives disabled-row settings
  through extra arguments without applying the same rules as the TUI.
- Artist image loading exposed the consequences: the footer and renderer
  received different objects, and an image-data update needed a manually
  requested redraw when the text did not change.

## Approach

Introduce structured views and a shared interaction engine around the current
terminal renderer. Keep a temporary adapter for existing picker callers.
Prove the interface by migrating Artist navigation before migrating the
remaining application flows.

A complete renderer rewrite would put existing keyboard and image behavior
at unnecessary risk. Adding only a dictionary adapter would leave navigation,
updates, and presentation state spread across application callbacks.

## Ownership

Application controllers own provider requests, catalog data, playback,
downloads, tracking, permission decisions, and the destination of an action.
They translate domain objects into display objects and handle UI events.
They schedule remote work outside the terminal input loop.

The UI owns focus, filtering, scrolling, selection, tabs, navigation history,
rendering, image presentation, transient messages, and keyboard bindings.
It has no imports from providers, application controllers, CLI argument
parsing, tracking, or playback logic.

The UI's cover service may download and render a supplied image URL. That is
presentation work. Resolving a provider resource into that URL remains
application work. Information screens must receive their data explicitly;
they must not independently search a provider for missing metadata.

## Shared Data Contract

Use small typed Python data objects, with adapters for domain dictionaries.
Do not require providers to return UI-specific objects.

### Strict Presentation And Navigation Follow-Up

The user approved explicit presentation choices after an incomplete Artist
loading destination replaced a valid search screen. Keep the visual layout
unchanged; enforce the following contract at the shared boundary:

- Every screen supplies `DetailLines`, and every item explicitly supplies its
  details policy. Every `DetailLines` field is required.
- `Content(*parts)` supplies immutable content, `Inherit` uses the corresponding
  screen field, and `Blank` deliberately suppresses it. Missing fields, plain
  strings in place of policies, and unresolved screen-level inheritance are
  programming errors, not invitations to guess.
- Content supports ordinary strings, `LabelValue`, and `Count`. Formatting,
  separators, sanitizing, colors and truncation belong to presentation.
- Screen-level details are stored once. Items can inherit the whole block or
  override individual fields. Existing optional image descriptors retain their
  explicit resolving/available/unavailable/failed lifecycle.
- Fixed content parts, items, tabs, contexts and command snapshots are tuples.
  Mutable selection, caches and producer queues remain mutable intentionally.
- `BeginNavigation` leaves the complete source view active. A matching
  `CompleteNavigation` installs one ready/empty destination atomically;
  `FailNavigation` reports an error without replacing source content.
- Back cancels pending navigation before leaving the source. A different
  selection, query, tab or focused item invalidates the pending request. Late
  responses must match the source generation and request identity.
- Initial loading without a previous screen remains supported. Pushing a
  loading child is rejected; callers must use pending navigation instead.
- Navigation start, completion, cancellation, failure and stale completions
  are written to the normal debug log. Provider failures retain tracebacks.

The legacy AnimeThemes search-to-Artist boundary projects the current search
into the same shared model using the same metadata helper as legacy search.
It preserves the selected result, search tab, filter, scroll and rendered cover
while fetching. It does not introduce a second provider-specific renderer.

### Footer Roles

`ui/footer.py` owns immutable `Footer` and `FooterStatus` values with
`StatusKind` roles. Status, context and action segments are rendered separately;
context/actions establish their muted color independently of status resets.
Semantic values reject ANSI/control characters. The remaining string callers
use a centralized legacy reset guard rather than per-screen color repairs.
Search workers return semantic statuses; picker boundaries still receive
rendered strings.

`ViewSpec.message_kind` is independent of the screen lifecycle state. A failed
navigation keeps the valid source ready while its message receives the error
role. `UpdateStatus` supplies/resets that role explicitly. Cover status preserves
sanitized diagnostic details, and legacy overlays preserve trusted badge colors.
Typed footer context is explicit; the renderer does not guess provider labels.

### View

- Stable view ID and activation generation.
- Prompt, ordered items, explicit loading/empty/error state.
- Optional tabs identified by stable IDs and the active tab ID.
- Supported actions and their existing key bindings.
- Selection policy: single/multiple, optional automatic single-result entry,
  explicit list order, and query-input behavior.
- Optional view-level details and image for menus sharing one subject.
- Revision identifying an accepted state update.

### Item

- Stable item ID, label, optional hint and prefix/badge.
- Kind: item, submenu, or heading.
- Enabled state and optional reason for a disabled selectable item.
- Optional details, image descriptor, and information-screen content.
- Optional section ID connecting rows to a heading.
- Opaque application reference; the UI does not inspect domain payloads.

Headings are not disabled actions. They have no selection target or result
number. Disabled actions may have an explanation but cannot execute.
Both are excluded from automatic and multiple selection.

### Details

Three structured text lines: title, secondary information, and metadata.
The UI renders these with the existing title, secondary, and metadata colors.
The fourth line is composed from context, image/transient status, and the
available navigation actions. Applications do not supply ANSI footer strings.

Keep the existing image area, list, prompt/divider, and four detail lines.
No additional columns, panels, or permanent footer rows are introduced.
Empty detail fields retain their allocated line; the application decides
whether a meaningful fallback is appropriate.

### Image

Application state is explicit: resolving URL, available URL, unavailable,
or resolution failed. Image identity changes when its URL changes.

The presentation service owns queued/downloading/rendering/ready/failed
state for an available URL. The footer and image area read the same resolved
presentation state. Cached images can enter ready immediately.

Use the existing footer status placement and spinner. Distinguish fetching
cover information, loading/rendering the image, unavailable, and failed.
Keep navigation hints visible within the existing width constraints.
Do not report unavailable while the URL is still being resolved.

## Events And Navigation

A UI session receives a view and an application event handler. Events use
view IDs, activation generations, and item IDs, never list indexes.

Events include selection, focus change, query submission, tab change,
requested action, Back, and Quit. Filtering already supplied items remains
local UI behavior; an application search query is an explicit event.

The handler returns presentation commands such as push view, replace view,
update items, update status, pop view, or exit session. The handler must not
block on network requests. Background application work delivers updates
through a thread-safe session queue.

The UI maintains a stack containing each view and its selected item ID,
filter text, scroll position, active tab, and marked item IDs. The application
chooses which child view to push. Back restores the parent state. At a root,
Back and Quit remain distinct events handled according to that flow's policy.
Playing media returns control to the existing view without losing its state.

Automatic entry into a single result waits for final results and considers
only enabled selectable items. It is explicit per view/tab. Returning with
Back suppresses that automatic entry until a new query or activation warrants
it, so the user cannot become trapped in an automatically reopened submenu.

## Async Updates And Selection Safety

- Apply updates by view ID, activation generation, and item ID.
- Discard updates for closed or superseded activations, even if IDs match.
- Updating an existing parent in the stack must not switch the active view.
- Preserve focus by item ID across reordering and result refreshes.
- If that item disappears, choose the nearest enabled item or explicit empty
  state; never execute the item that inherits its old index.
- Metadata, image, status, and item changes all invalidate presentation.
- Coalesce repeated pending updates; do not redraw continuously without a
  visible change. Ignore image completion for an obsolete image URL.
- Loading must not block keyboard input or change the selected item.

## Filtering, Headings, And Plain Mode

Reuse the current fuzzy matcher and item rendering. A section heading remains
visible when its child items match; headings do not appear as orphan matches.
Preserve supported existing shortcuts. Printable characters remain available
for filter input rather than becoming implicit action shortcuts.

TUI and plain mode consume the same view, selection rules, and events. Plain
mode displays headings without selectable numbers and numbers only selectable
items. It describes available actions from structured action data, rather
than stripping a TUI footer. It need not reproduce cursor hover, animation,
or terminal images. Its loading and failure messages must remain readable.

## Artist Example

Search results -> Artist performance sections -> Section themes -> existing
video selection/playback boundary.

The artist controller fetches artist details, group participation, song
credits, and anime cover URLs. It supplies the already approved Artist
layouts. The UI knows only items, submenus, details, and image states.

Every Artist submenu retains the artist identity. Performance counts are
provided by the controller and are not inferred by the UI. Group-song
selection, image loading, and Back restoration use the shared contract.
Quality deduplication and new playback actions remain later feature work.

## Migration And Verification

1. Add the data contract, state transitions, and presentation update queue.
2. Connect the existing TUI renderer and plain renderer to the contract.
3. Add the reusable navigation session and compatibility adapter.
4. Migrate Artist as the first application consumer and review its real
   terminal layout before expanding migration.
5. Migrate other flows incrementally, then remove the legacy adapter when
   no callers remain. Do not maintain two independent picker engines.

Verify nested Back restoration, tabs, duplicate labels, filtering/heading
behavior, disabled and multiple selection, auto-entry/back behavior, loading,
failure, async reordering/removal, stale updates, and plain-mode parity.
Use actual terminal integration checks for delayed covers, terminal image
replacement, resize behavior, and narrow terminals. Preserve the visual
layout and confirm input remains responsive during background work.

## Non-Goals

No provider feature expansion, quality policy changes, tracking changes,
player-screen redesign, changed progress UI, new key bindings, new visual
theme, external picker dependency, or standalone terminal application.

## Delivered Interfaces

- `ui/view_model.py`: immutable view, item, detail, image, action and tab
  records, common section filtering, and selectable-item rules.
- `ui/view_session.py`: identity-based events and commands, view stack,
  presentation position, activation generations, and coalesced update queue.
- `ui/view_presentation.py`: shared image/footer presentation state and
  four-line formatting, including persistent cover loading/failure status.
- `ui/view_picker.py`: session adapter for the existing terminal and plain
  renderers. Converts indexes only at this compatibility boundary.
- `ui/picker_state.py`: low-level position transport across renderer calls.
- `app/animethemes_artist_controller.py`: Artist data loading and application
  event handling. It supplies views rather than terminal rendering callbacks.

Applications start `ViewSession(initial_view, handler)` and pass it to
`run_view_session(flags, ui, session)`. The handler receives `UiEvent` values
after selection/actions release the picker. Focus events are debounced; their
handlers must schedule work and return promptly. Intentional playback handoff
remains application-owned and returns to the existing session.

For example, an application handler opens a supplied child with
`PushView(child_view)`. Back is handled by the shared navigation stack.
An asynchronous worker posts `UpdateItems(view_id, generation, items)` to
update existing records or uses `replace_all=True` to replace their membership
and order. Partial updates cannot resurrect a removed item.

When fetching a different query or submenu, use `BeginNavigation` and keep the
source visible until a matching `CompleteNavigation` supplies the ready/empty
destination. `replace_current=True` replaces the source rather than pushing a
child. `ReplaceView(..., new_activation=True)` is for already prepared views;
activating an incomplete loading destination through push or replacement is
rejected. Initial loading with no source is still permitted. Ordinary metadata
updates use `ReplaceView` without that flag. Completions from prior activations
are discarded. Single-result auto-entry resets for an explicit query or tab
change; ordinary metadata updates do not consume auto-entry.

Cover-resolution workers should include the original `ImageSpec` in
`UpdateItems.expected_images`. This prevents an older resolver from overwriting
a newer cover, even when their logical resource keys are the same.

Structured plain mode derives its actions from the same supplied action list
but describes actual line-mode commands: numbers, `b`, `q`, `t`, and custom
action IDs. TUI printable letters remain available for filtering. Structured
renderer restarts preserve buffered input and restore terminal settings.

## Contract Enforcement

The view model, picker state and view session are checked strictly by mypy
and are included in the Ruff lint/format gate. Runtime constructors validate
identities, nested records, flags, revisions, event payloads and update guards.
Bad internal data raises rather than being silently treated as empty content.
Application references and exit results are opaque objects, not UI-owned
business dictionaries.

Views, queued item updates and selection-event collections snapshot supplied
data. Picker/session positions remain mutable because navigation needs to
change them; supplied mark sets are copied and positions are validated at
renderer/session entry boundaries. Positive out-of-range indexes still clamp
against the current options. Negative indexes and wrong field types are errors.

An event has a known UI kind; custom application actions use `action` with a
registered action ID. Command types are explicit, and background producers
may only post update/completion commands. Controller batches containing
unsupported command types are rejected before any command is applied;
legal-transition checks still run per command. Stale generations, obsolete
requests and unsupported selections remain ignored outcomes rather than
exceptions, and do not cancel a valid pending navigation.

Action IDs identify command roles, not display-row identity. Repeated roles
such as `back` may have distinct nonempty key groups and labels, preserving
existing Left/Search and Esc/Back hints. Overlapping bindings and ambiguous
duplicates are rejected. This does not introduce provider-specific behavior.

## Remaining Migration

Regular provider search, history, downloads, AniList, details, progress,
episodes, mirrors and playback menus still use their existing picker API.
AnimeThemes search results and non-Artist tabs also retain their existing
entry flow. They can migrate individually using the shared contract; their
domain behavior must be reviewed and preserved during each migration.

The legacy adapter cannot be removed yet. Quality deduplication, new playback
actions, and opening other AnimeThemes entity tabs remain feature work.

## Continuous Terminal Lifecycle

The interactive application now retains one lazy `TerminalRuntime` across picker
and loader calls. Original terminal settings are captured once and restored at
application exit, not between screens. Nested information screens borrow the
owner; external editors and foreground local players suspend it explicitly.
Curses cleanup reasserts the owner's raw mode before returning to other screens.

Legacy state handlers run through `app/terminal_controller.py`. Their application
preparation uses detached UI/machine/config/argument snapshots. UI entry points
marshal requests to the main thread through the bounded preparation pump; workers
do not render or read terminal input. This covers work before and between picker
calls, rather than just the callable passed to an individual loading helper.
Accepted handlers commit their state; cancelled handlers cannot activate late UI
requests. The adapter retains legacy source position for a cancelled transition.

Loading input consumes non-cancelling keys and preserves escape-sequence
boundaries before handing input to a ready screen. Esc/Left cancels; Ctrl-C quits.
Cover/metadata presentation loading remains independent of transition loading.
Loading helpers enter the alternate screen before their first draw. After an
invoked UI action exits that screen, pending pump ticks must not draw, reposition
the normal-buffer cursor, clear its rows, or reopen the alternate screen. Loader
painting immediately invalidates the affected cached row, so an identical
destination footer still repaints. Cleanup invalidates only rows it painted,
without writing into the restored shell buffer. Re-entering an already active
alternate screen never clears its image.
Pickers clear the previous cover in the same output buffer as their first ready
frame, rather than clearing it during destination setup.
Information and playback frames use `terminal_images.replace_for_frame` to
delete the previous overlay and record the replacement in the same handoff.
A text-only destination drops native ownership too. Curses drops it only after
its preparation finishes, immediately before taking over the display; playback
teardown emits its deletion sequence instead of merely resetting the flag.
Loading presentation waits 150ms; work and input handling start immediately.
Only actual wait cycles may paint the status, never final result acceptance.
The presentation delay resets after each dispatched UI call returns, so time
spent using a ready picker cannot cause a loading flash on forward/back/quit.
Cached/fast preparation has no artificial delay. Debug logs include preparation
duration, abandoned work, first-frame timing and slow picker frames.

Catalog executor jobs use detached snapshots, so timeouts cannot leave a worker
mutating the visible show. Details and playback-entry preparation commit accepted
results instead of exposing partly prepared contexts. A running Python thread is
not forcibly killed on cancellation; its late result/UI requests are discarded.

Managed playback keeps its existing specialized loading/player loop rather than
being executed as a generic screen controller. New screen-controller entry stops
its loading ticker/input reader before taking over. IPC mpv cannot read the TUI's
stdin. This is a compatibility handoff, not a claim that the playback renderer
has been migrated to ViewSession. Normal search/history/downloads/AniList/details,
episode/mirror and AnimeThemes transitions share the preparation bridge.
