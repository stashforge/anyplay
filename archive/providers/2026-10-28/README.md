# Archived Miruro and Mkissa providers — 2026-10-28

Removed at user request. This folder is outside the distributed media_cli package.
It is a restoration reference, not an enabled extension or automatic fallback.

The provider modules and their dedicated tests were moved here unchanged. SHA256.json
records the original contents. Shared files, registry metadata and dependency extras
are snapshots for reference; do not copy these over newer shared files wholesale.

To restore deliberately: review/update the provider code, move modules/tests back,
add their registry entries and requirements, and explicitly restore any needed
aliases and Mkissa crypto/clock integration. Retest before enabling. No automatic
reactivation is configured.

Shared Senshi playlist decryption and generic URL decoding remain in the CLI.
Legacy history IDs/fields are preserved; they do not activate archived providers.
Saved defaults naming either removed provider migrate to AniPM. Other settings stay.
