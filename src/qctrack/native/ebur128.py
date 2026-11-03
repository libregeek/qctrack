"""Minimal ``ctypes`` binding for libebur128: BS.1770 loudness, loudness range, peaks.

Constants come from ``ebur128.h`` (libebur128 1.2.x). The header is not installed
alongside the library, so they are repeated here and checked by the tests.
"""

from __future__ import annotations

import ctypes
import math
from types import TracebackType
from typing import Self

import numpy as np
import numpy.typing as npt

from qctrack.native._loader import load_library

# Mode flags (EBUR128_MODE_*): each builds on the ones before it.
MODE_M = 1
MODE_S = (1 << 1) | MODE_M
MODE_I = (1 << 2) | MODE_M
MODE_LRA = (1 << 3) | MODE_S
MODE_SAMPLE_PEAK = (1 << 4) | MODE_M
MODE_TRUE_PEAK = (1 << 5) | MODE_M | MODE_SAMPLE_PEAK

_SUCCESS = 0
_ERRORS = {1: "out of memory", 2: "invalid mode", 3: "invalid channel index", 4: "no change"}

_lib: ctypes.CDLL | None = None


class Ebur128Error(Exception):
    """libebur128 returned an error code."""


def _library() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    lib = load_library("ebur128", ("libebur128.so.1",))
    vp, ci, cu = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
    pd = ctypes.POINTER(ctypes.c_double)
    sig = {
        "ebur128_init": (vp, [cu, ctypes.c_ulong, ci]),
        "ebur128_destroy": (None, [ctypes.POINTER(vp)]),
        "ebur128_add_frames_float": (ci, [vp, vp, ctypes.c_size_t]),
        "ebur128_loudness_global": (ci, [vp, pd]),
        "ebur128_loudness_range": (ci, [vp, pd]),
        "ebur128_sample_peak": (ci, [vp, cu, pd]),
        "ebur128_true_peak": (ci, [vp, cu, pd]),
    }
    for name, (res, args) in sig.items():
        fn = getattr(lib, name)
        fn.restype = res
        fn.argtypes = args
    _lib = lib
    return lib


def _check(rc: int, what: str) -> None:
    if rc != _SUCCESS:
        raise Ebur128Error(f"{what}: {_ERRORS.get(rc, f'error {rc}')}")


class Ebur128:
    """Loudness meter for one fixed channel count and sample rate."""

    def __init__(self, channels: int, rate: int, mode: int = MODE_I | MODE_LRA | MODE_TRUE_PEAK):
        self._lib = _library()
        self.channels = channels
        self._st: ctypes.c_void_p | None = ctypes.c_void_p(
            self._lib.ebur128_init(channels, rate, mode)
        )
        if not self._st.value:
            self._st = None
            raise Ebur128Error("ebur128_init failed (invalid channels, rate or mode)")

    def _handle(self) -> ctypes.c_void_p:
        if self._st is None:
            raise ValueError("meter is closed")
        return self._st

    def add(self, block: npt.NDArray[np.float32]) -> None:
        """Feed a ``(frames, channels)`` float32 block."""
        if block.ndim != 2 or block.shape[1] != self.channels:
            raise ValueError("block does not match the meter's channel count")
        data = np.ascontiguousarray(block, dtype=np.float32)
        _check(
            self._lib.ebur128_add_frames_float(self._handle(), data.ctypes.data, len(data)),
            "add_frames",
        )

    def _double(self, fn_name: str, *args: int) -> float:
        out = ctypes.c_double()
        _check(getattr(self._lib, fn_name)(self._handle(), *args, ctypes.byref(out)), fn_name)
        return out.value

    def loudness(self) -> float:
        """Gated integrated loudness in LUFS (``-inf`` when everything is gated out)."""
        return self._double("ebur128_loudness_global")

    def loudness_range(self) -> float:
        """Loudness range in LU (EBU Tech 3342)."""
        return self._double("ebur128_loudness_range")

    def sample_peak(self) -> float:
        """Largest absolute sample over all channels, linear."""
        return max(self._double("ebur128_sample_peak", c) for c in range(self.channels))

    def true_peak(self) -> float:
        """Largest 4x-oversampled peak over all channels, linear."""
        return max(self._double("ebur128_true_peak", c) for c in range(self.channels))

    def close(self) -> None:
        if self._st is not None:
            self._lib.ebur128_destroy(ctypes.byref(self._st))
            self._st = None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def to_db(linear: float) -> float:
    """Linear amplitude to dB (``-inf`` for zero)."""
    return 20.0 * math.log10(linear) if linear > 0 else -math.inf
