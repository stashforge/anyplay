# Shared Terminal UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Introduce reusable structured terminal views and nested navigation, then migrate the approved Artist flow without changing its visual layout.

**Architecture:** Application controllers supply typed view snapshots and handle typed events. A UI session owns navigation and reconciles asynchronous updates by identity. A presentation adapter feeds the existing picker renderer; plain mode consumes the same model and selection policies.

**Tech Stack:** Python standard-library dataclasses, enums, queues and threading; the existing terminal picker, fuzzy matcher and poster renderer; pytest.

**Spec:** [Shared Terminal UI Design](tui-view-design.md)

## Global Constraints

- Preserve the existing image area, list, prompt/divider, and four detail lines.
- No additional columns, panels, or permanent footer rows are introduced.
- No commits, pushes, or version changes are included in this work.
- The UI has no imports from providers, application controllers, CLI argument parsing, tracking, or playback logic.
- Background work must not block the terminal input loop.
- Keep a temporary adapter for existing picker callers; do not maintain two independent picker engines.
- Quality deduplication and new playback actions remain later feature work.
- Preserve supported existing shortcuts and printable-character filtering.

## Review Focus

- Duplicate labels and reordered results must preserve identity, not select a different row.
- Late completions for a closed view or replaced image URL must not overwrite the visible state.
- Back from an automatically opened child must restore its parent without immediately reopening it.
- Heading-only filters, disabled entries, and empty/loading results must not produce invalid selections.
- Slow image resolution must display status and redraw without requiring keyboard input.

## Task 1: Structured Views And Shared Selection Rules

**Files:**
- Create `media_cli/ui/view_model.py`.
- Create `tests/test_ui_view_model.py`.

**Interfaces:**
- Immutable `DetailLines(title, secondary, metadata)` and `ImageSpec(key, state, url, message)`.
- Immutable `ViewItem(id, label, kind, enabled, hint, prefix, section_id, details, image, info, reference)`.
- Immutable `ViewTab(id, label)` and `ViewAction(id, label, keys)`.
- Immutable `ViewSpec(id, prompt, items, details, image, tabs, active_tab_id, actions, state, message, selection_policy, reverse_items)`.
- `visible_items(view, query)` uses the existing fuzzy matcher and retains relevant section headings.
- `selectable_items(items)` excludes headings and disabled items for both render modes.

- [ ] Write tests for duplicate labels with distinct IDs, duplicate-ID rejection, unknown section references, empty lists, heading-only matches, disabled actions, and single/multiple selection eligibility.
- [ ] Run `pytest -q tests/test_ui_view_model.py`; confirm failures before adding implementation.
- [ ] Implement the model, validation, and common filtering/eligibility rules without ANSI presentation or application imports.
- [ ] Run the model tests; confirm all pass.

## Task 2: Navigation, Events, And Async Updates

**Files:**
- Create `media_cli/ui/view_session.py`.
- Create `tests/test_ui_view_session.py`.

**Interfaces:**
- `ViewPosition(selected_id, query, scroll, active_tab_id, marked_ids)` stores presentation state.
- `UiEvent(kind, view_id, generation, item_id, value)` reports interactions by identity.
- Commands: `PushView`, `ReplaceView`, `PopView`, `UpdateItems`, `UpdateStatus`, and `ExitSession`.
- `ViewSession(initial_view, handle_event)` owns the stack and a thread-safe update queue.
- `session.dispatch(event)`, `session.post(command)`, and `session.drain_updates()` return whether presentation changed.
- `session.current` and `session.position` expose the active frame to the renderer.

- [ ] Write tests for three-level Back restoration including query, scroll, active tab and marked IDs.
- [ ] Add tests for reordered/deleted selected items, duplicate labels, obsolete activation updates, obsolete image updates, and updates to a hidden parent without changing the active view.
- [ ] Add tests for auto-entry only after final results, disabled-only/heading-only lists, and suppression after Back.
- [ ] Run `pytest -q tests/test_ui_view_session.py`; confirm missing implementation failures.
- [ ] Implement the session and bounded/coalesced update queue. Reject stale generations and preserve selection by item ID.
- [ ] Run model and session tests; confirm all pass.

## Task 3: Presentation And Existing Picker Integration

**Files:**
- Create `media_cli/ui/view_presentation.py`.
- Create `media_cli/ui/view_picker.py`.
- Modify `media_cli/ui/picker.py` and `media_cli/ui/plain_picker.py`.
- Reuse `media_cli/ui/poster.py`, `media_cli/ui/picker_render.py`, and `media_cli/ui/terminal_images.py`.
- Create `tests/test_ui_view_presentation.py` and `tests/test_ui_view_picker.py`.

