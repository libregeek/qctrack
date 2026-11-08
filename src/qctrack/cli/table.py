"""Plain-text tables for the terminal."""

from __future__ import annotations

from collections.abc import Sequence

_ELLIPSIS = "…"
_GAP = "  "
_MIN_WIDTH = 10


def _clip(text: str, width: int, keep_end: bool) -> str:
    if len(text) <= width:
        return text
    if keep_end:  # file paths: the name matters more than the folder
        return _ELLIPSIS + text[-(width - 1) :]
    return text[: width - 1] + _ELLIPSIS


def format_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    right: Sequence[int] = (),
    shrink: Sequence[int] = (),
    keep_end: Sequence[int] = (),
    max_width: int | None = None,
) -> list[str]:
    """Align ``rows`` under ``headers``.

    Columns in ``right`` are right-aligned. When ``max_width`` is given and the table is
    wider, the widest of the ``shrink`` columns is cut down (with an ellipsis, keeping the
    end of the text for columns in ``keep_end``) until it fits or they reach a minimum.
    """
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]
    if max_width is not None:
        while sum(widths) + len(_GAP) * (len(widths) - 1) > max_width:
            candidates = [i for i in shrink if widths[i] > _MIN_WIDTH]
            if not candidates:
                break
            widths[max(candidates, key=widths.__getitem__)] -= 1

    def line(cells: Sequence[str]) -> str:
        out = []
        for i, cell in enumerate(cells):
            cell = _clip(cell, widths[i], i in keep_end)
            out.append(cell.rjust(widths[i]) if i in right else cell.ljust(widths[i]))
        return _GAP.join(out).rstrip()

    rule = _GAP.join("-" * w for w in widths)
    return [line(headers), rule, *(line(r) for r in rows)]
