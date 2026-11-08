"""Turn a ``Measured`` into JSON-safe data and back (used by the cache, and by report.json).

The work is done by ``cattrs``. Only the types it does not handle on its own are registered
here: ``Path``, ``Counter`` (integer keys become JSON strings) and NumPy arrays.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, get_args, get_origin

import cattrs
import numpy as np
from cattrs.preconf.json import make_converter

from qctrack.core.analyze import Measured
from qctrack.core.profile import Profile

_converter = make_converter()
_converter.register_unstructure_hook(Path, str)
_converter.register_structure_hook(Path, lambda v, _t: Path(v))
_converter.register_unstructure_hook(np.ndarray, lambda a: a.tolist())
_converter.register_structure_hook_func(
    lambda t: t is np.ndarray or get_origin(t) is np.ndarray,  # type: ignore[comparison-overlap]
    lambda v, _t: np.asarray(v, dtype=np.float64),
)
_converter.register_unstructure_hook(Counter, lambda c: {str(k): v for k, v in c.items()})
_converter.register_structure_hook_func(
    lambda t: get_origin(t) is Counter,  # type: ignore[comparison-overlap]
    lambda v, t: Counter({get_args(t)[0](k): int(n) for k, n in v.items()}),
)


def _listify(value: Any) -> Any:
    """cattrs unstructures an ``ndarray | None`` field by its declared type and leaves the
    array as it is; turn any such array into a list so the data is plain JSON."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {k: _listify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_listify(v) for v in value]
    return value


def measured_to_data(measured: Measured, spectrum_decimals: int | None = None) -> dict[str, Any]:
    """JSON-safe data for ``measured`` without its path (the caller knows the file).

    ``spectrum_decimals`` rounds the averaged spectrum, which is by far the largest part, for
    reports that are read by people and tools. The cache keeps it exact.
    """
    m = measured.measurements
    if spectrum_decimals is not None and m is not None:
        spectra = [
            replace(r, mean_spectrum_db=np.round(r.mean_spectrum_db, spectrum_decimals))
            if r.mean_spectrum_db is not None
            else r
            for r in m.spectrum
        ]
        measured = replace(measured, measurements=replace(m, spectrum=spectra))
    data: dict[str, Any] = _listify(_converter.unstructure(measured))
    del data["path"]
    return data


def measured_from_data(data: dict[str, Any], path: Path) -> Measured:
    """The inverse of :func:`measured_to_data`. Raises ``ValueError`` on bad data."""
    try:
        return _converter.structure({**data, "path": str(path)}, Measured)
    except (cattrs.BaseValidationError, KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"unreadable measurements: {e}") from e


def profile_to_data(profile: Profile) -> dict[str, Any]:
    """The values of a scoring profile, for recording in a report (REQ-PRF-06)."""
    data: dict[str, Any] = _converter.unstructure(profile)
    return data


def measured_to_json(measured: Measured) -> str:
    """JSON text of :func:`measured_to_data`, exact (used by the cache)."""
    return _converter.dumps(measured_to_data(measured))


def measured_from_json(text: str, path: Path) -> Measured:
    """The inverse of :func:`measured_to_json`. Raises ``ValueError`` on bad data."""
    try:
        data = _converter.loads(text, dict)
    except ValueError as e:
        raise ValueError(f"unreadable cached measurement: {e}") from e
    return measured_from_data(data, path)
