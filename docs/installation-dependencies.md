# Installation dependencies

`pyproject.toml` owns the package requirements. A normal `pipx install .` from this
checkout includes the libraries used by common providers, HTTP fallbacks, playlists
and decryption. Optional imports do not imply optional installation: essential
libraries remain installed so users do not need to assemble normal playback support.

| Default Python library | Purpose |
| --- | --- |
| requests | HTTP health-check fallback |
| curl-cffi | Native HTTP transport and provider/browser-clearance requests |
| beautifulsoup4 | HTML providers and native embed parsers |
| m3u8 | Playlist qualities and segmented subtitle preparation |
| pycryptodome | Default crypto backend |

## Optional groups

| Group | Adds | Intended use |
| --- | --- | --- |
| native-crypto | cryptography | Uni extraction and alternative crypto backends |
| legacy-browser | playwright | Older browser fallback paths that still call Playwright |
| dev | Test, lint, type and build tools | Development and distribution checks |

From a fresh checkout, `pipx install '.[native-crypto]'` adds the crypto backend to
the normal install. For an existing editable development environment, run
`pipx runpip anyplay install -e '.[native-crypto]'` from that checkout. Use
`'.[dev]'` instead when preparing that environment to run tests/build checks.
No command here publishes a package or updates the Android companion.

Use `native-crypto` or `crypto-termux` for optional crypto backends. Miruro and
Mkissa/AllAnime are archived and their provider-specific installation extras
are no longer included. Existing browser compatibility extras remain available.

## System tools and Android apps

Python extras do not install these tools or apps:

- Desktop playback uses mpv; the local-file launcher also supports VLC.
- Downloads choose an installed ffmpeg/yt-dlp engine. The worker can also use
  aria2c for direct files. External track combination requires the capable ffmpeg
  route; optional aria2c acceleration is not a general requirement.
- Shared Linux challenge verification uses installed Chromium in an isolated
  profile, only after normal HTTP/clearance requests need browser recovery.
  It does not require Playwright or a downloaded Playwright browser.
- Senshi's Linux runtime requires Node.js 26+, bubblewrap and prlimit (util-linux).
  Its Termux route uses the paired Anyplay WebView APK instead of those Linux tools.
- Termux Android launches use `am`; missing setup gives action-specific guidance.

Working HTTP and browsing do not acquire all these requirements at startup.
Missing optional capabilities show scoped hints; absolute requirements reach the
owning CLI/job as actionable errors. Ordinary no-source/site failures do not turn
into installation warnings. This reference describes the development checkout,
not a claim about the currently published package or device installation.
