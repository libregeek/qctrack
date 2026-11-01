"""Minimal ``ctypes`` binding for libmpg123: decode an MP3 to float32 PCM blocks.

Settings that matter for measurement accuracy:

* ``MPG123_QUIET``: errors arrive as return codes, never as terminal output.
* ``MPG123_FORCE_FLOAT``: float samples that keep values above full scale (F2.07, F2.08).
* ``MPG123_RVA_OFF``: stored ReplayGain must never alter the measured samples.

Constants come from ``mpg123.h`` and ``fmt123.h`` (libmpg123 1.32).
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Self

import numpy as np
import numpy.typing as npt

from qctrack.native._loader import load_library

# enum mpg123_parms
_PARAM_FLAGS = 1
_PARAM_RVA = 5
# enum mpg123_param_flags
_FLAG_QUIET = 0x20
_FLAG_FORCE_FLOAT = 0x400
# enum mpg123_param_rva
_RVA_OFF = 0
# enum mpg123_enc_enum
_ENC_FLOAT_32 = 0x200
# enum mpg123_errors
_OK = 0
_ERR = -1
_NEW_FORMAT = -11
_DONE = -12

#: Decoded block size in bytes (architecture section 4.8).
BLOCK_BYTES = 64 * 1024

_lib: ctypes.CDLL | None = None


class Mpg123Error(Exception):
    """libmpg123 reported an error; ``code`` is its error number."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def _library() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    lib = load_library("mpg123", ("libmpg123.so.0",))
    vp, ci, cl, cd = ctypes.c_void_p, ctypes.c_int, ctypes.c_long, ctypes.c_double
    sig = {
        "mpg123_init": (ci, []),
        "mpg123_new": (vp, [ctypes.c_char_p, ctypes.POINTER(ci)]),
        "mpg123_delete": (None, [vp]),
        "mpg123_param2": (ci, [vp, ci, cl, cd]),
        "mpg123_getparam2": (ci, [vp, ci, ctypes.POINTER(cl), ctypes.POINTER(cd)]),
        "mpg123_open_fd": (ci, [vp, ci]),
        "mpg123_close": (ci, [vp]),
        "mpg123_getformat": (
            ci,
            [vp, ctypes.POINTER(cl), ctypes.POINTER(ci), ctypes.POINTER(ci)],
        ),
        "mpg123_read": (ci, [vp, vp, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]),
        "mpg123_errcode": (ci, [vp]),
        "mpg123_plain_strerror": (ctypes.c_char_p, [ci]),
    }
    for name, (res, args) in sig.items():
        fn = getattr(lib, name)
        fn.restype = res
        fn.argtypes = args
    if lib.mpg123_init() != _OK:
        raise Mpg123Error("mpg123_init failed", _ERR)
    _lib = lib
    return lib


def _strerror(code: int) -> str:
    msg = _library().mpg123_plain_strerror(code)
    return msg.decode("utf-8", "replace") if msg else f"error {code}"


class Mpg123Decoder:
    """Decode one MP3 file. Use as a context manager so handles are always freed."""

    def __init__(self, path: str | Path) -> None:
        self._lib = _library()
        self._mh: int | None = None
        self._fd: int | None = None
        self.rate = 0
        self.channels = 0
        #: Number of times the output format changed after the first block.
        self.format_changes = 0
        err = ctypes.c_int(0)
        mh = self._lib.mpg123_new(None, ctypes.byref(err))
        if not mh:
            raise Mpg123Error(_strerror(err.value), err.value)
        self._mh = mh
        try:
            self._configure()
            # Open the file ourselves so it is opened with read-only flags (REQ-ACC-03).
            self._fd = os.open(path, os.O_RDONLY)
            self._check(self._lib.mpg123_open_fd(mh, self._fd))
            self._refresh_format()
        except BaseException:
            self.close()
            raise

    def _configure(self) -> None:
        self._check(
            self._lib.mpg123_param2(self._mh, _PARAM_FLAGS, _FLAG_QUIET | _FLAG_FORCE_FLOAT, 0.0)
        )
        self._check(self._lib.mpg123_param2(self._mh, _PARAM_RVA, _RVA_OFF, 0.0))

    def _check(self, rc: int) -> None:
        if rc != _OK:
            code = self._lib.mpg123_errcode(self._mh) if rc == _ERR else rc
            raise Mpg123Error(_strerror(code), code)

    def _refresh_format(self) -> None:
        rate, channels, enc = ctypes.c_long(), ctypes.c_int(), ctypes.c_int()
        self._check(
            self._lib.mpg123_getformat(
                self._mh, ctypes.byref(rate), ctypes.byref(channels), ctypes.byref(enc)
            )
        )
        if enc.value != _ENC_FLOAT_32:
            raise Mpg123Error(f"unexpected sample encoding {enc.value:#x}", _ERR)
        self.rate, self.channels = rate.value, channels.value

    def param(self, which: int) -> int:
        """Read back an integer decoder parameter (used by tests)."""
        value, fvalue = ctypes.c_long(), ctypes.c_double()
        self._check(
            self._lib.mpg123_getparam2(self._mh, which, ctypes.byref(value), ctypes.byref(fvalue))
        )
        return value.value

    def blocks(self) -> Iterator[npt.NDArray[np.float32]]:
        """Yield decoded audio as ``(frames, channels)`` float32 arrays.

        Each block holds up to :data:`BLOCK_BYTES` bytes. Every yielded array is a new
        object and can be kept. Raises :class:`Mpg123Error` on a decode error.
        """
        if self._mh is None:
            raise ValueError("decoder is closed")
        first = True
        while True:
            buf = np.empty(BLOCK_BYTES // 4, dtype=np.float32)
            filled = 0
            done_total = False
            while filled < BLOCK_BYTES:
                done = ctypes.c_size_t(0)
                ptr = ctypes.c_void_p(buf.ctypes.data + filled)
                rc = self._lib.mpg123_read(self._mh, ptr, BLOCK_BYTES - filled, ctypes.byref(done))
                filled += done.value
                if rc == _NEW_FORMAT:
                    old = (self.rate, self.channels)
                    self._refresh_format()
                    if (self.rate, self.channels) != old and not first:
                        self.format_changes += 1
                        if filled:
                            break
                    continue
                if rc == _DONE:
                    done_total = True
                    break
                if rc != _OK:
                    self._check(rc)
            n = filled // 4 // max(self.channels, 1)
            if n:
                first = False
                yield buf[: n * self.channels].reshape(n, self.channels)
            if done_total:
                return

    def close(self) -> None:
        if self._mh is not None:
            self._lib.mpg123_close(self._mh)
            self._lib.mpg123_delete(self._mh)
            self._mh = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


PARAM_RVA = _PARAM_RVA
PARAM_FLAGS = _PARAM_FLAGS
FLAG_QUIET = _FLAG_QUIET
FLAG_FORCE_FLOAT = _FLAG_FORCE_FLOAT
