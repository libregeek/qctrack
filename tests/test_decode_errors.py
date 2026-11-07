import os
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest

from qctrack.core.decode_errors import (
    DecodeErrors,
    mp3_decode_errors,
    vorbis_decode_errors,
)
from qctrack.core.mp3scan import Mp3Scan, scan_mp3
from qctrack.core.oggscan import scan_ogg
from qctrack.native._stderr import StderrCapture
from qctrack.native.mpg123 import Mpg123Decoder, Mpg123Error
from qctrack.native.vorbisfile import VorbisDecoder, VorbisError

SAMPLES = Path(os.environ.get("QCTRACK_SAMPLES", "~/Lab/samples")).expanduser()
real = pytest.mark.skipif(not SAMPLES.is_dir(), reason="samples folder not available")


def encode(out: Path, codec: str, seconds: int = 30) -> bytes:
    """Encode a tonal, music-like signal. Unlike white noise, damage to it is noticed by the
    decoders (noise decodes almost any bit pattern without complaint)."""
    rng = np.random.default_rng(5)
    x = np.arange(44100 * seconds) / 44100
    sig = sum(
        rng.uniform(0.01, 0.05)
        * np.sin(
            2 * np.pi * rng.uniform(100, 12000) * x
            + rng.uniform(0, 6) * np.sin(2 * np.pi * rng.uniform(0.2, 3) * x)
        )
        for _ in range(30)
    )
    sig = (sig * (1 + 0.5 * np.sin(2 * np.pi * 2 * x))).astype(np.float32)
    pcm = np.stack([sig, sig * 0.8], axis=1)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "44100", "-ac", "2", "-i", "-"]
        + ["-c:a", codec, str(out)],
        input=pcm.tobytes(),
        check=True,
    )
    return out.read_bytes()


def burst(data: bytes, count: int, length: int = 16) -> bytes:
    """Invert ``count`` runs of ``length`` bytes, evenly spread over the middle of the file.

    Spread out, so that each run damages a different frame or page and is counted alone.
    """
    b = bytearray(data)
    lo, hi = len(b) // 10, len(b) * 9 // 10
    for i in range(count):
        at = lo + (hi - lo) * i // count
        for j in range(at, at + length):
            b[j] ^= 0xFF
    return bytes(b)


def mp3_errors(path: Path) -> DecodeErrors:
    scan = scan_mp3(path)
    fatal = None
    with Mpg123Decoder(path, capture_messages=True) as d:
        try:
            for _ in d.blocks():
                pass
        except Mpg123Error as e:
            fatal = str(e)
        return mp3_decode_errors(d.messages, scan, fatal, d.messages_dropped)


def ogg_errors(path: Path) -> DecodeErrors:
    scan = scan_ogg(path)
    fatal = None
    with VorbisDecoder(path) as d:
        try:
            for _ in d.blocks():
                pass
        except VorbisError as e:
            fatal = str(e)
        return vorbis_decode_errors(d.holes, scan, fatal)


