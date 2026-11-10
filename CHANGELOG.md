# Changelog

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
