"""Tags and stored loudness gain (F1.09).

Tag contents never affect audio quality, so this module only reports: which tag
formats are present, the common fields, any stored ReplayGain values, and structural
problems (malformed, duplicate or foreign tags). Parsing is done by Mutagen; presence
and layout are read from the raw bytes so a tag Mutagen quietly repairs is still seen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mutagen
from mutagen.apev2 import APEv2
from mutagen.id3 import ID3
from mutagen.oggvorbis import OggVorbis

from qctrack.core.detect import Format
from qctrack.core.mp3scan import LameTag

# Single-valued fields: a second value is a duplicate. Vorbis comments allow repeats for
# fields such as ARTIST, so only these are checked.
_SINGLE_VORBIS = ("TITLE", "ALBUM", "TRACKNUMBER", "DATE")
_SINGLE_ID3 = ("TIT2", "TALB", "TRCK", "TDRC")
_MAX_TAGS = 8
_GAIN_KEYS = {
    "REPLAYGAIN_TRACK_GAIN": ("track", "gain"),
    "REPLAYGAIN_TRACK_PEAK": ("track", "peak"),
    "REPLAYGAIN_ALBUM_GAIN": ("album", "gain"),
    "REPLAYGAIN_ALBUM_PEAK": ("album", "peak"),
}
_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?|[-+]?\.\d+")


@dataclass
class StoredGain:
    """One stored loudness value, with where it came from."""

    source: str  # e.g. "ID3v2 TXXX", "ID3v2 RVA2", "APEv2", "Vorbis comment", "LAME tag"
    scope: str  # "track" or "album"
    gain_db: float | None = None
    peak: float | None = None


@dataclass
class TagInfo:
    #: Tag formats found in the file, e.g. ``["ID3v2.3", "ID3v1", "APEv2"]``.
    formats: list[str] = field(default_factory=list)
    title: str = ""
    artist: str = ""
    album: str = ""
    #: Track number without the total, e.g. "3" for "3/12".
    track_number: str = ""
    #: All text fields, keyed by tag key (frame ID or Vorbis field name). Pictures and other
    #: binary data are left out; see :attr:`pictures`.
    fields: dict[str, list[str]] = field(default_factory=dict)
    pictures: int = 0
    picture_bytes: int = 0
    gains: list[StoredGain] = field(default_factory=list)
    #: Structural problems, each a short sentence.
    malformed: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    #: Tag formats that do not normally belong with the audio format.
    foreign: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        return self.malformed + self.duplicates + [f"foreign tag: {f}" for f in self.foreign]


def lame_gains(lame: LameTag) -> list[StoredGain]:
    """Stored gain values from a LAME tag (radio is track gain, audiophile is album gain)."""
    out: list[StoredGain] = []
    if lame.radio_gain_db is not None:
        out.append(StoredGain("LAME tag", "track", lame.radio_gain_db, lame.peak or None))
    if lame.audiophile_gain_db is not None:
        out.append(StoredGain("LAME tag", "album", lame.audiophile_gain_db, None))
    return out


def parse_gain_db(text: str) -> float | None:
    """Parse ``"-6.50 dB"`` style text; None if it has no number."""
    m = _NUMBER.search(text)
    return float(m.group()) if m else None


def read_tags(path: str | Path, fmt: Format) -> TagInfo:
    """Read tags from an MP3 or Ogg Vorbis file (read-only)."""
    info = TagInfo()
    if fmt is Format.MP3:
        _read_mp3(path, info)
    elif fmt is Format.VORBIS:
        _read_vorbis(path, info)
    return info


# --- MP3 --------------------------------------------------------------------------------


def _leading_id3v2(path: str | Path) -> list[tuple[int, int]]:
    """Version ``(major, minor)`` of each ID3v2 tag at the start of the file."""
    found: list[tuple[int, int]] = []
    with open(path, "rb") as f:
        pos = 0
        for _ in range(_MAX_TAGS):
            f.seek(pos)
            hdr = f.read(10)
            if len(hdr) < 10 or hdr[:3] != b"ID3" or any(b & 0x80 for b in hdr[6:10]):
                break
            found.append((hdr[3], hdr[4]))
            size = (hdr[6] << 21) | (hdr[7] << 14) | (hdr[8] << 7) | hdr[9]
            pos += 10 + size + (10 if hdr[5] & 0x10 else 0)
    return found


def _tail_markers(path: str | Path) -> tuple[bool, bool]:
    """Whether an ID3v1 tag and an APE tag sit at the end of the file."""
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        has_v1 = False
        if size >= 128:
            f.seek(size - 128)
            has_v1 = f.read(3) == b"TAG"
        end = size - 128 if has_v1 else size
        has_ape = False
        if end >= 32:
            f.seek(end - 32)
            has_ape = f.read(8) == b"APETAGEX"
    return has_v1, has_ape


def _read_mp3(path: str | Path, info: TagInfo) -> None:
    versions = _leading_id3v2(path)
    has_v1, has_ape = _tail_markers(path)
    info.formats = [f"ID3v2.{major}" for major, _ in versions]
    if has_v1:
        info.formats.append("ID3v1")
    if has_ape:
        info.formats.append("APEv2")
    if len(versions) > 1:
        info.duplicates.append(f"{len(versions)} ID3v2 tags in one file")
    if versions or has_v1:
        try:
            tag = ID3(path)
        except mutagen.MutagenError as e:
            info.malformed.append(f"ID3 tag could not be read ({e})")
        else:
            _from_id3(tag, info)
    if has_ape:
        try:
            ape = APEv2(path)
        except mutagen.MutagenError as e:
            info.malformed.append(f"APEv2 tag could not be read ({e})")
        else:
            _from_ape(ape, info)


def _from_id3(tag: ID3, info: TagInfo) -> None:
    gains: dict[tuple[str, str], StoredGain] = {}
    for key in tag:
        frames = tag.getall(key)
        if key.startswith("APIC"):
            info.pictures += len(frames)
            info.picture_bytes += sum(len(f.data) for f in frames)
            continue
        if key.startswith("TXXX:"):
            desc = key[5:].upper()
            text = " ".join(str(t) for f in frames for t in f.text)
            if desc in _GAIN_KEYS:
                scope, kind = _GAIN_KEYS[desc]
                g = gains.setdefault((scope, "ID3v2 TXXX"), StoredGain("ID3v2 TXXX", scope))
                _set_gain(g, kind, text, info)
            info.fields.setdefault(key, []).append(text)
            continue
        if key.startswith("RVA2:"):
            for f in frames:
                if f.channel != 1:  # master volume only
                    continue
                scope = "album" if f.desc.lower() == "album" else "track"
                gains[(scope, "ID3v2 RVA2")] = StoredGain(
                    "ID3v2 RVA2", scope, float(f.gain), float(f.peak)
                )
            continue
        text_values = [str(t) for f in frames if hasattr(f, "text") for t in f.text]
        if text_values:
            info.fields.setdefault(key, []).extend(text_values)
        if key in _SINGLE_ID3 and len(text_values) > 1:
            info.duplicates.append(f"{key} has {len(text_values)} values")
    info.gains.extend(gains.values())
    _common(info, "TIT2", "TPE1", "TALB", "TRCK")


def _from_ape(ape: APEv2, info: TagInfo) -> None:
    gains: dict[str, StoredGain] = {}
    for key, value in ape.items():
        if getattr(value, "kind", 0) != 0:  # binary item
            continue
        text = str(value)
        upper = key.upper()
        if upper in _GAIN_KEYS:
            scope, kind = _GAIN_KEYS[upper]
            _set_gain(gains.setdefault(scope, StoredGain("APEv2", scope)), kind, text, info)
        info.fields.setdefault(f"APE:{key}", []).append(text)
    info.gains.extend(gains.values())


# --- Vorbis -----------------------------------------------------------------------------


def _read_vorbis(path: str | Path, info: TagInfo) -> None:
    info.formats = ["Vorbis comments"]
    try:
        ogg: Any = OggVorbis(path)
    except mutagen.MutagenError as e:
        info.malformed.append(f"Vorbis comments could not be read ({e})")
        return
    tags = ogg.tags
    if tags is None:
        info.formats = []
        return
    gains: dict[str, StoredGain] = {}
    for key, values in tags.items():
        upper = key.upper()
        if upper == "METADATA_BLOCK_PICTURE":
            info.pictures += len(values)
            info.picture_bytes += sum(len(v) * 3 // 4 for v in values)  # base64 -> bytes
            continue
        info.fields.setdefault(upper, []).extend(str(v) for v in values)
        if upper in _GAIN_KEYS:
            scope, kind = _GAIN_KEYS[upper]
            g = gains.setdefault(scope, StoredGain("Vorbis comment", scope))
            _set_gain(g, kind, str(values[0]), info)
    for key in _SINGLE_VORBIS:
        n = len(info.fields.get(key, []))
        if n > 1:
            info.duplicates.append(f"{key} has {n} values")
    info.gains.extend(gains.values())
    _common(info, "TITLE", "ARTIST", "ALBUM", "TRACKNUMBER")


# --- shared -----------------------------------------------------------------------------


def _set_gain(g: StoredGain, kind: str, text: str, info: TagInfo) -> None:
    value = parse_gain_db(text)
    if value is None:
        info.malformed.append(f"{g.source}: {g.scope} {kind} '{text}' is not a number")
    elif kind == "gain":
        g.gain_db = value
    else:
        g.peak = value


def _common(info: TagInfo, title: str, artist: str, album: str, track: str) -> None:
    def joined(key: str) -> str:
        return " / ".join(info.fields.get(key, []))

    info.title, info.artist, info.album = joined(title), joined(artist), joined(album)
    info.track_number = joined(track).split("/")[0].strip()
