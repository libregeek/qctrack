"""False-positive regression corpus (todo 7.6; spec-review-false-positives.md section 11).

Each case builds one file with FFmpeg from a synthetic source and states the outcome a
correct analyser must give. Genuine files must not be rated poor; real defects and
transcodes must still be caught. Used by ``tests/test_false_positives.py`` and by
``tools/accuracy/fp_corpus.py`` (which writes the files and prints a table).

Synthetic signals show whether a rule can misfire, not how often it does on real music.
"""

from __future__ import annotations

import hashlib
import struct
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SECONDS = 15
_NOISE = "anoisesrc=d={d}:c={c}:r=44100:a=0.3:seed={s}"
_LEVEL = "volume='0.35+0.3*sin(2*PI*t/7)':eval=frame"
GOOD = frozenset("AB")
NOT_POOR = frozenset("ABC")


@dataclass(frozen=True)
class Expect:
    """What the analyser must report. ``ratings`` None means the file is not graded."""

    ratings: frozenset[str] | None
    gates: tuple[str, ...] = ()
    status: str = "Scored"
    #: Replacement action (requirements 5.9); None for files that are not scored.
    action: str | None = None


@dataclass(frozen=True)
class Case:
    name: str
    #: Finding in spec-review-false-positives.md that the case guards.
    finding: str
    expect: Expect
    build: Callable[[Path, Path], None]  # (work folder, output file)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def source(work: Path, colour: str = "pink", extra: str = "") -> Path:
    """A 15 s stereo source: independent noise per channel with slow level changes, plus an
    optional filter chain. Cached in ``work`` by its recipe."""
    key = f"src_{colour}_{hashlib.sha1(extra.encode()).hexdigest()[:10]}.wav"
    out = work / key
    if not out.exists():
        graph = f"[0][1]amerge=inputs=2,{_LEVEL}" + (f",{extra}" if extra else "")
        _ffmpeg(
            "-f", "lavfi", "-i", _NOISE.format(d=SECONDS, c=colour, s=1),
            "-f", "lavfi", "-i", _NOISE.format(d=SECONDS, c=colour, s=2),
            "-filter_complex", graph, "-c:a", "pcm_f32le", str(out),
        )  # fmt: skip
    return out


def _encode(src: Path, out: Path, *args: str) -> None:
    _ffmpeg("-i", str(src), *args, str(out))


def _mp3(*args: str, extra: str = "", colour: str = "pink") -> Callable[[Path, Path], None]:
    def build(work: Path, out: Path) -> None:
        _encode(source(work, colour, extra), out, "-c:a", "libmp3lame", *args)

    return build


def _vorbis(q: str) -> Callable[[Path, Path], None]:
    def build(work: Path, out: Path) -> None:
        _encode(source(work), out, "-c:a", "libvorbis", "-q:a", q)

    return build


def _transcode(first: Callable[[Path, Path], None], suffix: str) -> Callable[[Path, Path], None]:
    def build(work: Path, out: Path) -> None:
        tmp = work / f"{out.stem}_first{suffix}"
        first(work, tmp)
        _encode(tmp, out, "-c:a", "libmp3lame", "-b:a", "320k")

    return build


def _youtube_chain(work: Path, out: Path) -> None:
    """A dark mono source (an old film track) as a video site serves it, AAC at 128 kbps
    with a 16 kHz lowpass, then re-encoded to 256 kbps MP3 at 48 kHz: a transcode whose
    source had faded long before the wall, so the file cannot show which it is."""
    tmp = work / f"{out.stem}_aac.m4a"
    _encode(source(work, extra=_dark(8000)), tmp, "-c:a", "aac", "-b:a", "128k",
            "-cutoff", "16000", "-ac", "1")  # fmt: skip
    _encode(tmp, out, "-c:a", "libmp3lame", "-b:a", "256k", "-ar", "48000", "-ac", "2")


def _derived(
    base: Callable[[Path, Path], None], suffix: str, change: Callable[[bytes], bytes]
) -> Callable[[Path, Path], None]:
    def build(work: Path, out: Path) -> None:
        tmp = work / f"{out.stem}_base{suffix}"
        base(work, tmp)
        out.write_bytes(change(tmp.read_bytes()))

    return build


