"""AniList authentication and token persistence helpers."""

from __future__ import annotations

import getpass
from typing import Any

from ..core import storage
from ..state import paths
from ..state import secrets as secret_state

GREEN = "\033[1;32m"
RED = "\033[1;31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def save_anilist_token(cfg: dict[str, Any], token: str) -> str:
    token = storage.sanitize_token(token)
    if token and secret_state.set_secret(secret_state.ANILIST_KEY, token):
        disk_cfg = dict(cfg)
        disk_cfg["anilist_token"] = ""
        storage.save_config(disk_cfg)
        cfg["anilist_token"] = token
        return "secret"
    cfg["anilist_token"] = token or ""
    storage.save_config(cfg)
    return "config"


def clear_anilist_token(cfg: dict[str, Any]) -> None:
    secret_state.delete_secret(secret_state.ANILIST_KEY)
    cfg["anilist_token"] = ""
    storage.save_config(cfg)


def anilist_token_storage_status(cfg: dict[str, Any]) -> str:
    if secret_state.get_secret(secret_state.ANILIST_KEY):
        return "secret"
    if cfg.get("anilist_token"):
        return "config"
    return "none"


def mask_token(token: str) -> str:
    token = storage.sanitize_token(token)
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}************{token[-4:]}"


def anilist_auth_status_lines(cfg: dict[str, Any]) -> list[str]:
    raw_secret_token = secret_state.get_secret(secret_state.ANILIST_KEY)
    raw_config_token = cfg.get("anilist_token") or ""
    secret_token = storage.sanitize_token(raw_secret_token)
    config_token = storage.sanitize_token(raw_config_token)
    store = "secret" if secret_token else ("config" if config_token else "none")
    token = secret_token or config_token
    keyring_path = secret_state.backend_path()
    lines = ["AniList"]
    if token:
        lines.append(f"  {GREEN}✔{RESET} Token stored")
    else:
        lines.append(f"  {RED}✗{RESET} Not logged in")
    if store == "secret":
        lines.append("  - Storage: OS secret storage")
    elif store == "config":
        lines.append("  - Storage: private config file")
    else:
        lines.append("  - Storage: none")
    lines.append(f"  - Config: {paths.CONFIG_PATH}")
    if keyring_path:
        lines.append(f"  - Keyring: available ({keyring_path})")
    else:
        lines.append("  - Keyring: unavailable (secret-tool not found)")
    if token:
        lines.append(f"  - Token: {mask_token(token)}")
    if raw_secret_token != secret_token or raw_config_token != config_token:
        lines.append("  - Warning: token had wrapping quotes; they will be stripped on next login")
    if store == "config" and keyring_path:
        lines.append("  - Hint: run auth login again to move the token to keyring")
    return lines


def stored_anilist_token(cfg: dict[str, Any]) -> str:
    return storage.sanitize_token(
        secret_state.get_secret(secret_state.ANILIST_KEY)
        or cfg.get("anilist_token")
        or ""
    )


def anilist_auth_login_existing_lines(cfg: dict[str, Any]) -> list[str]:
    return [
        "AniList",
        f"  {GREEN}✔{RESET} Already authenticated",
        "",
        "Run `auth logout` first to replace the stored token.",
    ]


def anilist_auth_token_lines(cfg: dict[str, Any], raw: bool = False) -> list[str] | None:
    token = stored_anilist_token(cfg)
    if not token:
        return None
    if raw:
        return [token]
    return [
        f"AniList token: {mask_token(token)}",
        "Use `auth token --raw` to reveal the complete token.",
    ]


def prompt_anilist_token() -> str:
    return storage.sanitize_token(getpass.getpass(f"\n{BOLD}Paste AniList Token: {RESET}"))