@pytest.fixture(scope="module")
def mp3_bytes(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return encode(tmp_path_factory.mktemp("enc") / "clean.mp3", "libmp3lame")


@pytest.fixture(scope="module")
def ogg_bytes(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return encode(tmp_path_factory.mktemp("enc") / "clean.ogg", "libvorbis")


def put(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --- MP3 --------------------------------------------------------------------------------


def test_clean_mp3_has_no_errors(tmp_path: Path, mp3_bytes: bytes) -> None:
    r = mp3_errors(put(tmp_path, "clean.mp3", mp3_bytes))
    assert r.count == 0 and r.details == [] and r.tag_notes == 0


def test_truncated_mp3_reports_an_error(tmp_path: Path, mp3_bytes: bytes) -> None:
    r = mp3_errors(put(tmp_path, "t.mp3", mp3_bytes[: len(mp3_bytes) // 2 + 123]))
    print(f"truncated mp3: {r}")
    assert r.partial_tail and r.count == 1


def test_known_tags_are_not_errors(tmp_path: Path, mp3_bytes: bytes) -> None:
    items = b"\x04\x00\x00\x00\x00\x00\x00\x00Title\x00abcd"
    ape = items + b"APETAGEX" + struct.pack("<IIII", 2000, 32 + len(items), 1, 0) + b"\x00" * 8
    body = b"LYRICSBEGIN" + b"IND0000300" + b"LYRICSEND"
    lyrics = body + b"%06d" % len(body) + b"LYRICS200"
    id3v1 = b"TAG" + b"\x00" * 125
    for name, tail in {
        "id3v1": id3v1,
        "ape": ape,
        "lyrics": lyrics,
        "all": lyrics + ape + id3v1,
    }.items():
        path = put(tmp_path, f"{name}.mp3", mp3_bytes + tail)
        scan = scan_mp3(path)
        assert scan.unidentified_bytes == 0, name  # the scanner knows these tags
        r = mp3_errors(path)
        print(f"{name}: errors {r.count}, tag notes {r.tag_notes}")
        assert r.count == 0, (name, r)


def test_junk_after_the_audio_is_an_error(tmp_path: Path, mp3_bytes: bytes) -> None:
    # Garbage inside the audio, unlike a tag at its end, is a lost-sync event.
    mid = len(mp3_bytes) // 2
    r = mp3_errors(put(tmp_path, "junk.mp3", mp3_bytes[:mid] + b"\x00" * 777 + mp3_bytes[mid:]))
    assert r.resyncs >= 1 and r.count >= 1


def test_corrupted_mp3_counts_broken_frames(tmp_path: Path, mp3_bytes: bytes) -> None:
    few = mp3_errors(put(tmp_path, "few.mp3", burst(mp3_bytes, 2)))
    many = mp3_errors(put(tmp_path, "many.mp3", burst(mp3_bytes, 30)))
    print(f"2 bursts: {few.count}; 30 bursts: {many.count}; first detail: {many.details[:1]}")
    assert many.count > 5 and many.count > few.count and few.count <= 2
    assert many.concealed > 0 and all(isinstance(x, str) for x in many.details)
    assert len(many.details) <= 20


def test_hole_in_mp3(tmp_path: Path, mp3_bytes: bytes) -> None:
    mid = len(mp3_bytes) // 2
    r = mp3_errors(put(tmp_path, "h.mp3", mp3_bytes[:mid] + mp3_bytes[mid + 5000 :]))
    assert r.count >= 1


def test_classifier_on_messages() -> None:
    scan = Mp3Scan(size=1000, audio_start=10, audio_end=900)
    msgs = [
        "Note: ID3v2.4 rev 0 tag of 100 bytes",  # not an error
        "Note: Xing/Lame/Info header detected",
        "Note: Illegal Audio-MPEG-Header 0x50455441 at offset 901.",  # APE: one byte in
        "Note: Illegal Audio-MPEG-Header 0x4c595249 at offset 900.",  # tag at end of audio
        "Note: Illegal Audio-MPEG-Header 0x00000000 at offset 500.",  # inside the audio
        "[src/libmpg123/layer3.c:INT123_do_layer3():1948] error: dequantization failed!",
        "Note: broken frame 12, filling up with 4608 zeroes, from 4608",
    ]
    r = mp3_decode_errors(msgs, scan)
    assert (r.concealed, r.resyncs, r.tag_notes, r.partial_tail) == (1, 1, 2, False)
    assert r.count == 2 and len(r.details) == 3  # the resync, the error line, the broken frame
    # Anything the capture could not keep still counts, one concealed frame per dropped line.
    assert mp3_decode_errors([], scan, dropped=7).count == 7
    assert mp3_decode_errors([], scan, fatal="boom").count == 1


# --- Ogg Vorbis -------------------------------------------------------------------------


def test_clean_ogg_has_no_errors(tmp_path: Path, ogg_bytes: bytes) -> None:
    assert ogg_errors(put(tmp_path, "clean.ogg", ogg_bytes)).count == 0


def test_corrupted_ogg_counts_holes(tmp_path: Path, ogg_bytes: bytes) -> None:
    few = ogg_errors(put(tmp_path, "few.ogg", burst(ogg_bytes, 1)))
    many = ogg_errors(put(tmp_path, "many.ogg", burst(ogg_bytes, 8)))
    print(f"corrupted ogg: {few.concealed} hole for 1 burst, {many.concealed} for 8")
    assert few.concealed == 1 and many.concealed >= 4
    assert many.count == many.concealed + int(many.partial_tail)


def test_truncated_ogg_reports_an_error(tmp_path: Path, ogg_bytes: bytes) -> None:
    r = ogg_errors(put(tmp_path, "t.ogg", ogg_bytes[: len(ogg_bytes) // 2 + 77]))
    assert r.partial_tail and r.count >= 1


# --- the stderr capture ------------------------------------------------------------------


def test_capture_keeps_stderr_working(
    capfd: pytest.CaptureFixture[str], mp3_bytes: bytes, tmp_path: Path
) -> None:
    path = put(tmp_path, "m.mp3", burst(mp3_bytes, 30))
    os.write(2, b"before\n")
    with Mpg123Decoder(path, capture_messages=True) as d:
        for _ in d.blocks():
            os.write(2, b"between blocks\n")  # the caller's own output must still appear
        assert d.messages
    os.write(2, b"after\n")
    err = capfd.readouterr().err
    assert "before" in err and "after" in err and err.count("between blocks") > 0
    assert "broken frame" not in err and "dequantization" not in err  # captured, not leaked


def test_quiet_decoder_prints_and_stores_nothing(
    capfd: pytest.CaptureFixture[str], mp3_bytes: bytes, tmp_path: Path
) -> None:
    path = put(tmp_path, "m.mp3", burst(mp3_bytes, 30))
    with Mpg123Decoder(path) as d:
        for _ in d.blocks():
            pass
        assert d.messages == [] and d.messages_dropped == 0
    assert capfd.readouterr().err == ""


def test_capture_limits_stored_lines() -> None:
    cap = StderrCapture(max_lines=3)
    try:
        with cap.active():
            os.write(2, b"".join(b"line %d\n" % i for i in range(10)) + b"unfinished")
    finally:
        cap.close()
    # Three kept, seven counted, and the unterminated last line is flushed on close.
    assert cap.lines == ["line 0", "line 1", "line 2", "unfinished"]
    assert cap.dropped == 7


# --- real samples -----------------------------------------------------------------------


@real
@pytest.mark.parametrize(
    "name",
    ["sample.mp3", "Track9.mp3", "Sundara Ravanan.mp3", "Dooreyaanu Keralam.mp3",
     "Bajagovindam.mp3", "Keli Vipinam-M.mp3", "Bhoothaganangalkk-F.mp3", "sample.ogg"],
)  # fmt: skip
def test_real_samples_are_clean(name: str) -> None:
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"{name} not available")
    r = ogg_errors(path) if path.suffix == ".ogg" else mp3_errors(path)
    print(f"{name}: {r.count} errors, {r.tag_notes} tag notes, details {r.details[:2]}")
    assert r.count == 0
