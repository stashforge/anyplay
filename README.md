# Anyplay

Find, watch, and download anime, donghua, movies, and anime themes from your terminal.
Built for Linux and Android through Termux.

## Features

- Keyboard-driven interface with themes, smart filters, and responsive layouts.
- Multiple providers, selectable mirrors and qualities, and torrent file selection.
- Watch history, resume playback, offline downloads, and a persistent download queue.
- Optional AniList tracking, opening/ending skips, and new-episode notifications.
- AnimeThemes browsing with video or terminal audio-only playback.

## Install

Requires **Python 3.10+**. Install `pipx`, `mpv`, and `ffmpeg` with your system's
package manager, then:

```sh
git clone https://github.com/stashforge/anyplay.git
cd anyplay
pipx install '.[native-crypto]'
```

The crypto extra supports Gotaku and additional native extractors. Some actions
need optional tools; Anyplay explains missing requirements when you use them.

### Termux

```sh
pkg install python git ffmpeg
pip install pipx
git clone https://github.com/stashforge/anyplay.git
cd anyplay
pipx install .
```

Install a supported Android video player for video playback. Terminal audio-only
playback needs Termux's `mpv` package.

For providers that need Android WebView access, install the
[Anyplay WebView companion](https://github.com/stashforge/anyplay-webview/releases)
and pair it once:

```sh
anyplay companion pair
```

## Start Watching

```sh
anyplay                               # Open Search
anyplay search "grand blue"           # Search the default provider
anyplay search anipm:"oshi no ko"      # Choose a provider
anyplay search animethemes:"oshi no ko"
anyplay history                       # Browse watch history
anyplay continue                      # Resume the last title
anyplay downloads                     # Open your offline library
```

Choose playback or download actions from a title's menu. Press `?` for the current
screen's shortcuts, `/` to filter, and Alt+D to open the download manager.

Filters accept plain text or conditions such as `year:>=2008 genre:magic`.
Available fields and suggestions follow the current screen.

## Make It Yours

```sh
anyplay config                        # Show saved settings; credentials hidden
anyplay config -e                     # Edit settings
anyplay config -e --expanded          # Edit with all supported settings shown
anyplay theme list
anyplay theme preview ember
```

Use `anyplay config set theme ember` to save a theme. Settings omitted from your
config use their defaults.

## Optional Integrations

```sh
anyplay auth login                    # Connect AniList
anyplay anilist                       # Browse AniList lists
anyplay service install               # Install Linux user scheduler
anyplay service start                 # Enable scheduled checks
```

The scheduler checks tracked titles for updates and can send desktop notifications.
It shares the refresh interval with History; the default is one hour.

## Help and Troubleshooting

```sh
anyplay -h
anyplay search -h
anyplay providers
anyplay health all --translation both
anyplay health report latest
anyplay logs --tail 80
```

Run a command with `--debug` when investigating a problem. Provider availability
and individual mirrors can change; health reports distinguish failed checks from
checks that could not be completed.

See the [changelog](CHANGELOG.md) for release notes.

## License

[MIT](LICENSE).
