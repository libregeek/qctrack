"""Loading native libraries by their upstream names (no distribution-specific paths)."""

from __future__ import annotations

import ctypes
import ctypes.util


class LibraryNotFoundError(OSError):
    """A required native library could not be loaded."""


def load_library(name: str, sonames: tuple[str, ...]) -> ctypes.CDLL:
    """Load ``lib<name>`` trying each soname in turn, then the platform's search."""
    for soname in sonames:
        try:
            return ctypes.CDLL(soname)
        except OSError:
            continue
    found = ctypes.util.find_library(name)
    if found is not None:
        try:
            return ctypes.CDLL(found)
        except OSError:
            pass
    raise LibraryNotFoundError(f"cannot load native library '{name}' (tried {', '.join(sonames)})")
