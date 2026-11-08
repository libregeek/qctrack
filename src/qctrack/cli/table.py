"""Plain-text tables for the terminal."""

from __future__ import annotations

from collections.abc import Callable, Sequence

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
    style: Callable[[int, int, str], str] | None = None,
    rule_char: str = "-",
) -> list[str]:
    """Align ``rows`` under ``headers``.

    Columns in ``right`` are right-aligned. When ``max_width`` is given and the table is
    wider, the widest of the ``shrink`` columns is cut down (with an ellipsis, keeping the
    end of the text for columns in ``keep_end``) until it fits or they reach a minimum.

    ``style(column, row, text)`` may add escape codes to a cell after it has been clipped;
    widths and padding are always worked out on the plain text. ``row`` is -1 for the header
    and the rule (whose column is -1).
    """
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]
    if max_width is not None:
        while sum(widths) + len(_GAP) * (len(widths) - 1) > max_width:
            candidates = [i for i in shrink if widths[i] > _MIN_WIDTH]
            if not candidates:
                break
            widths[max(candidates, key=widths.__getitem__)] -= 1

    def line(cells: Sequence[str], row: int) -> str:
        out = []
        for i, cell in enumerate(cells):
            plain = _clip(cell, widths[i], i in keep_end)
            shown = style(i, row, plain) if style else plain
            pad = " " * (widths[i] - len(plain))
            out.append(pad + shown if i in right else shown + pad)
        return _GAP.join(out).rstrip()

    rule = _GAP.join(rule_char * w for w in widths)
    return [
        line(headers, -1),
        style(-1, -1, rule) if style else rule,  # column -1 means the rule
        *(line(r, n) for n, r in enumerate(rows)),
    ]