def _renamed(*args: str, suffix: str) -> Callable[[Path, Path], None]:
    def build(work: Path, out: Path) -> None:
        tmp = work / f"{out.stem}_real{suffix}"
        _encode(source(work), tmp, *args)
        out.write_bytes(tmp.read_bytes())

    return build


def ogg_crc(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else crc << 1
            crc &= 0xFFFFFFFF
    return crc


def clear_eos(ogg: bytes) -> bytes:
    """The same Ogg file with the end-of-stream flag cleared on its last page."""
    out = bytearray(ogg)
    i = out.rfind(b"OggS")
    out[i + 5] &= 0xFF & ~0x04
    out[i + 22 : i + 26] = bytes(4)
    out[i + 22 : i + 26] = struct.pack("<I", ogg_crc(bytes(out[i:])))
    return bytes(out)


def pad_after_id3(mp3: bytes, n: int = 4096) -> bytes:
    """Insert ``n`` zero bytes between the leading ID3v2 tag (if any) and the audio."""
    end = 0
    if mp3[:3] == b"ID3":
        end = 10 + (mp3[6] << 21 | mp3[7] << 14 | mp3[8] << 7 | mp3[9])
    return mp3[:end] + bytes(n) + mp3[end:]


_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)


def drop_frames_after_xing(mp3: bytes, n: int = 200) -> bytes:
    """Remove ``n`` audio frames from the start of an MPEG-1 Layer III file at a frame
    boundary and keep its Xing/Info frame unchanged, as a raw lossless split does: the
    header still counts the removed frames, and the new first frame needs reservoir data
    that is gone (finding H8)."""
    pos = 0
    if mp3[:3] == b"ID3":
        pos = 10 + (mp3[6] << 21 | mp3[7] << 14 | mp3[8] << 7 | mp3[9])
    starts = []
    while pos + 4 <= len(mp3) and mp3[pos] == 0xFF and len(starts) < n + 2:
        starts.append(pos)
        rate = (44100, 48000, 32000)[(mp3[pos + 2] >> 2) & 3]
        pos += 144_000 * _BITRATES[mp3[pos + 2] >> 4] // rate + ((mp3[pos + 2] >> 1) & 1)
    xing, first_kept = starts[0], starts[n + 1]
    return mp3[:xing] + mp3[xing : starts[1]] + mp3[first_kept:]


