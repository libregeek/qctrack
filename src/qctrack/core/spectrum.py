"""Spectral cutoff, cutoff sharpness and upsampling evidence (F3.01, F3.04, F3.06).

The analyser is fed the decoder's ``(frames, channels)`` float32 blocks. Each non-silent
4096-sample frame is windowed (Hann) and transformed, and its log spectrum is added to a
bucket chosen by the frame's loudness. Memory is therefore fixed by the number of buckets,
not by the track length. At the end the "loud frames" (those within :data:`LOUD_WINDOW_DB`
of the loudest typical frames) give one averaged spectrum in dBFS, where a full-scale sine
reads 0 dB. :func:`analyse_spectrum` turns that spectrum into measurements.

Definitions used by :func:`analyse_spectrum` (this is Q-A5 in the architecture; the numbers
are starting values to tune with the labelled corpus, T10 and T11):

* The spectrum is smoothed over :data:`SMOOTH_HZ`. The steepest fall across any
  :data:`SPAN_HZ` span (``sharpness``, F3.04) marks the candidate edge.
* The *plateau* is the smoothed level just before that fall and the *floor* is the median
  level above it, so a noise floor that tilts with frequency does not matter. A *cliff*
  exists when the fall is at least :data:`MIN_DROP_DB` and the edge is not within
  :data:`EDGE_HZ` of Nyquist.
* The *cutoff* is the first frequency, scanning up from the plateau, where the spectrum
  drops :data:`EDGE_DROP_DB` below the plateau (or 80% of the way to the floor when the
  whole drop is smaller). Without a cliff the cutoff is Nyquist: nothing limits the band.
* The *fade* is how far the smoothed spectrum falls from the stretch at
  :data:`FADE_REF` of the cutoff to the plateau. Below a transcode's wall the content runs at
  close to its normal level (a fade of 2-6 dB on test signals). When a source's high
  frequencies are already faint, LAME codes nothing in its top scalefactor band (sfb21, from
  about 16 kHz at 44.1 kHz) even at 320 kbps, which also leaves a wall, but only after the
  content has faded (23-30 dB on test signals). F3.02 uses it to tell the two apart.
* The *top-band share* is the share of loud frames that carry real content in the
  2 kHz above the wall: the band level is at least :data:`TOP_CONTENT_DB` above its own
  floor (its 10th percentile over the loud frames). A file made from a lower-bitrate source
  cannot have content above that source's lowpass in any frame (0-0.4% on test signals,
  from start and end frames); LAME's sfb21 cut works frame by frame, so a genuine file
  still codes the top band in louder frames (3-40% on real music). The average spectrum
  hides this; per-frame levels in coarse :data:`TOP_BAND_HZ` bands from
  :data:`TOP_START_HZ` up are kept for it (about 100 bytes a frame).
* *Upsampling* is reported when a cliff sits just under the Nyquist frequency of a common
  lower sample rate. A genuine band-limited file can look the same (matrix F3.06: "report
  together with the cutoff"), so this is evidence and not a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

FFT_SIZE = 4096
#: Frames transformed per batch; bounds the working memory.
BATCH_FRAMES = 16
#: Width of one loudness bucket.
BUCKET_DB = 3.0
#: Frames with RMS below this (dBFS) are silence and are ignored.
LEVEL_MIN_DB = -90.0
LEVEL_MAX_DB = 6.0
#: Frames within this many dB of the 90th-percentile frame level are "loud".
LOUD_WINDOW_DB = 12.0
#: Fewer loud frames than this give no cutoff (about 0.4 s of audio at 44.1 kHz).
MIN_LOUD_FRAMES = 4

SMOOTH_HZ = 200.0
#: Spectrum below this fraction of Nyquist is not searched.
START_FRACTION = 0.2
#: Smallest fall across SPAN_HZ that counts as a cliff.
MIN_DROP_DB = 20.0
#: The cutoff is where the spectrum has fallen this far below the plateau. Far enough
#: to clear window leakage, near enough to stay at the edge of a steep wall.
EDGE_DROP_DB = 45.0
#: A cutoff this close to Nyquist counts as no cutoff.
EDGE_HZ = 500.0
SPAN_HZ = 1000.0
#: Reference stretch for the fade, as fractions of the cutoff frequency.
FADE_REF = (0.6, 0.75)
#: Per-frame top-band levels: band width and lowest band edge.
TOP_BAND_HZ = 500.0
TOP_START_HZ = 10000.0
#: A frame has content above the wall when that band is this far above its own floor.
TOP_CONTENT_DB = 20.0
#: The band examined starts this far above the wall and spans TOP_SPAN_HZ.
TOP_GAP_HZ, TOP_SPAN_HZ = 300.0, 2000.0
#: Common source sample rates tested for F3.06.
SOURCE_RATES = (11025, 12000, 16000, 22050, 24000, 32000)
#: Cutoff window, relative to the source Nyquist, that matches a resampled file.
UPSAMPLE_LOW, UPSAMPLE_HIGH = 0.92, 1.0

_EPS = 1e-20
_N_BUCKETS = int((LEVEL_MAX_DB - LEVEL_MIN_DB) / BUCKET_DB) + 1

Block = npt.NDArray[np.float32]
Spectrum = npt.NDArray[np.float64]


@dataclass
class SpectrumResult:
    """Measurements for one stretch of audio with a fixed sample rate."""

    rate: int
    #: Non-silent frames transformed, and how many of them were "loud".
    frames: int = 0
    loud_frames: int = 0
    #: False when there was too little loud audio to measure.
    measured: bool = False
    nyquist_hz: float = 0.0
    #: F3.01: highest sustained frequency; Nyquist when nothing limits the band.
    cutoff_hz: float = 0.0
    #: True when a cliff (brick wall) below Nyquist was found.
    has_cliff: bool = False
    #: Smoothed level (dBFS) just under the cutoff and above it, and the difference.
    plateau_db: float = 0.0
    floor_db: float = 0.0
    drop_db: float = 0.0
    #: F3.04: steepest fall over 1 kHz, in dB per kHz.
    sharpness_db_per_khz: float = 0.0
    #: F3.06: sample rate the content looks resampled from, or None.
    upsampled_from_hz: int | None = None
    #: dB the spectrum falls from FADE_REF of the cutoff to the plateau; None without a cliff.
    fade_db: float | None = None
    #: Percent of loud frames with real content above the wall; None without a cliff or
    #: when the band above the wall cannot be examined.
    top_band_share: float | None = None
    #: The averaged spectrum in dBFS, bin k at ``k * rate / FFT_SIZE`` Hz (None if unmeasured).
    mean_spectrum_db: Spectrum | None = field(default=None, repr=False)


def _smooth(db: Spectrum, width: int) -> Spectrum:
    padded = np.pad(db, (width // 2, width // 2), mode="edge")
    return np.convolve(padded, np.full(width, 1.0 / width), mode="valid")


def _fade(sm: Spectrum, res: float, cutoff_hz: float, plateau_db: float) -> float:
    lo = int(FADE_REF[0] * cutoff_hz / res)
    hi = max(lo + 1, int(FADE_REF[1] * cutoff_hz / res) + 1)
    return round(float(sm[lo:hi].mean()) - plateau_db, 1)


def fade_before_cutoff(r: SpectrumResult) -> float | None:
    """The stored fade, or for results saved before it existed, the fade recomputed from the
    stored spectrum. None when there is no cliff."""
    if not r.has_cliff:
        return None
    if r.fade_db is not None:
        return r.fade_db
    if r.mean_spectrum_db is None:
        return None
    res = r.rate / FFT_SIZE
    sm = _smooth(
        np.asarray(r.mean_spectrum_db, dtype=np.float64), max(3, round(SMOOTH_HZ / res)) | 1
    )
    return _fade(sm, res, r.cutoff_hz, r.plateau_db)


def _top_bins(rate: int) -> tuple[int, int, int]:
    """First bin, bin width and count of the coarse top bands at ``rate``."""
    res = rate / FFT_SIZE
    start = int(np.ceil(TOP_START_HZ / res))
    width = max(1, round(TOP_BAND_HZ / res))
    count = max(0, (FFT_SIZE // 2 + 1 - start) // width)
    return start, width, count


def top_band_share(rows: npt.NDArray[np.float32], rate: int, cutoff_hz: float) -> float | None:
    """Percent of frames (``rows``: per-frame linear power of the coarse top bands) with
    content in the bands from TOP_GAP_HZ to TOP_GAP_HZ + TOP_SPAN_HZ above ``cutoff_hz``."""
    if len(rows) == 0:
        return None
    res = rate / FFT_SIZE
    start, width, count = _top_bins(rate)
    lo_hz = cutoff_hz + TOP_GAP_HZ
    hi_hz = min(cutoff_hz + TOP_GAP_HZ + TOP_SPAN_HZ, rate / 2 - TOP_GAP_HZ)
    sel = [
        i
        for i in range(count)
        if (start + i * width) * res >= lo_hz and (start + (i + 1) * width) * res <= hi_hz
    ]
    if not sel:
        return None
    db = 10 * np.log10(rows[:, sel].astype(np.float64).mean(axis=1) + _EPS)
    floor = float(np.percentile(db, 10))
    return round(float((db > floor + TOP_CONTENT_DB).mean()) * 100, 1)


def analyse_spectrum(mean_db: Spectrum, rate: int) -> SpectrumResult:
    """Measure cutoff, sharpness and upsampling evidence on an averaged dBFS spectrum."""
    bins = len(mean_db)
    res = rate / FFT_SIZE
    nyq = rate / 2
    r = SpectrumResult(rate=rate, measured=True, nyquist_hz=nyq, cutoff_hz=nyq)
    r.mean_spectrum_db = mean_db
    width = max(3, round(SMOOTH_HZ / res)) | 1
    sm = _smooth(mean_db, width)
    start = int(START_FRACTION * (bins - 1))
    span = max(1, round(SPAN_HZ / res))
    if bins - start <= span + 1:
        return r

    falls = sm[start : bins - span] - sm[start + span :]
    edge = start + int(falls.argmax())
    r.sharpness_db_per_khz = float(max(0.0, falls.max()) * 1000.0 / (span * res))
    before = max(start, edge - round(300 / res))
    r.plateau_db = float(sm[before : edge + 1].mean())
    r.floor_db = float(np.median(sm[edge + span :]))
    r.drop_db = r.plateau_db - r.floor_db
    if falls.max() < MIN_DROP_DB or r.drop_db < MIN_DROP_DB:
        return r

    level = r.plateau_db - min(0.8 * r.drop_db, EDGE_DROP_DB)
    below = np.nonzero(sm[edge:] < level)[0]
    cut = edge + int(below[0]) if len(below) else bins - 1
    if cut * res >= nyq - EDGE_HZ:
        return r
    r.has_cliff = True
    r.cutoff_hz = float(cut * res)
    r.fade_db = _fade(sm, res, r.cutoff_hz, r.plateau_db)
    for source in SOURCE_RATES:
        if source < rate and UPSAMPLE_LOW * source / 2 <= r.cutoff_hz <= UPSAMPLE_HIGH * source / 2:
            r.upsampled_from_hz = source
            break
    return r


class SpectrumAnalyzer:
    """Stream decoded blocks into per-loudness spectrum sums."""

    def __init__(self, rate: int) -> None:
        self._rate = rate
        self._window = np.hanning(FFT_SIZE)
        self._scale = 2.0 / float(self._window.sum())
        self._pending: npt.NDArray[np.float32] | None = None
        self._seen = False
        self._done: list[SpectrumResult] = []
        self._reset()

    def _reset(self) -> None:
        self._counts = np.zeros(_N_BUCKETS, dtype=np.int64)
        self._sums = np.zeros((_N_BUCKETS, FFT_SIZE // 2 + 1), dtype=np.float64)
        #: Per non-silent frame: its loudness bucket and coarse top-band powers.
        self._top_buckets: list[npt.NDArray[np.int64]] = []
        self._top_rows: list[npt.NDArray[np.float32]] = []

    def feed(self, block: Block, rate: int | None = None) -> None:
        """Add a block. Pass ``rate`` if the decoder's rate changed (chained Ogg)."""
        if len(block) == 0:
            return
        if rate is not None and rate != self._rate:
            self._flush()
            self._rate = rate
        self._seen = True
        pending = self._pending
        if pending is not None and pending.shape[1] != block.shape[1]:
            pending = None  # channel count changed: drop under one frame of audio
        data = block if pending is None else np.concatenate([pending, block])
        usable = len(data) // FFT_SIZE
        if usable >= BATCH_FRAMES:
            self._transform(data[: usable * FFT_SIZE], usable)
            data = data[usable * FFT_SIZE :]
        self._pending = data

    def _transform(self, data: Block, count: int) -> None:
        frames = data.reshape(count, FFT_SIZE, -1).astype(np.float64)
        level = 10 * np.log10(np.mean(frames**2, axis=(1, 2)) + _EPS)
        mag = np.abs(np.fft.rfft(frames * self._window[None, :, None], axis=1)) * self._scale
        power = np.mean(mag**2, axis=2)
        db = 10 * np.log10(power + _EPS)
        audible = level >= LEVEL_MIN_DB
        buckets = np.minimum(_N_BUCKETS - 1, ((level - LEVEL_MIN_DB) / BUCKET_DB).astype(np.int64))
        for bucket, spec in zip(buckets[audible], db[audible], strict=True):
            self._counts[bucket] += 1
            self._sums[bucket] += spec
        start, width, count = _top_bins(self._rate)
        if count and audible.any():
            top = power[audible, start : start + count * width]
            rows = top.reshape(len(top), count, width).mean(axis=2).astype(np.float32)
            self._top_rows.append(rows)
            self._top_buckets.append(buckets[audible])

    def _flush(self) -> None:
        if not self._seen:
            return
        self._seen = False
        pending, self._pending = self._pending, None
        if pending is not None and len(pending) >= FFT_SIZE:
            count = len(pending) // FFT_SIZE
            self._transform(pending[: count * FFT_SIZE], count)
        counts, sums = self._counts, self._sums
        top_rows, top_buckets = self._top_rows, self._top_buckets
        self._reset()
        total = int(counts.sum())
        result = SpectrumResult(rate=self._rate, frames=total, nyquist_hz=self._rate / 2)
        result.cutoff_hz = result.nyquist_hz
        if total:
            cum = np.cumsum(counts)
            p90 = int(np.searchsorted(cum, 0.9 * total))
            first = max(0, p90 - round(LOUD_WINDOW_DB / BUCKET_DB))
            loud = int(counts[first:].sum())
            result.loud_frames = loud
            if loud >= MIN_LOUD_FRAMES:
                mean = sums[first:].sum(axis=0) / loud
                measured = analyse_spectrum(mean, self._rate)
                measured.frames, measured.loud_frames = total, loud
                if measured.has_cliff and top_rows:
                    rows = np.concatenate(top_rows)
                    is_loud = np.concatenate(top_buckets) >= first
                    measured.top_band_share = top_band_share(
                        rows[is_loud], self._rate, measured.cutoff_hz
                    )
                result = measured
        self._done.append(result)

    def results(self) -> list[SpectrumResult]:
        """One result per stretch with a fixed sample rate. Call once, after the last block."""
        self._flush()
        return self._done
