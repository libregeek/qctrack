"""Terminal colours, kept to what is easy on the eyes.

Only the 8 basic ANSI foreground colours, bold and dim are used, so every terminal theme
maps them to its own comfortable palette. There are no backgrounds and no bright colours,
and meaning is never carried by colour alone (ratings keep their letters).

Colour is automatic: on for an interactive terminal, off when output is piped, when
``NO_COLOR`` is set (https://no-color.org) or ``TERM=dumb``. ``--color always|never`` overrides.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TextIO

_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "cyan": "36",
}

#: Colours by rating letter: calm for good, warm for middling, red for poor.
RATING_STYLE: Mapping[str, tuple[str, ...]] = {
    "A": ("bold", "green"),
    "B": ("green",),
    "C": ("yellow",),
    "D": ("red",),
    "F": ("bold", "red"),
}
VERDICT_STYLE: Mapping[str, tuple[str, ...]] = {
    "Pass": ("green",),
    "Warn": ("yellow",),
    "Fail": ("red",),
}


def should_color(mode: str, stream: TextIO, env: Mapping[str, str] | None = None) -> bool:
    """``mode`` is ``auto``, ``always`` or ``never``."""
    env = os.environ if env is None else env
    if mode == "always":
        return True
    if mode == "never":
        return False
    if env.get("NO_COLOR") or env.get("TERM") == "dumb":
        return False
    return stream.isatty()


class Painter:
    """Wraps text in ANSI styles, or returns it untouched when colour is off."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles or not text:
            return text
        codes = ";".join(_CODES[s] for s in styles)
        return f"\033[{codes}m{text}\033[0m"

    def rating(self, text: str, letter: str) -> str:
        return self(text, *RATING_STYLE.get(letter, ()))

    def verdict(self, text: str, verdict: str) -> str:
        return self(text, *VERDICT_STYLE.get(verdict, ()))
