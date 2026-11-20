# Anyplay

Anyplay is a terminal app for finding, watching, and downloading anime and other media from multiple providers.

It is built for people who like a fast keyboard-first workflow. Search a title, pick an episode, choose a mirror if you want, and play it in your video player. It also keeps local history, resume time, downloads, custom skip data, AniSkip chapters, and provider preferences.

## What It Can Do

- Search across streaming providers from one CLI.
- Play episodes with `mpv` on desktop.
- Launch Android players from Termux, including Next Player, VLC, MPV Android, MPV-Rex, and related players.
- Pick mirrors and qualities when a provider has more than one source.
- Browse AnimeThemes songs, artists, series, studios, and public playlists.
- Play AnimeThemes audio in the terminal on desktop or Termux without a video window.
- Resume the last watched episode and timestamp.
- Download episodes with `aria2c`, `yt-dlp`, `ffmpeg`, or the built-in HLS downloader.
- Browse downloaded episodes offline.
- Sync progress with AniList when you enable it.
- Use AniSkip or your own custom opening and ending skips.
- Fall back to torrents when normal mirrors are missing, if torrent fallback is enabled.

## Install

Python 3.10 or newer is required.

The recommended install method is `pipx`.

```bash
git clone https://github.com/stashforge/anyplay.git
cd anyplay
pipx install .
```

For development, install it editable:

```bash
git clone https://github.com/stashforge/anyplay.git
cd anyplay
pipx install -e .
```

On Termux:

```bash
pkg update
pkg install python git ffmpeg
git clone https://github.com/stashforge/anyplay.git
cd anyplay
pipx install -e .
```

You also need a player. On desktop, `mpv` is the main target. On Android, install one of the supported external players.

The normal install includes `requests`, `curl-cffi`, `beautifulsoup4`, `m3u8`, and
`pycryptodome`. Individual qualities and common provider/parser support do not need
extra installation groups. Libraries load when used; playback and download tools
are checked when the selected action needs them.

Optional groups are `native-crypto` (the `cryptography` backend, including Uni
extraction), `legacy-browser` (the older Playwright fallback), and `dev` (tests and
build tools). For a fresh install with the crypto backend, use
`pipx install '.[native-crypto]'` from the checkout. Existing extra names remain
supported. Shared Linux verification uses installed Chromium and does not require
Playwright; the Android companion is a separate APK.

See [installation dependencies](docs/installation-dependencies.md) for the group
and system-tool distinctions. These instructions describe the current GitHub source.

### Android WebView Companion

