# Changelog

## 0.11.0 — 2026-11-20

### Added

- Shared search home, provider tables, responsive headers, themes, padding and centered layouts.
- Smart filters with field suggestions across search, History, Downloads, AniList and AnimeThemes.
- Persistent download queue, supported transfer resume, and a simplified download manager.
- Scheduled History and AniList checks through systemd user services and desktop notifications.
- Gotaku provider and current signed AniDB playback API support.
- Theme commands and an expanded configuration editor.

### Fixed

- History navigation stalls, repeated cover rendering and inconsistent watched-episode styling.
- Download recovery, track merging, skip timing handling and provider playback failures.
- AniList navigation, tab loading, help formatting and option validation.
- AnimeThemes direct audio selection and playback labels.

### Changed

- Retired Senshi; provider archives remain local reference material.
- Unified UI styling and configuration defaults; updated user documentation.

## 0.10.0 — 2026-11-10

### Added

- AnimeThemes browsing through its GraphQL API, including artists, series,
  studios, public playlists, and shared song details.
- AnimeThemes audio-only playback using terminal mpv on desktop and Termux.
  Player-screen controls include seeking, pause, repeat, and previous/next songs.
- Playback shortcuts for the existing quality, mirror, and version menus,
  with return-on-Esc, inactivity return, and playback-end handling.
- A session fullscreen toggle for desktop playback.

### Fixed

- HLS downloads containing extensionless WebVTT subtitle segments, including
  the reported AniPM Auto-stream failure.
- FFmpeg download diagnostics now retain sanitized error details.
- Player-screen resizing, stale cover overlays, and short-terminal overflow.
- AnimeThemes metadata, cover status, Studio playback covers, and repeated
  catalogue requests where complete data is already available.
- Removed unreachable duplicate details-menu code.

### Distribution

- Updated the dependency lockfile to include the current installation requirements.
- Synced and smoke-tested the CLI changes on Termux; the companion APK and
  existing pairing do not need to change for this update.
