"""Measurements on decoded float PCM: clipping (F2.07) and channel anomalies (F2.12).

Both analysers are fed the ``(frames, channels)`` float32 blocks yielded by the decoders,
one block at a time, so memory stays bounded whatever the file length. They only
measure; turning a measurement into Pass/Warn/Fail belongs to the scoring layer.

Definitions (quality-evaluation-matrix F2.07 and F2.12):

* A clipped run is at least :data:`CLIP_MIN_RUN` consecutive samples of one channel with
  ``|x| >= CLIP_LEVEL``. Runs continue across block boundaries. Samples outside such runs
  do not count, so a lone peak at full scale is not clipping.
* Channel statistics are RMS per channel, the L/R Pearson correlation and the RMS of the
  side signal ``(L - R) / 2`` relative to the mid signal ``(L + R) / 2``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

#: Magnitude at or above which a float sample counts as clipped.
CLIP_LEVEL = 0.999
#: Shortest run of consecutive clipped samples that counts as clipping.
CLIP_MIN_RUN = 3
#: A channel quieter than this (dBFS RMS) is dead when another channel is active.
DEAD_RMS_DB = -80.0
#: A channel louder than this (dBFS RMS) counts as active.
ACTIVE_RMS_DB = -60.0
#: L/R correlation at or above this means both channels carry the same signal.
FAKE_STEREO_CORRELATION = 0.999
#: L/R correlation below this means the channels are largely out of phase.
INVERTED_CORRELATION = -0.5

Block = npt.NDArray[np.float32]


@dataclass
class ClippingResult:
    """Clipping measurements over everything fed to a :class:`ClippingAnalyzer`."""

    samples: int = 0
    clipped_samples: int = 0
    runs: int = 0
    longest_run: int = 0
    #: Largest absolute sample value seen (can exceed 1.0 for lossy decoders).
    max_abs: float = 0.0

    @property
    def clipped_percent(self) -> float:
        return 100.0 * self.clipped_samples / self.samples if self.samples else 0.0


class ClippingAnalyzer:
    """Count runs of consecutive full-scale samples, per channel, across blocks."""

    def __init__(self) -> None:
        self._result = ClippingResult()
        self._channels = 0
        #: Length of the run still open at the end of the last block, per channel.
        self._open: npt.NDArray[np.int64] = np.zeros(0, dtype=np.int64)

    def feed(self, block: Block) -> None:
        frames, channels = block.shape
        if frames == 0:
            return
        if channels != self._channels:
            # A chained stream changed layout: runs cannot continue across it.
            self._close_runs(self._open)
            self._channels = channels
            self._open = np.zeros(channels, dtype=np.int64)
        res = self._result
        res.samples += block.size
        res.max_abs = max(res.max_abs, float(np.max(np.abs(block))))
        mask = np.abs(block) >= CLIP_LEVEL
        for c in range(channels):
            self._open[c] = self._feed_channel(mask[:, c], int(self._open[c]))

    def _feed_channel(self, mask: npt.NDArray[np.bool_], carry: int) -> int:
        """Close the runs in one channel's mask; return the length of the run left open."""
        n = len(mask)
        edges = np.diff(np.concatenate(([0], mask.view(np.int8), [0])))
        starts = np.flatnonzero(edges == 1)
        ends = np.flatnonzero(edges == -1)
        if len(starts) == 0:
            self._close_runs(np.array([carry]))
            return 0
        lengths = (ends - starts).astype(np.int64)
        if starts[0] == 0:
            lengths[0] += carry
        else:
            self._close_runs(np.array([carry]))
        open_len = 0
        if ends[-1] == n:
            open_len = int(lengths[-1])
            lengths = lengths[:-1]
        self._close_runs(lengths)
        return open_len

    def _close_runs(self, lengths: npt.NDArray[np.int64]) -> None:
        long = lengths[lengths >= CLIP_MIN_RUN]
        if len(long):
            res = self._result
            res.runs += len(long)
            res.clipped_samples += int(long.sum())
            res.longest_run = max(res.longest_run, int(long.max()))

    def result(self) -> ClippingResult:
        """Finish the open runs and return the totals. Call once, after the last block."""
        self._close_runs(self._open)
        self._open = np.zeros_like(self._open)
        return self._result


def _db(power: float) -> float:
    return 10.0 * math.log10(power) if power > 0 else -math.inf


@dataclass
class ChannelResult:
    """Channel statistics for one stretch of audio with a fixed channel count."""

    channels: int
    frames: int
    #: RMS per channel in dBFS (``-inf`` for digital silence).
    rms_db: list[float]
    #: L/R Pearson correlation; ``None`` unless stereo, or when a channel is constant.
    correlation: float | None = None
    #: Side RMS minus mid RMS in dB; ``None`` unless stereo, ``-inf`` for identical L/R.
    side_to_mid_db: float | None = None
    dead_channels: list[int] = field(default_factory=list)

    @property
    def fake_stereo(self) -> bool:
        return self.correlation is not None and self.correlation >= FAKE_STEREO_CORRELATION

    @property
    def inverted(self) -> bool:
        return self.correlation is not None and self.correlation < INVERTED_CORRELATION


class _Segment:
    """Running sums for one channel layout."""

    def __init__(self, channels: int) -> None:
        self.channels = channels
        self.frames = 0
        self.sum = np.zeros(channels)
        self.sum_sq = np.zeros(channels)
        self.sum_lr = 0.0
        self.sum_mid_sq = 0.0
        self.sum_side_sq = 0.0

    def add(self, block: Block) -> None:
        x = block.astype(np.float64)
        self.frames += len(x)
        self.sum += x.sum(axis=0)
        self.sum_sq += np.einsum("ij,ij->j", x, x)
        if self.channels == 2:
            left, right = x[:, 0], x[:, 1]
            self.sum_lr += float(left @ right)
            mid, side = (left + right) / 2, (left - right) / 2
            self.sum_mid_sq += float(mid @ mid)
            self.sum_side_sq += float(side @ side)

    def finish(self) -> ChannelResult:
        n = max(self.frames, 1)
        rms_db = [_db(float(s) / n) for s in self.sum_sq]
        result = ChannelResult(self.channels, self.frames, rms_db)
        loudest = max(rms_db)
        if loudest > ACTIVE_RMS_DB:
            result.dead_channels = [i for i, v in enumerate(rms_db) if v < DEAD_RMS_DB]
        if self.channels == 2 and self.frames:
            mean = self.sum / n
            var = self.sum_sq / n - mean**2
            if np.all(var > 1e-18):
                cov = self.sum_lr / n - mean[0] * mean[1]
                result.correlation = float(np.clip(cov / math.sqrt(var[0] * var[1]), -1.0, 1.0))
            if self.sum_mid_sq > 0:
                result.side_to_mid_db = _db(self.sum_side_sq) - _db(self.sum_mid_sq)
        return result


class ChannelAnalyzer:
    """RMS per channel, L/R correlation and side energy, across blocks.

    A chained Ogg stream can change its channel count part way through; each stretch
    with one layout is reported separately, in file order.
    """

    def __init__(self) -> None:
        self._done: list[ChannelResult] = []
        self._current: _Segment | None = None

    def feed(self, block: Block) -> None:
        frames, channels = block.shape
        if frames == 0:
            return
        if self._current is None or self._current.channels != channels:
            self._flush()
            self._current = _Segment(channels)
        self._current.add(block)

    def _flush(self) -> None:
        if self._current is not None:
            self._done.append(self._current.finish())
            self._current = None

    def results(self) -> list[ChannelResult]:
        """Return one result per channel layout. Call once, after the last block."""
        self._flush()
        return self._done
