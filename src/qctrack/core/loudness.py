"""Peak, loudness and dynamic range (F2.08-F2.10), measured by libebur128.

Feed the decoder's float blocks. libebur128 cannot change channel count or sample rate
part way through, so a chained Ogg stream that does is measured one stretch at a time
and reported as one result per stretch, in file order.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from qctrack.native.ebur128 import Ebur128, to_db


@dataclass
class LoudnessResult:
    channels: int
    rate: int
    frames: int
    #: Largest absolute sample, dBFS (``-inf`` for silence).
    sample_peak_db: float
    #: Largest 4x-oversampled peak, dBTP.
    true_peak_db: float
    #: Gated integrated loudness, LUFS (``-inf`` when nothing passes the gate).
    integrated_lufs: float
    #: Loudness range, LU.
    lra_lu: float

    @property
    def plr_db(self) -> float:
        """Peak-to-loudness ratio: true peak minus integrated loudness, in dB."""
        return self.true_peak_db - self.integrated_lufs


class LoudnessAnalyzer:
    """Stream decoded blocks through libebur128."""

    def __init__(self, rate: int) -> None:
        self._rate = rate
        self._meter: Ebur128 | None = None
        self._frames = 0
        self._done: list[LoudnessResult] = []

    def feed(self, block: npt.NDArray[np.float32], rate: int | None = None) -> None:
        """Add a block. Pass ``rate`` if the decoder's rate changed (chained Ogg)."""
        if len(block) == 0:
            return
        channels = block.shape[1]
        if rate is not None and rate != self._rate:
            self._flush()
            self._rate = rate
        if self._meter is None or self._meter.channels != channels:
            self._flush()
            self._meter = Ebur128(channels, self._rate)
        self._meter.add(block)
        self._frames += len(block)

    def _flush(self) -> None:
        meter = self._meter
        if meter is None:
            return
        try:
            self._done.append(
                LoudnessResult(
                    channels=meter.channels,
                    rate=self._rate,
                    frames=self._frames,
                    sample_peak_db=to_db(meter.sample_peak()),
                    true_peak_db=to_db(meter.true_peak()),
                    integrated_lufs=meter.loudness(),
                    lra_lu=meter.loudness_range(),
                )
            )
        finally:
            meter.close()
            self._meter = None
            self._frames = 0

    def results(self) -> list[LoudnessResult]:
        """One result per stretch with fixed layout and rate. Call once, after the last block."""
        self._flush()
        return self._done
