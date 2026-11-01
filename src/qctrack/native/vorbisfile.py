"""Minimal ``ctypes`` binding for libvorbisfile: decode Ogg Vorbis to float32 PCM blocks.

``ov_read_float`` returns floats that keep values above full scale (F2.07, F2.08).
Constants come from ``vorbis/codec.h`` (libvorbis 1.3.7).
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

#: Decoded block size in bytes (architecture section 4.8).
BLOCK_BYTES = 64 * 1024

OV_HOLE = -3
_ERRORS = {
    -1: "false or no data available",
    -2: "end of file",
    -3: "interruption in the data (hole)",
    -128: "read error",
    -129: "internal fault",
    -130: "not implemented",
    -131: "invalid argument",
    -132: "not a Vorbis stream",
    -133: "bad or missing header",
    -134: "unsupported Vorbis version",
    -135: "not an audio stream",
    -136: "bad packet",
    -137: "bad link",
    -138: "stream is not seekable",
}

# sizeof(OggVorbis_File) is about 1 KB on 64-bit Linux; this upper bound is safe because
# the structure is opaque to us and initialised by ov_fopen.
_VF_BYTES = 4096

_lib: ctypes.CDLL | None = None


class VorbisError(Exception):
    """libvorbisfile reported an error; ``code`` is its OV_* error number."""

    def __init__(self, code: int) -> None:
        super().__init__(_ERRORS.get(code, f"error {code}"))
        self.code = code


class _VorbisInfo(ctypes.Structure):
    # Leading fields of vorbis_info; the bitrate hints are not needed here.
    _fields_ = (("version", ctypes.c_int), ("channels", ctypes.c_int), ("rate", ctypes.c_long))


def _library() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    lib = load_library("vorbisfile", ("libvorbisfile.so.3",))
    vp, ci = ctypes.c_void_p, ctypes.c_int
    float_pp = ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(ctypes.c_float)))
    sig = {
        "ov_fopen": (ci, [ctypes.c_char_p, vp]),
        "ov_clear": (ci, [vp]),
        "ov_info": (ctypes.POINTER(_VorbisInfo), [vp, ci]),
        "ov_read_float": (ctypes.c_long, [vp, float_pp, ci, ctypes.POINTER(ci)]),
    }
    for name, (res, args) in sig.items():
        fn = getattr(lib, name)
        fn.restype = res
        fn.argtypes = args
    _lib = lib
    return lib


class VorbisDecoder:
    """Decode one Ogg Vorbis file. Use as a context manager so the handle is freed."""

    def __init__(self, path: str | Path) -> None:
        self._lib = _library()
        self._vf: ctypes.Array[ctypes.c_char] | None = ctypes.create_string_buffer(_VF_BYTES)
        self.rate = 0
        self.channels = 0
        #: Number of interruptions (OV_HOLE) reported while decoding.
        self.holes = 0
        #: Number of times the stream format changed (chained streams).
        self.format_changes = 0
        # ov_fopen opens the file itself with fopen(path, "rb"): read-only (REQ-ACC-03).
        rc = self._lib.ov_fopen(os.fsencode(path), self._vf)
        if rc != 0:
            self._vf = None  # ov_fopen frees its own state on failure
            if not os.path.exists(path):  # fopen failure comes back as a generic code
                raise FileNotFoundError(path)
            raise VorbisError(rc)
        self._refresh_format()

    def _query_format(self) -> tuple[int, int]:
        """Return ``(rate, channels)`` of the link currently being decoded."""
        info = self._lib.ov_info(self._vf, -1)
        if not info:
            raise VorbisError(-131)
        return int(info.contents.rate), int(info.contents.channels)

    def _refresh_format(self) -> None:
        self.rate, self.channels = self._query_format()

    def blocks(self) -> Iterator[npt.NDArray[np.float32]]:
        """Yield decoded audio as ``(frames, channels)`` float32 arrays.

        Libvorbis hands back small chunks; they are gathered here into blocks of up to
        :data:`BLOCK_BYTES` bytes, which is much faster to process with NumPy. Every
        yielded array is a new object. :attr:`rate` and :attr:`channels` always describe
        the block that was yielded last, so they are correct for chained streams too.
        Interruptions are counted in :attr:`holes` and decoding continues;
        any other error raises :class:`VorbisError`.
        """
        if self._vf is None:
            raise ValueError("decoder is closed")
        pcm = ctypes.POINTER(ctypes.POINTER(ctypes.c_float))()
        link = ctypes.c_int(0)
        last_link = 0
        rate, channels = self._query_format()
        capacity = BLOCK_BYTES // 4 // channels
        block = np.empty((capacity, channels), dtype=np.float32)
        filled = 0
        while True:
            n = self._lib.ov_read_float(
                self._vf, ctypes.byref(pcm), capacity - filled, ctypes.byref(link)
            )
            if n == 0:
                break
            if n == OV_HOLE:
                self.holes += 1
                continue
            if n < 0:
                raise VorbisError(int(n))
            if link.value != last_link:
                # A chained stream may change rate or channels; blocks never mix formats.
                last_link = link.value
                new_rate, new_channels = self._query_format()
                if (new_rate, new_channels) != (rate, channels):
                    self.format_changes += 1
                    if filled:
                        self.rate, self.channels = rate, channels
                        yield block[:filled]
                    rate, channels = new_rate, new_channels
                    capacity = BLOCK_BYTES // 4 // channels
                    block = np.empty((capacity, channels), dtype=np.float32)
                    filled = 0
            chunks = [np.ctypeslib.as_array(pcm[ch], shape=(n,)) for ch in range(channels)]
            done = 0
            while done < n:
                take = min(n - done, capacity - filled)
                for ch in range(channels):
                    block[filled : filled + take, ch] = chunks[ch][done : done + take]
                filled += take
                done += take
                if filled == capacity:
                    self.rate, self.channels = rate, channels
                    yield block
                    block = np.empty((capacity, channels), dtype=np.float32)
                    filled = 0
        if filled:
            self.rate, self.channels = rate, channels
            yield block[:filled]

    def close(self) -> None:
        if self._vf is not None:
            self._lib.ov_clear(self._vf)
            self._vf = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
