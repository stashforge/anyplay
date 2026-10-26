# Anyplay

Anyplay is a terminal app for finding, watching, and downloading anime and other media from multiple providers.

It is built for people who like a fast keyboard-first workflow. Search a title, pick an episode, choose a mirror if you want, and play it in your video player. It also keeps local history, resume time, downloads, custom skip data, AniSkip chapters, and provider preferences.

## What It Can Do

- Search across streaming providers from one CLI.
- Play episodes with `mpv` on desktop.
- Launch Android players from Termux, including Next Player, VLC, MPV Android, MPV-Rex, and related players.
- Pick mirrors and qualities when a provider has more than one source.
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
and system-tool distinctions. These instructions describe this checkout; the
published package may not yet include these changes.

Senshi on Termux additionally needs the paired Anyplay WebView companion (version
8 or newer). It starts the service only when source extraction needs it; it does
not require Node.js, bubblewrap or a browser window. The existing private pairing
is reused. Missing pairing or an outdated companion does not trigger automatic
pairing or fall back to an unrestricted JavaScript runtime. The service stops
after its existing idle timeout; Linux continues to use its isolated Node worker.

### Android WebView Companion

Get the companion APK from
[Anyplay WebView releases](https://github.com/stashforge/anyplay-webview/releases).
AnimePahe/Kwik website access requires companion versionCode 10 or newer.
Use a companion-enabled Anyplay build; older public CLI releases do not contain
this integration yet.

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
- `miruro`
- `mkissa`
- `reanime`
- `senshi`
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
anyplay config set provider miruro
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
provider = miruro
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

Anyplay supports two kinds of skips:

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

## License

MIT License. See `LICENSE` for details.
