"""Scoring profiles: loading, merging over the packaged default, and validation.

A profile holds every tunable number of the scoring model (REQ-PRF-01). A user profile may
override only some values (REQ-PRF-04); it is validated on its own first, so that errors
name the file and the line the user wrote (REQ-PRF-05). ``tomllib`` reports lines only for
syntax errors, so a small scanner maps each key to its line.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

OBJECTIVES = ("O1", "O2", "O3")
GATE_KEYS = ("G2_cap", "G3_cap", "G4_cap", "G5_min_coverage")
RATING_KEYS = ("A", "B", "C", "D")
_TOP_LEVEL = ("name", "version", "objectives", "factors", "bitrate", "gates", "ratings")
_DEFAULT_RESOURCE = "default-profile.toml"

Path_ = tuple[str, ...]


class ProfileError(Exception):
    """A profile could not be loaded. ``str()`` is ``FILE:LINE: message``."""

    def __init__(self, source: str, line: int | None, message: str) -> None:
        self.source = source
        self.line = line
        self.message = message
        where = f"{source}:{line}" if line else source
        super().__init__(f"{where}: {message}")


@dataclass(frozen=True)
class Profile:
    name: str
    version: str
    objectives: Mapping[str, float]
    factors: Mapping[str, float]
    mp3_bitrate_anchors: tuple[tuple[float, float], ...]
    gates: Mapping[str, float]
    ratings: Mapping[str, float]
    content_hash: str  # sha256 of the merged content, recorded in reports (REQ-PRF-06)

    @property
    def label(self) -> str:
        return f"{self.name}@{self.version}"


# --- line tracking ------------------------------------------------------------------

_TABLE_RE = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$")
_KEY_RE = re.compile(r'^\s*("(?:[^"\\]|\\.)*"|\'[^\']*\'|[A-Za-z0-9_-]+)\s*=')


def _unquote(key: str) -> str:
    if key[:1] == '"':
        return str(json.loads(key))
    if key[:1] == "'":
        return key[1:-1]
    return key


def _split_dotted(text: str) -> Path_:
    parts = re.findall(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'|[^.\s]+', text)
    return tuple(_unquote(p) for p in parts)


def _key_lines(text: str) -> dict[Path_, int]:
    """Line number of every table header and top-level ``key =`` line, by key path.

    Keys inside inline tables (``anchors = { 64 = 0 }``) map to the line of the whole
    assignment, which is where the user has to look anyway.
    """
    lines: dict[Path_, int] = {}
    table: Path_ = ()
    for number, raw in enumerate(text.splitlines(), start=1):
        header = _TABLE_RE.match(raw)
        if header:
            table = _split_dotted(header.group(1))
            lines.setdefault(table, number)
            continue
        key = _KEY_RE.match(raw)
        if key:
            lines.setdefault(table + (_unquote(key.group(1)),), number)
    return lines


# --- validation -------------------------------------------------------------------


class _Checker:
    def __init__(self, source: str, text: str, data: Mapping[str, Any]) -> None:
        self.source = source
        self.data = data
        self.lines = _key_lines(text)

    def fail(self, path: Path_, message: str) -> ProfileError:
        # Fall back to the nearest enclosing key or table that has a line.
        for n in range(len(path), 0, -1):
            if path[:n] in self.lines:
                return ProfileError(self.source, self.lines[path[:n]], message)
        return ProfileError(self.source, None, message)

    def number(self, path: Path_, value: object, lo: float, hi: float | None = None) -> float:
        label = ".".join(path)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise self.fail(path, f"{label}: must be a number, got {value!r}")
        if value < lo:
            raise self.fail(path, f"{label}: must not be below {lo:g}, got {value!r}")
        if hi is not None and value > hi:
            raise self.fail(path, f"{label}: must not be above {hi:g}, got {value!r}")
        return float(value)

    def table(self, path: Path_, value: object) -> Mapping[str, Any]:
        if not isinstance(value, dict):
            raise self.fail(path, f"{'.'.join(path)}: must be a table")
        return value

    def unknown(self, path: Path_, keys: Mapping[str, Any], allowed: tuple[str, ...]) -> None:
        for key in keys:
            if key not in allowed:
                raise self.fail(path + (key,), f"unknown key {'.'.join(path + (key,))!r}")


def _validate(
    source: str, text: str, data: Mapping[str, Any], known_factors: tuple[str, ...] | None
) -> None:
    """Check one profile file. ``known_factors`` is None only for the default itself."""
    c = _Checker(source, text, data)
    c.unknown((), data, _TOP_LEVEL)
    for key in ("name", "version"):
        if key in data and not (isinstance(data[key], str) and data[key].strip()):
            raise c.fail((key,), f"{key}: must be a non-empty string")
    if "objectives" in data:
        objs = c.table(("objectives",), data["objectives"])
        c.unknown(("objectives",), objs, OBJECTIVES)
        for k, v in objs.items():
            c.number(("objectives", k), v, 0)
    if "factors" in data:
        facs = c.table(("factors",), data["factors"])
        for k, v in facs.items():
            if known_factors is not None and k not in known_factors:
                raise c.fail(("factors", k), f"unknown factor ID {k!r}")
            c.number(("factors", k), v, 0)
    if "bitrate" in data:
        _validate_bitrate(c, data["bitrate"])
    if "gates" in data:
        gates = c.table(("gates",), data["gates"])
        c.unknown(("gates",), gates, GATE_KEYS)
        for k, v in gates.items():
            c.number(("gates", k), v, 0, 100)
    if "ratings" in data:
        ratings = c.table(("ratings",), data["ratings"])
        c.unknown(("ratings",), ratings, RATING_KEYS)
        for k, v in ratings.items():
            c.number(("ratings", k), v, 0, 100)


def _validate_bitrate(c: _Checker, value: object) -> None:
    codecs = c.table(("bitrate",), value)
    c.unknown(("bitrate",), codecs, ("mp3",))
    if "mp3" not in codecs:
        return
    mp3 = c.table(("bitrate", "mp3"), codecs["mp3"])
    c.unknown(("bitrate", "mp3"), mp3, ("anchors",))
    if "anchors" in mp3:
        _anchors(c, ("bitrate", "mp3", "anchors"), mp3["anchors"])


def _anchors(c: _Checker, path: Path_, value: object) -> tuple[tuple[float, float], ...]:
    table = c.table(path, value)
    if len(table) < 2:
        raise c.fail(path, f"{'.'.join(path)}: needs at least two points")
    points: list[tuple[float, float]] = []
    for key, score in table.items():
        try:
            kbps = float(key)
        except ValueError:
            raise c.fail(path, f"{'.'.join(path)}: {key!r} is not a bitrate in kbps") from None
        if kbps <= 0:
            raise c.fail(path, f"{'.'.join(path)}: bitrate {key} must be above 0")
        points.append((kbps, c.number(path, score, 0, 100)))
    points.sort()
    for (_, a), (kb, b) in zip(points, points[1:], strict=False):
        if b < a:
            raise c.fail(
                path, f"{'.'.join(path)}: score falls at {kb:g} kbps; scores must not decrease"
            )
    return tuple(points)


def _check_merged(source: str, user_text: str, merged: Mapping[str, Any]) -> None:
    """Cross-field rules that only make sense on the merged result."""
    c = _Checker(source, user_text, merged)
    ratings = merged["ratings"]
    values = [ratings[k] for k in RATING_KEYS]
    for i in range(len(RATING_KEYS) - 1):
        hi_key, lo_key = RATING_KEYS[i], RATING_KEYS[i + 1]
        if values[i] <= values[i + 1]:
            bad = ("ratings", hi_key) if ("ratings", hi_key) in c.lines else ("ratings", lo_key)
            raise c.fail(
                bad,
                f"rating bands overlap: {hi_key} starts at {values[i]:g} but {lo_key} starts "
                f"at {values[i + 1]:g}; each band must start above the next",
            )
    if values[-1] <= 0:
        raise c.fail(("ratings", "D"), "ratings.D: must be above 0 (F covers the rest)")
    if sum(merged["objectives"].values()) <= 0:
        raise c.fail(("objectives",), "objectives: at least one weight must be above 0")


# --- merging and loading ------------------------------------------------------------


def _merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in base.keys() | over.keys():
        if key not in over:
            out[key] = base[key]
        elif key not in base:
            out[key] = over[key]
        elif key == "anchors":
            out[key] = over[key]  # a curve is replaced whole, never mixed point by point
        elif isinstance(base[key], dict) and isinstance(over[key], dict):
            out[key] = _merge(base[key], over[key])
        else:
            out[key] = over[key]
    return out


def default_profile_text() -> str:
    return resources.files("qctrack.data").joinpath(_DEFAULT_RESOURCE).read_text("utf-8")


def _parse(source: str, text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        m = re.search(r"\(at line (\d+)", str(e))
        raise ProfileError(source, int(m.group(1)) if m else None, f"invalid TOML: {e}") from e


def _build(merged: Mapping[str, Any]) -> Profile:
    canonical = json.dumps(merged, sort_keys=True, separators=(",", ":")).encode()
    anchors = tuple(
        sorted((float(k), float(v)) for k, v in merged["bitrate"]["mp3"]["anchors"].items())
    )
    return Profile(
        name=merged["name"],
        version=merged["version"],
        objectives={k: float(v) for k, v in merged["objectives"].items()},
        factors={k: float(v) for k, v in merged["factors"].items()},
        mp3_bitrate_anchors=anchors,
        gates={k: float(v) for k, v in merged["gates"].items()},
        ratings={k: float(v) for k, v in merged["ratings"].items()},
        content_hash=hashlib.sha256(canonical).hexdigest(),
    )


def _default_data() -> dict[str, Any]:
    text = default_profile_text()
    data = _parse("default-profile.toml", text)
    _validate("default-profile.toml", text, data, None)
    return data


def load_default() -> Profile:
    data = _default_data()
    _check_merged("default-profile.toml", default_profile_text(), data)
    return _build(data)


def load_profile(path: Path) -> Profile:
    """Load a user profile over the default. Raises ``ProfileError`` naming file and line."""
    source = str(path)
    try:
        text = path.read_text("utf-8")
    except OSError as e:
        raise ProfileError(source, None, f"cannot read profile: {e.strerror}") from e
    except UnicodeDecodeError as e:
        raise ProfileError(source, None, "profile is not valid UTF-8") from e
    base = _default_data()
    user = _parse(source, text)
    _validate(source, text, user, tuple(base["factors"]))
    merged = _merge(base, user)
    _check_merged(source, text, merged)
    return _build(merged)