def drop_audio_pages(ogg: bytes) -> bytes:
    """Drop the first half of the audio pages of an Ogg Vorbis file, keeping its header pages and
    renumbering the rest, so the stream starts at a high granule position as a recording
    from mid-broadcast does (finding M3)."""
    pages, pos = [], 0
    while pos < len(ogg):
        nseg = ogg[pos + 26]
        size = 27 + nseg + sum(ogg[pos + 27 : pos + 27 + nseg])
        pages.append(bytearray(ogg[pos : pos + size]))
        pos += size
    headers = [pg for pg in pages if struct.unpack_from("<q", pg, 6)[0] == 0]
    audio = pages[len(headers) :]
    kept = headers + audio[len(audio) // 2 :]
    out = bytearray()
    for seq, pg in enumerate(kept):
        struct.pack_into("<I", pg, 18, seq)
        struct.pack_into("<I", pg, 22, 0)
        struct.pack_into("<I", pg, 22, ogg_crc(bytes(pg)))
        out += pg
    return bytes(out)


def _split_with_ffmpeg(work: Path, out: Path) -> None:
    """A 320 kbps file split at 5 s without re-encoding (``-c copy``), as splitters do."""
    tmp = work / f"{out.stem}_full.mp3"
    _mp3("-b:a", "320k")(work, tmp)
    _ffmpeg("-ss", "5", "-i", str(tmp), "-c", "copy", str(out))


def _dark(corner: int) -> str:
    """Two 2-pole lowpass filters: a gradual 24 dB/octave roll-off, no wall of its own."""
    return f"lowpass=f={corner}:poles=2,lowpass=f={corner}:poles=2"


CASES: tuple[Case, ...] = (
    # Genuine encodes: must not be rated poor.
    Case("genuine_320.mp3", "baseline", Expect(frozenset("A"), action="Keep"),
         _mp3("-b:a", "320k")),
    Case("genuine_128.mp3", "C4", Expect(GOOD, action="Replace: low bitrate"),
         _mp3("-b:a", "128k")),
    Case("genuine_v0_ffmpeg.mp3", "N3", Expect(frozenset("A"), action="Keep"),
         _mp3("-q:a", "0")),
    Case("genuine_32khz_160.mp3", "C5", Expect(GOOD, action="Replace: low bitrate"),
         _mp3("-ar", "32000", "-b:a", "160k")),
    Case("genuine_112.mp3", "H5", Expect(GOOD, action="Replace: low bitrate"),
         _mp3("-b:a", "112k")),
    Case("mono_160.mp3", "H6", Expect(GOOD, action="Keep"),
         _mp3("-ac", "1", "-b:a", "160k")),
    Case("mono_in_stereo_320.mp3", "H6", Expect(GOOD, action="Keep"),
         _mp3("-b:a", "320k", extra="pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1")),
    Case("dark_9000_320.mp3", "N1", Expect(GOOD, action="Undetermined"),
         _mp3("-b:a", "320k", extra=_dark(9000))),
    Case("dark_5000_320.mp3", "N1", Expect(GOOD, action="Undetermined"),
         _mp3("-b:a", "320k", extra=_dark(5000))),
    Case("cut_short_frame_320.mp3", "C6", Expect(GOOD, action="Keep"),
         _derived(_mp3("-b:a", "320k"), ".mp3", lambda b: b[:-200])),
    Case("zero_padding_320.mp3", "H3", Expect(frozenset("A"), action="Keep"),
         _derived(_mp3("-b:a", "320k"), ".mp3", pad_after_id3)),
    Case("vorbis_q6.ogg", "baseline", Expect(frozenset("A"), action="Keep"),
         _vorbis("6")),
    Case("vorbis_q1.ogg", "N2", Expect(NOT_POOR, action="Replace: low bitrate"),
         _vorbis("1")),
    Case("vorbis_no_eos.ogg", "C6", Expect(frozenset("A"), action="Keep"),
         _derived(_vorbis("6"), ".ogg", clear_eos)),
    # Files cut at their start: the audio from there on is intact.
    Case("split_with_ffmpeg_320.mp3", "H8", Expect(frozenset("A"), action="Keep"),
         _split_with_ffmpeg),
    Case("split_stale_xing_320.mp3", "H8", Expect(GOOD, action="Keep"),
         _derived(_mp3("-b:a", "320k"), ".mp3", drop_frames_after_xing)),
    Case("vorbis_from_mid_stream.ogg", "M3", Expect(frozenset("A"), action="Keep"),
         _derived(_vorbis("6"), ".ogg", drop_audio_pages)),
    # Real defects and transcodes: must still be caught. Since profile 2.0 a transcode's
    # sound score reflects how it sounds (its source); G4 sets the best-copy verdict.
    Case("half_320.mp3", "C6",
         Expect(frozenset("F"), ("G2",), action="Replace: damaged"),
         _derived(_mp3("-b:a", "320k"), ".mp3", lambda b: b[: len(b) // 2])),
    Case("half_vorbis.ogg", "C6",
         Expect(frozenset("F"), ("G2",), action="Replace: damaged"),
         _derived(_vorbis("6"), ".ogg", lambda b: b[: len(b) // 2])),
    Case("transcode_128_to_320.mp3", "N1",
         Expect(NOT_POOR, ("G4",), action="Replace: lossy source"),
         _transcode(_mp3("-b:a", "128k"), ".mp3")),
    Case("transcode_vorbis_q2_to_320.mp3", "N1",
         Expect(NOT_POOR, ("G4",), action="Replace: lossy source"),
         _transcode(_vorbis("2"), ".ogg")),
    # A transcode of a dark source (like Himabinduvayi, review 11.4): never G4, never Keep.
    Case("dark_film_from_video_site_256.mp3", "11.4",
         Expect(GOOD, action="Undetermined"), _youtube_chain),
    # Other formats named .mp3: never scored as broken MP3.
    Case("aac_in_mp4_named.mp3", "C8", Expect(None, ("G1",), "Unsupported"),
         _renamed("-c:a", "aac", "-b:a", "128k", suffix=".m4a")),
    Case("wav_named.mp3", "C8", Expect(None, ("G1",), "Unsupported"),
         _renamed("-c:a", "pcm_s16le", suffix=".wav")),
)  # fmt: skip
