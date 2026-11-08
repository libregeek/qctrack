"""Turn a ``Measured`` into JSON-safe data and back (used by the cache, and by report.json).

The work is done by ``cattrs``. Only the types it does not handle on its own are registered
here: ``Path``, ``Counter`` (integer keys become JSON strings) and NumPy arrays.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, get_args, get_origin

import cattrs
import numpy as np
from cattrs.preconf.json import make_converter

from qctrack.core.analyze import Measured

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


def measured_to_json(measured: Measured) -> str:
    """JSON for ``measured`` without its path (the caller knows which file it is for)."""
    data: dict[str, Any] = _converter.unstructure(measured)
    del data["path"]
    return _converter.dumps(data)


def measured_from_json(text: str, path: Path) -> Measured:
    """The inverse of :func:`measured_to_json`. Raises ``ValueError`` on bad data."""
    try:
        data = _converter.loads(text, dict)
        data["path"] = str(path)
        return _converter.structure(data, Measured)
    except (cattrs.BaseValidationError, KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"unreadable cached measurement: {e}") from e
