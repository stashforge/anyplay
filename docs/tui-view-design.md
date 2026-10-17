# Shared Terminal UI Design

Status: proposed for review; application implementation has not started.

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