**Interfaces:**
- `ViewPresentation(session, poster_manager)` owns item presentation objects and image state.
- `presentation.details(item_id, width)` renders the existing three detail lines and context/status/action footer.
- `presentation.poster(item_id)` reads the same image presentation object used by the footer.
- `run_view_session(flags, ui, session)` connects structured views to the existing picker and translates results into `UiEvent` values.
- Add an optional presentation-position interface to the low-level picker so filter, focus and scroll can be restored without application callbacks.
- Existing `tui_pick(...)` callers continue to work during migration. Callback/index adaptation exists only within the UI boundary.

- [ ] Write tests for exactly four detail lines, existing colors/truncation, view-level details, and item-level overrides.
- [ ] Add tests for URL resolution, downloading/rendering, ready, unavailable and failed cover states; both footer and image read the same object.
- [ ] Add tests that data/image completion invalidates presentation even when text is unchanged, and that replacing an image ignores its previous completion.
- [ ] Add tests for TUI/plain parity: headings are unnumbered and unselectable, disabled items cannot execute, multiple selection uses IDs, and tab/actions use structured data.
- [ ] Add tests for filtered selection and Back position restoration through the real picker boundary.
- [ ] Run the new tests; confirm failures before implementation.
- [ ] Implement the presentation adapter and low-level position support. Reuse existing rendering and key processing rather than copy the terminal engine.
- [ ] Remove dependence on parsing ANSI navigation footers for structured plain-mode views. Preserve the legacy path until its callers migrate.
- [ ] Run all UI model/session/presentation/picker tests and existing regression tests.

## Task 4: Artist Controller Migration

**Files:**
- Modify `media_cli/app/animethemes_artist.py` and the Artist branch in `media_cli/app/animethemes.py`.
- Reuse `media_cli/services/animethemes.py` for provider requests.
- Modify `tests/test_animethemes_artist.py`.
- Create `tests/test_artist_view_session.py`.

**Interfaces:**
- The Artist controller supplies a sections `ViewSpec` and constructs child song `ViewSpec` values from service data.
- Its event handler maps selection/focus events to service work and presentation commands.
- Background service results post generation-aware item updates through the session queue.
- The controller emits the existing playback boundary after selecting a video; no playback policy changes.

- [ ] Write tests for Artist -> performance group -> song navigation, selected artist identity in every submenu, group counts, role/co-performer credits, and cover ownership.
- [ ] Add tests for automatic entry into one artist/section, Back restoration, and failure/empty responses.
- [ ] Add a delayed-cover test with unchanged metadata that becomes ready without keyboard input.
- [ ] Run tests; confirm the controller has not yet migrated.
- [ ] Replace Artist's custom picker loops, hover timers, manual poster globals, and explicit redraw calls with the shared session API. Keep network/playback work in application/services.
- [ ] Preserve the approved displayed text and Artist layouts. Do not add new tab features or quality selection rules.
- [ ] Run Artist and shared UI tests, plus existing AnimeThemes regressions.

## Task 5: Terminal Verification And Migration Boundary

**Files:**
- Create `tests/test_ui_view_terminal.py` for actual pseudo-terminal checks.
- Update `docs/tui-view-design.md` with delivered interfaces and remaining callers.

- [ ] Add terminal tests for delayed cover completion while idle, image replacement after moving selection, nested Back restoration, resizing, and narrow layouts.
- [ ] Exercise real Kitty-protocol output and symbol-mode output; verify image payloads/frame placement rather than only a successful download.
- [ ] Verify plain-mode Artist navigation without cursor escape sequences or selectable heading numbers.
- [ ] Run the complete test suite, with local socket access when required by existing Android proxy tests.
- [ ] Check the UI package for forbidden application/provider imports in new shared modules and inspect the final diff for unrelated changes.
- [ ] Document the remaining legacy callers and the adapter removal condition. Do not migrate unrelated flows without reviewing their existing behavior first.
- [ ] Show the actual Artist terminal layouts and report concrete verification results for user review. Do not commit or push.

## Completion Criteria

Artist navigation, selection restoration, metadata, images and cover status run through the shared UI contract. Both render modes use the same identities and selection rules. Existing non-migrated flows remain functional through the current picker boundary. The UI can display another supplied submenu without provider-specific rendering code.

Migration of all remaining callers is explicitly subsequent work; the compatibility adapter must not be described as removed until no callers use it.
