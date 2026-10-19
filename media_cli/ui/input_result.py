"""Explicit native input consumption alongside legacy boolean transition hooks."""

from enum import Enum


class KeyResult(Enum):
    CONSUMED = "consumed"
