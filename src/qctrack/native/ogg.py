"""Minimal ``ctypes`` binding for libogg's sync layer.

``ogg_sync_pageseek`` finds page boundaries and verifies each page's CRC-32, so a page
that comes back has passed its checksum. Everything else about a page is read from its
header bytes by the caller.
"""

from __future__ import annotations

import ctypes
from types import TracebackType
from typing import Self

from qctrack.native._loader import load_library

# sizeof(ogg_sync_state) is 32 bytes on 64-bit Linux; the structure is opaque to us.
_SYNC_BYTES = 256

_lib: ctypes.CDLL | None = None


class _OggPage(ctypes.Structure):
    _fields_ = (
        ("header", ctypes.c_void_p),
        ("header_len", ctypes.c_long),
        ("body", ctypes.c_void_p),
        ("body_len", ctypes.c_long),
    )


def _library() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    lib = load_library("ogg", ("libogg.so.0",))
    vp, ci, cl = ctypes.c_void_p, ctypes.c_int, ctypes.c_long
    sig = {
        "ogg_sync_init": (ci, [vp]),
        "ogg_sync_clear": (ci, [vp]),
        "ogg_sync_buffer": (vp, [vp, cl]),
        "ogg_sync_wrote": (ci, [vp, cl]),
        "ogg_sync_pageseek": (cl, [vp, ctypes.POINTER(_OggPage)]),
    }
    for name, (res, args) in sig.items():
        fn = getattr(lib, name)
        fn.restype = res
        fn.argtypes = args
    _lib = lib
    return lib


class OggSync:
    """Feed bytes in, get verified pages out. Use as a context manager."""

    def __init__(self) -> None:
        self._lib = _library()
        self._state: ctypes.Array[ctypes.c_char] | None = ctypes.create_string_buffer(_SYNC_BYTES)
        self._lib.ogg_sync_init(self._state)
        self._page = _OggPage()

    def feed(self, data: bytes | memoryview) -> None:
        if self._state is None:
            raise ValueError("sync state is closed")
        n = len(data)
        dst = self._lib.ogg_sync_buffer(self._state, n)
        if not dst:
            raise MemoryError("ogg_sync_buffer failed")
        ctypes.memmove(dst, bytes(data), n)
        self._lib.ogg_sync_wrote(self._state, n)

    def seek_page(self) -> tuple[int, bytes, int]:
        """Look for the next page.

        Returns ``(n, header, body_len)``. ``n > 0`` is the length of a page that passed
        its CRC (``header`` and ``body_len`` describe it). ``n < 0`` means ``-n`` bytes
        were skipped without finding a valid page. ``n == 0`` means more data is needed.
        """
        if self._state is None:
            raise ValueError("sync state is closed")
        n = int(self._lib.ogg_sync_pageseek(self._state, ctypes.byref(self._page)))
        if n > 0:
            header = ctypes.string_at(self._page.header, self._page.header_len)
            return n, header, int(self._page.body_len)
        return n, b"", 0

    def body(self) -> bytes:
        """The body of the page last returned by :meth:`seek_page`."""
        return ctypes.string_at(self._page.body, self._page.body_len)

    def close(self) -> None:
        if self._state is not None:
            self._lib.ogg_sync_clear(self._state)
            self._state = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
