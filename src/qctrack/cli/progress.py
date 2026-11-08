"""Progress line on standard error: ``Processing n/total file: <path>`` (REQ-CLI-09).

Shown only when the stream is an interactive terminal, so pipes and redirected output stay
clean. The line is rewritten in place and erased at the end, before results are printed.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TextIO

_CLEAR = "\r\033[K"


class Progress:
    def __init__(self, total: int, stream: TextIO) -> None:
        self._total = total
        self._stream = stream
        self.enabled = stream.isatty()

    def update(self, number: int, path: Path) -> None:
        """Announce that file ``number`` (1-based) is about to be processed."""
        if not self.enabled:
            return
        prefix = f"Processing {number}/{self._total} file: "
        room = shutil.get_terminal_size().columns - len(prefix) - 1
        text = str(path)
        if room > 1 and len(text) > room:
            text = "…" + text[-(room - 1) :]  # keep the end, where the file name is
        self._stream.write(f"{_CLEAR}{prefix}{text}")
        self._stream.flush()

    def done(self) -> None:
        """Erase the progress line."""
        if self.enabled:
            self._stream.write(_CLEAR)
            self._stream.flush()
