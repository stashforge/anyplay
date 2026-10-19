"""Shared decoding of complete terminal keys; stream ownership stays with callers."""

from types import MappingProxyType

_KEYS = MappingProxyType(
    {
        b"\x1b": "ESC",
        b"\r": "ENTER",
        b"\n": "ENTER",
        b"\x7f": "BACKSPACE",
        b"\x08": "BACKSPACE",
        b"\x03": "CTRL_C",
        b"\x04": "CTRL_D",
        b"\x0e": "CTRL_N",
        b"\x0f": "CTRL_O",
        b"\x10": "CTRL_P",
        b"\x12": "CTRL_R",
        b"\x15": "CTRL_U",
        b"\t": "TAB",
        b"\x1b[A": "UP",
        b"\x1bOA": "UP",
        b"\x1b[B": "DOWN",
        b"\x1bOB": "DOWN",
        b"\x1b[C": "RIGHT",
        b"\x1bOC": "RIGHT",
        b"\x1b[D": "LEFT",
        b"\x1bOD": "LEFT",
        b"\x1b[Z": "SHIFT_TAB",
        b"\x1b[H": "HOME",
        b"\x1bOH": "HOME",
        b"\x1b[F": "END",
        b"\x1bOF": "END",
        b"\x1b[3~": "DELETE",
        b"\x1b[1~": "HOME",
        b"\x1b[7~": "HOME",
        b"\x1b[4~": "END",
        b"\x1b[8~": "END",
        b"\x1b[5~": "PAGE_UP",
        b"\x1b[6~": "PAGE_DOWN",
        b"\x1b[15~": "F5",
        b"\x1bd": "ALT_D",
        b"\x1bD": "ALT_D",
        b"\x1br": "ALT_R",
        b"\x1bR": "ALT_R",
    }
)


def decode_key_bytes(raw: bytes) -> str:
    known = _KEYS.get(raw)
    if known is not None:
        return known
    if raw.startswith(b"\x1b"):
        return "UNKNOWN"
    text = raw.decode("utf-8", errors="ignore")
    return text if not text or text.isprintable() else "UNKNOWN"