Get the companion APK from
[Anyplay WebView releases](https://github.com/stashforge/anyplay-webview/releases).
AnimePahe/Kwik website access requires companion versionCode 10 or newer.
The current CLI includes companion integration; older builds may need an update.

```bash
anyplay companion pair
anyplay companion status
```

Pairing opens the companion if setup is needed. Tap **Pair**, copy the temporary
code, return to Termux and enter it at the hidden prompt. A valid saved pairing
is kept without opening setup again. No separate pairing script is required.

`status` checks the saved pairing and, if already running, authenticates the
service. It does not start the service, open the app, or change pairing. A paired
but stopped service is normal; normal provider access starts it when needed.
These commands are for Termux only and require no APK update for an existing
versionCode 10 installation.

## Quick Start

Search for a title:

```bash
anyplay search "grand blue"
```

Search with a specific provider:

```bash
anyplay search anikoto:"grand blue"
anyplay search anizone:"grand blue"
anyplay search animexin:"martial master"
```

Quotes are optional for multiword queries. Use `anyplay search animexin: -h`
for provider-specific help, or `anyplay providers` to list provider IDs.

Open your watch history:

```bash
anyplay history
```

Continue where you left off:

```bash
anyplay continue
```

Open downloaded episodes:

```bash
anyplay downloads
```

## Provider health reports

```bash
anyplay health anipm --translation both --seed 42
anyplay health all --translation both --seed 42
anyplay health anipm --translation dub --verify mpv --seed 42
anyplay health reports
anyplay health report latest
```

Checks default to Sub; `--translation both` records Sub and Dub independently.
Known unsupported translations are not applicable. `all` checks enabled providers
sequentially, uses each provider's supported depth, and saves one combined report.
An explicit provider can still be tested when disabled in normal use.

The default depth depends on the provider. `--verify extraction` stops before
media checks; HTTP checks fetch bounded samples, while `--verify mpv` silently
decodes a short sample without a window or sound. In a batch, unsupported depths
fall back to a supported shallower level, recorded separately for each provider.
Search-only catalogues never imply working playback. Movies currently sample
movie titles, not TV seasons; AnimeThemes and Torrents currently test search only.

Each provider has a shared 15-minute diagnostic budget, including both
translations, with up to three named sources sampled per episode. Large batch
runs can take a long time. Timeouts and omitted checks stay unverified or
inconclusive; local missing requirements do not mark providers broken. Ctrl+C
saves completed evidence and unstarted work, then returns without opening a pager.
Reports show scope, findings, a title digest and full evidence; `q` exits the
viewer. No normal history, watched progress or provider configuration is changed.

## Useful Commands

Play a specific episode:

```bash
anyplay search "frieren" -e 5
```

Prefer dub:

```bash
anyplay search "solo leveling" --dub
```

Choose a quality:

```bash
anyplay search "one piece" -e 1000 -q 1080p
```

Show mirrors before playing:

```bash
anyplay search "dandadan" -e 1 --sources
```

Download an episode:

```bash
anyplay search "bleach" -e 1 --download
```

Use a custom download folder:

```bash
anyplay search "bleach" -e 1 --download --download-dir ~/Videos/anyplay
```

Show available providers:

```bash
anyplay providers
```

Print recent diagnostic logs:

```bash
anyplay logs --tail 80
```

## AnimeThemes

```bash
anyplay search animethemes:"oshi no ko"
```

Use Tab to switch between Anime, Theme, Artist, Series, Studio, and Playlist
results. Songs share the same details, versions, quality, playback, and download
menus. Anime metadata and covers come from AnimeThemes' GraphQL API.

In a song's **Playback** settings, **Audio only: On** plays through command-line
mpv and keeps the terminal player screen visible. On Termux, install the terminal
player with `pkg install mpv`; audio mode does not launch an Android video app.
The mode applies on the next playback and lasts for the current song collection.
It disables video output and automatic user-script loading for that mpv process,
so visualizers cannot open a window. Normal video playback keeps your mpv scripts.
The source remains a video file; this is not an audio download or a bandwidth guarantee.

Player-screen keys:

| Key | Action |
| --- | --- |
| Q | Quit playback and return to song details |
| M | Open qualities for the current version |
| V | Open versions, when multiple versions exist |
| R | Cycle Repeat: Off, Once, Song, Collection |
| Shift+Left / Shift+Right | Previous / next song |
| Left / Right | Seek backward / forward 5 seconds in audio mode |
| Space | Pause / resume in audio mode |

Repeat, Auto-Play Next, and resume positions are collection-session settings;
listening to songs does not mark anime episodes watched. Downloads can include
the current song, selected songs, or all songs; available local copies are reused
for playback.

## Playback Settings And Menus

On desktop, **M** opens the current episode's Mirrors menu without quitting
playback. AnimeThemes uses the same shortcut for Quality and adds **V** for
Versions. The existing menus remain usable while playback continues. Esc returns
to the player screen; 30 seconds without input also returns there. If playback
ends while a menu is open, it returns to Details. Changing quality or mirror
preserves the position; changing a song version starts from the beginning.

Desktop **Playback → Fullscreen** overrides fullscreen for the current CLI
session. Before changing it, Anyplay follows your normal mpv configuration.
For a persistent default, set `fullscreen=yes` or `fullscreen=no` in `mpv.conf`.

The terminal player screen adapts to resizing. Short terminals hide the cover
and reduce optional details to keep playback status and controls visible.

## Providers

Run this command to see the providers available in your build:

```bash
anyplay providers
```

Current provider IDs include:

- `anikoto`
- `animegg`
- `animekhor`
- `animepahe`
- `anidb`
- `anizone`
- `animexin`
- `lucifer`
- `anipm`
- `animethemes`
- `reanime`
- `gotaku`
- `torrent`
- `movies`

You can set your default provider in the config file, and you can disable providers you do not want to see.

## Configuration

Open the config file:

```bash
anyplay config
```

Use a specific editor:

```bash
anyplay config --editor micro
```

Read or change one value:

```bash
anyplay config get quality
anyplay config set quality 1080p
anyplay config set provider anipm
anyplay config set player mpv
anyplay config set details_cover false
```

`details_cover` controls covers in title details/action menus (default: `true`).
Global covers must also be enabled; `--no-cover` still hides them everywhere.

Incognito sessions start with automatic next-episode downloads off. Opt in with
`--auto-download` or the session's Playback settings; the saved config stays unchanged.

The config file lives here:

```text
~/.config/anyplay/config.conf
```

Common options:

```ini
quality = 1080p
translation_type = sub
player = mpv
provider = anipm
title_language = romaji
disabled_providers =
download_dir =
auto_download_next = false
auto_delete_watched = false
torrent_fallback = true
aniskip = true
auto_skip = true
editor =
```

Anyplay keeps old valid values when it adds new config keys. If a key is missing, it writes the default value back into the config file.

Desktop playback uses your normal mpv configuration. Set `slang=eng` in
`~/.config/mpv/mpv.conf` to automatically select English subtitles only. With a
subtitle language preference set, Anyplay leaves nonmatching subtitles off but
keeps them available for manual selection, including separately attached tracks.
Without a `slang` preference, selection behavior stays unchanged. This does not
change download language settings or audio selection; burned-in captions cannot
be disabled.

## Downloads

The downloads screen is a small download manager. It can queue episodes, show progress, retry failed items, and play finished files.

Open it with:

```bash
anyplay downloads
```

Inside the downloads screen you can:

- Pause or resume downloads.
- Retry failed downloads.
- Open logs for a selected item.
- Play downloaded episodes.
- Turn session auto-download and auto-delete options on or off.

New episode MKV downloads retain opening/ending skip timings for offline playback.
Provider timings take priority; AniSkip is used only when no provider timings
are available and the episode has a known MAL identity. Existing downloads are
not automatically backfilled. Video, selected separate audio, and subtitles are
combined in the final MKV when available.

HLS downloads handle extensionless subtitle segments. When FFmpeg fails, the
item's Logs and debug log retain bounded, sanitized error details rather than
only its exit code.

## AniList

Log in:

```bash
anyplay auth login
```

Check login status:

```bash
anyplay auth status
```

Browse your AniList lists:

```bash
anyplay anilist
anyplay anilist watching
anyplay anilist completed
```

AniList sync is optional. You can keep Anyplay fully local if you prefer.

## Skips

Anyplay supports three sources of skips:

- Provider-supplied timings, when available.
- AniSkip data, when the title has matching public skip data.
- Custom skips, when you want to add your own opening or ending ranges.

Custom skips can be session-only or saved per title. Provider filters are supported, so a skip can apply to all providers or only some providers.

## Shell Completion

Generate completion scripts:

```bash
anyplay completion bash
anyplay completion zsh
anyplay completion fish
```

Install completion:

```bash
anyplay completion install bash
```

## Troubleshooting

If something fails, run with debug logging:

```bash
anyplay search "title" --debug
```

Then inspect recent logs:

```bash
anyplay logs --tail 120
```

For playback issues, check the selected mirror first. Some providers return mirrors that are slow, region-limited, or temporarily broken. Switching mirrors usually helps.

For Termux, make sure your player app is installed and that Termux can launch Android intents. If a provider works on desktop but not on Android, run with `--debug` and check the logs.

## Current TUI and Providers

Running `anyplay` opens Search. Use the home menu for History, downloaded titles,
providers and AniList. Press `?` for screen-specific shortcuts and `/` to filter.
The download manager is available with Alt+D, even with an empty queue. Saved
transfers remain available after restarting; supported downloads can resume.

Gotaku is now available (`anyplay search gotaku:"oshi no ko"`). Its encrypted
playback requires `cryptography`; install it in the same environment as Anyplay
(for pipx: `pipx inject anyplay cryptography`). AniDB uses the current Kyoto
playback API; protected playback may require optional `curl_cffi`. Senshi is no
longer included. Use `anyplay providers` for the current provider list.

## Smart Filters

Plain text filters normally. Field conditions combine with AND; comma-separated
values within a field are alternatives. Examples: `year:>=2008 genre:magic`,
`genre:"Martial Arts"`, or torrent `size:<2GB source:nyaa,knaben`.
History and Downloads also support `provider:`. Right accepts a completion.
Filters use metadata already loaded; missing fields cannot match.

### AniList filters

Lists, Search and Airing support title metadata filters and `progress`
(AniList progress). The `list` field is available only on the All tab. Press `/` to edit; Right accepts completion,
and Esc clears the filter. Example: `list:watching progress:>0`. List values
also accept AniList's stored names such as `CURRENT`. Fields use loaded data
only and apply within the visible list/tab. Missing values do not match.

### AnimeThemes search filters

Press `/` in search results. Fields follow the active tab: Anime supports
`title`, `type`, `year`, `season`, `themes`; Themes supports `title`, `artist`,
`anime`, `type` (OP/ED), `year`, `season`; Artists supports `title`, `group`;
Series and Studios support `title`; Playlists supports `title`, `owner`, `tracks`.
For example: `artist:yoasobi type:op` or `year:>=2020 season:spring`.
Only loaded metadata is used. Right accepts suggestions; commas select
alternatives, and separate conditions combine with AND.

AnimeThemes nested song lists (Anime, Artist, Series, Studio and Playlist),
including song download selection, support `title`, `artist`, `anime`, `type`
(OP/ED), `year` and `season` using the same filter editor. Missing metadata
does not match a field condition. Example: `type:ed artist:yoasobi`.

### History refresh interval

`history_refresh_minutes = 60` controls automatic History checks. Values below
30 minutes use 30; missing or blank values use 60. Updated, unchanged and failed checks all consume
the interval; failures retain the last successful data and timestamp. Manual
refresh bypasses the interval. Optional Linux scheduling uses systemd user units:

```sh
anyplay service install
anyplay service start
anyplay service status
anyplay service restart
anyplay service stop
```

Install only writes units; start enables the timer at login. A lightweight check
runs every five minutes and refreshes only due titles using the shared interval.
It does not wake a sleeping laptop or open verification popups. Status also shows
the last worker result. Restart reloads units after an update; run install again
if the Python installation moved. Desktop notifications use `notify-send` when available. Signed-in AniList
Watching and Rewatching lists are checked on the same refresh interval. Cached
AniList airing times produce “Scheduled to air” alerts (within one hour of the
scheduled time); this does not claim streaming availability. Provider-confirmed
increases produce “New episode available” notifications for tracked active titles.
Planning titles can notify on their first episode. AniList-only
titles need an existing saved provider match for availability checks; the worker
does not guess matches or add those titles to local History. Notification markers
persist across restarts and duplicate local/AniList titles are checked once.
The five-minute timer means schedule alerts can arrive a few minutes after airing.
The first successful observation establishes a baseline without backlog alerts.

Known configuration options share fallback rules: missing, null or blank values
use their defaults; numeric values below a defined minimum use that minimum;
invalid types or unsupported choices use the default. Valid `false` and `0`
values are preserved where supported. Paths and custom extension options remain
flexible. These rules apply to both `.conf` and legacy JSON configuration.

### Expanded configuration editor

`anyplay config -e --expanded` opens a temporary grouped reference with all
supported settings. Add an editor name, such as `-e nano`, to override your
preferred editor. Existing settings are active; missing settings show commented
defaults, choices and minimums. Uncomment a setting to enable it. On successful
save, changed keys update in place and new keys append to your actual file;
commenting/deleting an active setting removes that override. Your saved order
and comments are preserved. `anyplay config` prints saved entries with credentials masked; `anyplay config -e` edits the real file. Editor priority is explicit `-e <editor>`, config `editor`, VISUAL, EDITOR, then an available system editor.
Missing settings use defaults without being added by ordinary config loading.

### Ember theme

Try `ANYPLAY_THEME=ember anyplay`, or save `theme = ember` in the config.
Ember uses amber accents, ivory text and warm muted colors throughout shared screens,
while retaining the terminal background. Help colors stay fixed.

List presets with `anyplay theme list`. Use `anyplay theme preview lavender`
to open the normal TUI with a temporary theme; your saved choice is unchanged.
Lavender includes pearl text and cool violet greys as well as lilac accents.

Background presets: `anyplay theme preview midnight` (indigo/lilac) and
`anyplay theme preview hearth` (warm charcoal/amber). They color Anyplay's
terminal cell area and restore terminal colors on exit; terminal-owned outer
padding is unchanged. Terminal-padding integration remains planned.

Background themes default to internal padding of one row above/below and two
columns on each side. Set `padding = 0` to disable, `padding = 1 3` for vertical/
horizontal values, or `padding = 1 2 1 2` for top/right/bottom/left. Empty uses
the theme default. Values are terminal cells and shrink on small terminals.

The TUI centers within wide terminals at a default maximum of 120 columns,
after padding. Set `max_width = 0` for full width or choose another column
limit. Narrow terminals use their available width. Search, headers, footers,
lists and the download manager share this viewport.

## License

MIT License. See `LICENSE` for details.
