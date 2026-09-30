# anyplay

A modern, lightning-fast terminal media browser, player, and downloader with AniList sync, multi-track subtitle HLS proxying, and Android/Termux support.

---

## Features

- **Headless Stream Scraping**: Fetches video streams from multiple anime and media providers directly in the terminal without requiring a browser or webview.
- **Multi-Track HLS Subtitle Streaming**: Built-in RFC 8216 HLS Master Manifest generator and multi-threaded proxy. Seamlessly injects multi-language WebVTT subtitle tracks (`#EXT-X-MEDIA:TYPE=SUBTITLES`) across desktop `mpv` and mobile players (Next Player, MPV Android, VLC).
- **Unified Show Dashboard**: Modern, interactive terminal dashboard displaying AniList metadata, cover art, scores, episode counts, watched indicators, and resume playback timestamps.
- **Resume & Timestamp Playback**: Automatically saves exact watch progress per episode. Resume seamlessly from your last timestamp with a single keystroke.
- **Android & Termux Ready**: Natively launches external Android video players (`Next Player`, `MPV Android`, `MPV-Rex`, `VLC`) via `am start` intents with custom episode titles, header forwarding, and local proxying.
- **Interactive Multi-Download System**:
  - Download single episodes, custom ranges (e.g. `1-12`), selected episodes, or entire seasons.
  - Supports `aria2c`, `yt-dlp`, `ffmpeg`, and native `hls-fetch`.
- **Offline Library Management**: Browse and play downloaded episodes offline via `anyplay downloads`, with progress tracking and AniList metadata matching.
- **AniList Sync & AniSkip**:
  - Automatic watch history synchronization with AniList via OAuth (`--login`).
  - Automatic opening and ending skip markers generation for `mpv` (`--aniskip`).
- **Provider Mirror Fallbacks**: Smart stream ranker prioritizing fast, reliable CDN mirrors and falling back automatically if an embed fails.

---

## Supported Providers

| Provider Command | Description | Multi-Subtitles | Dub Support |
| :--- | :--- | :---: | :---: |
| `anyplay anikoto` | Anikoto (MegaPlay / fast 1080p CDN) | Yes | Yes |
| `anyplay miruro` | Miruro (Multi-server anime scraper) | Yes | Yes |
| `anyplay allanime` | AllAnime (Extensive anime catalog) | Yes | Yes |
| `anyplay animexin` | AnimeXin (Asian anime & Donghua) | Yes | No |
| `anyplay animegg` | AnimeGG (Multiple streaming mirrors) | Yes | Yes |
| `anyplay anizone` | AniZone (Direct video streams) | Yes | Yes |
| `anyplay movies` | Movies & TV Series provider | Yes | Yes |

*Run `anyplay [query]` directly to search using your default provider.*

---

## Installation

### Option 1: pipx (Recommended for Linux / macOS)

`pipx` installs `anyplay` in an isolated environment and exposes it globally:

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
anyplay "Frieren"

# Search using a specific provider
anyplay anikoto "Jujutsu Kaisen"
anyplay miruro "Solo Leveling"
anyplay allanime "One Piece"
```

### Direct Episode & Quality Selection

```bash
# Jump directly to Episode 5 in 1080p English Sub
anyplay anikoto "Bleach" -e 5 -q 1080p -t sub

# Binge watch continuously (auto-plays next episode)
anyplay anikoto "Chainsaw Man" -e 1 --binge

# Choose stream quality and mirrors interactively
anyplay anikoto "Naruto" -e 1 --sources
```

### Downloading Episodes

```bash
# Interactive download menu (Single, Range, Selection, All)
anyplay anikoto "DanDaDan" -e 1 --download

# Use a specific downloader (e.g. aria2c or yt-dlp)
anyplay anikoto "Attack on Titan" -e 1 --download --downloader aria2c
```

### Managing Offline Downloads

```bash
# Open the interactive offline library
anyplay downloads
```

### AniList Integration & Skipping

```bash
# Authenticate your AniList account
anyplay --login

# Enable AniSkip opening/ending markers
anyplay anikoto "Death Note" -e 1 --aniskip
```

### Extracting & Printing URLs

```bash
# Print stream master M3U8, referer headers, and all subtitle tracks
anyplay anikoto search "Slime" -e 1 --print-url
```

---

## Shell Completions

`anyplay` provides tab completion for Bash, Zsh, and Fish:

```bash
# Auto-detect shell and install completions
anyplay completion --install

# Or generate completions manually:
anyplay completion bash
anyplay completion zsh
anyplay completion fish
```

---

## CLI Options Reference

```text
anyplay [provider] [search_query] [options]

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
  config                Open config file in $EDITOR or manage key/values
  completion            Generate or install shell completion scripts

Diagnostics:
  -v, --version         Show program version
  --debug               Enable debug logs and stack trace capture
```

---

## Configuration (`config.conf`)

Configuration is stored at `~/.config/anyplay/config.conf`:

```ini
provider = anikoto
quality = 1080p
translation_type = sub
binge = false
player = mpv
download_dir = ~/Downloads/anyplay
downloader = auto
aniskip = true
auto_track = true
cover = true
anilist_token =
anilist_sort = recent
spinner = braille
```

Manage configuration directly from the CLI:
```bash
# Open configuration in your default editor ($EDITOR or nano/vim)
anyplay config
anyplay config -e micro

# Get or set individual configuration values
anyplay config get quality
anyplay config set quality 1080p
```

---

## License

Distributed under the MIT License. See `LICENSE` for more information.
