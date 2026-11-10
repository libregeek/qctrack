"""Spectrogram image for the HTML report (F3.08, REQ-CLI-11).

The decoder's blocks are mixed to mono and cut into :data:`FFT_SIZE`-sample frames. Each frame's
spectrum is read at :data:`ROWS` frequencies spaced evenly on a log axis (from :data:`F_MIN_HZ`
to Nyquist) and kept as a byte, so memory stays bounded whatever the track length: when more
than :data:`MAX_COLUMNS` columns pile up, neighbours are merged in pairs. At the end the columns
are averaged down to at most :data:`WIDTH` and drawn as a 4-bit palette PNG, which keeps a
picture near 10 KB. The image has no axes or cutoff line; the report draws those on top from
``rate`` and the measured cutoff, so a rescore needs nothing new.

Pillow is imported only by :func:`render_png`, so it is needed only with ``--spectrograms``.
Only the first sample rate of a chained stream is drawn.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FFT_SIZE = 2048
ROWS = 128
WIDTH = 512
MAX_COLUMNS = 4096
F_MIN_HZ = 50.0
#: Level range drawn (dBFS, a full-scale sine reads 0), in :data:`LEVELS` steps.
DB_MIN, DB_MAX = -110.0, -10.0
LEVELS = 16
BATCH_FRAMES = 32
_EPS = 1e-20

#: Dark to bright ramp (black, purple, red, orange, yellow, white) as colour stops.
_STOPS = (
    (0.0, (0, 0, 4)),
    (0.25, (59, 15, 112)),
    (0.5, (140, 41, 129)),
    (0.7, (222, 73, 104)),
    (0.88, (254, 159, 109)),
    (1.0, (252, 253, 191)),
)

Block = npt.NDArray[np.float32]


@dataclass
class Spectrogram:
    """A spectrogram picture and what it needs to be read."""

    png: bytes = b""
    #: Sample rate of the audio; the top row is Nyquist, the bottom row :data:`F_MIN_HZ`.
    rate: int = 0
    duration_s: float = 0.0


def row_frequencies(rate: int) -> npt.NDArray[np.float64]:
    """Centre frequency of each row, lowest first."""
    return np.geomspace(F_MIN_HZ, rate / 2, ROWS)


def row_fraction(freq_hz: float, rate: int) -> float:
    """Height of ``freq_hz`` as a fraction from the bottom (0) to the top (1) of the image."""
    top = rate / 2
    if freq_hz <= F_MIN_HZ:
        return 0.0
    return min(1.0, math.log(freq_hz / F_MIN_HZ) / math.log(top / F_MIN_HZ))


class SpectrogramAnalyzer:
    """Stream decoded blocks in; :meth:`result` gives the picture."""

    def __init__(self, rate: int) -> None:
        self._rate = rate
        self._window = np.hanning(FFT_SIZE)
        self._scale = 2.0 / float(self._window.sum())
        self._bins = np.fft.rfftfreq(FFT_SIZE, 1.0 / rate)
        # Linear interpolation from FFT bins to row frequencies, as index and weight.
        pos = np.interp(row_frequencies(rate), self._bins, np.arange(len(self._bins)))
        self._lo = np.minimum(pos.astype(int), len(self._bins) - 2)
        self._w = pos - self._lo
        self._pending = np.zeros(0, dtype=np.float32)
        self._columns: list[npt.NDArray[np.uint8]] = []
        self._merged = 1  # frames each stored column stands for
        self._frames = 0
        self._stopped = False

    def feed(self, block: Block, rate: int | None = None) -> None:
        if self._stopped or len(block) == 0:
            return
        if rate is not None and rate != self._rate:
            self._stopped = True  # a chained stream changed rate: keep what was drawn
            return
        mono = block.mean(axis=1) if block.ndim == 2 else block
        data = np.concatenate([self._pending, mono.astype(np.float32, copy=False)])
        usable = len(data) // FFT_SIZE
        if usable >= BATCH_FRAMES:
            self._transform(data[: usable * FFT_SIZE], usable)
            data = data[usable * FFT_SIZE :]
        self._pending = data

    def _transform(self, data: npt.NDArray[np.float32], count: int) -> None:
        frames = data.reshape(count, FFT_SIZE).astype(np.float64)
        mag = np.abs(np.fft.rfft(frames * self._window, axis=1)) * self._scale
        db = 10 * np.log10(mag**2 + _EPS)
        level = np.clip((db - DB_MIN) / (DB_MAX - DB_MIN), 0.0, 1.0)
        cols = level[:, self._lo] * (1 - self._w) + level[:, self._lo + 1] * self._w
        self._columns.extend(row for row in (cols * 255).astype(np.uint8))
        self._frames += count
        while len(self._columns) > MAX_COLUMNS:
            cols = self._columns
            if len(cols) % 2:
                cols.append(cols[-1])
            self._columns = [
                ((a.astype(np.uint16) + b) // 2).astype(np.uint8)
                for a, b in zip(cols[0::2], cols[1::2], strict=True)
            ]
            self._merged *= 2

    def result(self) -> Spectrogram | None:
        """The picture, or None when there was too little audio. Needs Pillow."""
        if self._pending is not None and len(self._pending) >= FFT_SIZE:
            count = len(self._pending) // FFT_SIZE
            self._transform(self._pending[: count * FFT_SIZE], count)
            self._pending = self._pending[:0]
        if not self._columns:
            return None
        grid = np.stack(self._columns, axis=1)  # rows (low to high) x columns
        width = min(WIDTH, grid.shape[1])
        edges = np.linspace(0, grid.shape[1], width + 1).astype(int)
        sums = np.add.reduceat(grid.astype(np.uint32), edges[:-1], axis=1)
        grid = (sums // np.diff(edges)).astype(np.uint8)
        duration = self._frames * FFT_SIZE / self._rate
        return Spectrogram(render_png(grid[::-1]), self._rate, duration)


def _palette() -> list[int]:
    xs = [s[0] for s in _STOPS]
    out: list[int] = []
    for i in range(LEVELS):
        x = i / (LEVELS - 1)
        out.extend(round(float(np.interp(x, xs, [s[1][c] for s in _STOPS]))) for c in range(3))
    return out


def render_png(grid: npt.NDArray[np.uint8]) -> bytes:
    """Draw a ``rows x columns`` grid of 0-255 levels (top row first) as a palette PNG."""
    import io

    from PIL import Image  # imported here so Pillow is needed only with --spectrograms

    steps = (grid.astype(np.uint16) * LEVELS // 256).astype(np.uint8)
    image = Image.frombytes("P", (steps.shape[1], steps.shape[0]), steps.tobytes())
    image.putpalette(_palette())
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True, bits=4)
    return buf.getvalue()
