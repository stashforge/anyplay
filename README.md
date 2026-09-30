# allmanga-cli

A modern, lightning-fast terminal anime browser, player, and downloader with AniList sync, multi-track subtitle HLS proxying, and Android/Termux support.

---

## Features

- **Headless Stream Scraping**: Fetches video streams from multiple anime providers directly in the terminal without requiring a browser or webview.
- **Multi-Track HLS Subtitle Streaming**: Built-in RFC 8216 HLS Master Manifest generator and multi-threaded proxy. Seamlessly injects multi-language WebVTT subtitle tracks (`#EXT-X-MEDIA:TYPE=SUBTITLES`) across desktop `mpv` and mobile players (Next Player, MPV Android, VLC).
- **Unified Show Dashboard**: Modern, interactive terminal dashboard displaying AniList metadata, cover art, scores, episode counts, watched indicators, and resume playback timestamps.
- **Resume & Timestamp Playback**: Automatically saves exact watch progress per episode. Resume seamlessly from your last timestamp with a single keystroke.
- **Android & Termux Ready**: Natively launches external Android video players (`Next Player`, `MPV Android`, `MPV-Rex`, `VLC`) via `am start` intents with custom episode titles, header forwarding, and local proxying.
- **Interactive Multi-Download System**:
  - Download single episodes, custom ranges (e.g. `1-12`), selected episodes, or entire seasons.
  - Supports `aria2c`, `yt-dlp`, `ffmpeg`, and native `hls-fetch`.
- **Offline Library Management**: Browse and play downloaded episodes offline via `allmanga-cli downloads`, with progress tracking and AniList metadata matching.
- **AniList Sync & AniSkip**:
  - Automatic watch history synchronization with AniList via OAuth (`--login`).
  - Automatic opening and ending skip markers generation for `mpv` (`--aniskip`).
- **Provider Mirror Fallbacks**: Smart stream ranker prioritizing fast, reliable CDN mirrors and falling back automatically if an embed fails.

---

## Supported Providers

| Provider Command | Description | Multi-Subtitles | Dub Support |
| :--- | :--- | :---: | :---: |
| `allmanga-cli anikoto` | Anikoto (MegaPlay / fast 1080p CDN) | Yes | Yes |
| `allmanga-cli miruro` | Miruro (Multi-server anime scraper) | Yes | Yes |
| `allmanga-cli allanime` | AllAnime (Extensive anime catalog) | Yes | Yes |
| `allmanga-cli animexin` | AnimeXin (Asian anime & Donghua) | Yes | No |
| `allmanga-cli animegg` | AnimeGG (Multiple streaming mirrors) | Yes | Yes |
| `allmanga-cli anizone` | AniZone (Direct video streams) | Yes | Yes |
| `allmanga-cli movies` | Movies & TV Series provider | Yes | Yes |

*Run `allmanga-cli [query]` directly to search using your default provider.*

---

## Installation

### Option 1: pipx (Recommended for Linux / macOS)

`pipx` installs `allmanga-cli` in an isolated environment and exposes it globally:

```bash
# Clone the repository
git clone https://github.com/stashforge/allmanga-cli.git
cd allmanga-cli

# Install globally with optional dependencies
pipx install .[allanime,miruro]
```

### Option 2: Termux (Android)

```bash
# Update packages and install python + mpv
pkg update && pkg install python git mpv-android ffmpeg

# Clone and install
git clone https://github.com/stashforge/allmanga-cli.git
cd allmanga-cli
pip install .[allanime-termux]
```

### Option 3: Standard Virtual Environment (`venv`)

```bash
git clone https://github.com/stashforge/allmanga-cli.git
cd allmanga-cli
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install .[allanime,miruro]
```

---

## Usage Guide

### Basic Search & Stream

```bash
# Search using default provider
allmanga-cli "Frieren"

# Search using a specific provider
allmanga-cli anikoto "Jujutsu Kaisen"
allmanga-cli miruro "Solo Leveling"
allmanga-cli allanime "One Piece"
```

### Direct Episode & Quality Selection

```bash
# Jump directly to Episode 5 in 1080p English Sub
allmanga-cli anikoto "Bleach" -e 5 -q 1080p -t sub

# Binge watch continuously (auto-plays next episode)
allmanga-cli anikoto "Chainsaw Man" -e 1 --binge

# Choose stream quality and mirrors interactively
allmanga-cli anikoto "Naruto" -e 1 --sources
```

### Downloading Episodes

```bash
# Interactive download menu (Single, Range, Selection, All)
allmanga-cli anikoto "DanDaDan" -e 1 --download

# Use a specific downloader (e.g. aria2c or yt-dlp)
allmanga-cli anikoto "Attack on Titan" -e 1 --download --downloader aria2c
```

### Managing Offline Downloads

```bash
# Open the interactive offline library
allmanga-cli downloads
```

### AniList Integration & Skipping

```bash
# Authenticate your AniList account
allmanga-cli --login

# Enable AniSkip opening/ending markers
allmanga-cli anikoto "Death Note" -e 1 --aniskip
```

### Extracting & Printing URLs

```bash
# Print stream master M3U8, referer headers, and all subtitle tracks
allmanga-cli anikoto search "Slime" -e 1 --print-url
```

---

## CLI Options Reference

```text
allmanga-cli [provider] [search_query] [options]

Positional Arguments:
  provider              Optional provider: anikoto, miruro, allanime, animexin, animegg, anizone, movies
  query                 Title of the anime or show to search

Playback & Episode Options:
  -e, --episode NUM     Episode identifier (e.g., 1, 12, "OVA 1")
  -q, --quality QUALITY Video quality: best, 1080p, 720p, 480p, worst
  -t, --translation TYPE Audio language type: sub, dub
  -b, --binge           Enable continuous binge watching
  -p, --player PLAYER   Video player: mpv, rex, mpvrx, next, vlc
  --sources             Prompt interactively to choose stream mirror
  --aniskip             Enable AniSkip opening & ending chapter markers
  --print-url           Print stream links, subtitle tracks, and referer headers

Download Options:
  -d, --download        Download episode(s) instead of playing
  --downloader TOOL     Downloader engine: auto, aria2c, yt-dlp, ffmpeg, hls-fetch

Account & Library:
  --login               Log in with AniList OAuth
  downloads             Launch offline download library browser
  config                Interactively inspect and modify configuration

Diagnostics:
  -v, --version         Show program version
  --debug               Enable debug logs and stack trace capture
```

---

## Configuration (`config.json`)

Configuration is stored at `~/.config/allmanga-cli/config.json`:

```json
{
    "provider": "anikoto",
    "quality": "1080p",
    "translation_type": "sub",
    "binge": false,
    "player": "mpv",
    "download_dir": "~/Downloads/Anime",
    "downloader": "auto",
    "aniskip": true,
    "auto_track": true,
    "cover": true,
    "anilist_token": "",
    "anilist_sort": "recent",
    "spinner": "braille"
}
```

---

## License

Distributed under the MIT License. See `LICENSE` for more information.
