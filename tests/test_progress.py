import io
from pathlib import Path

import pytest

from qctrack.cli.progress import Progress


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_progress_line_format_and_cleanup() -> None:
    out = Tty()
    p = Progress(3, out)
    p.update(1, Path("/music/a.mp3"))
    p.update(2, Path("/music/b.mp3"))
    p.done()
    text = out.getvalue()
    assert "Processing 1/3 file: /music/a.mp3" in text
    assert "Processing 2/3 file: /music/b.mp3" in text
    assert text.endswith("\r\033[K")  # erased before the table is printed


def test_progress_is_silent_when_not_a_terminal() -> None:
    out = io.StringIO()
    p = Progress(2, out)
    p.update(1, Path("a.mp3"))
    p.done()
    assert out.getvalue() == ""


def test_long_paths_are_shortened_keeping_the_file_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shutil.get_terminal_size", lambda: __import__("os").terminal_size((40, 24))
    )
    out = Tty()
    Progress(10, out).update(4, Path("/a/very/long/folder/structure/of/music/track.mp3"))
    line = out.getvalue().removeprefix("\r\033[K")
    assert len(line) <= 39 and line.startswith("Processing 4/10 file: …")
    assert line.endswith("track.mp3")


def test_scan_leaves_standard_output_free_of_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from qctrack.cli.main import main

    (tmp_path / "x.mp3").write_bytes(b"not audio" * 50)
    main(["scan", str(tmp_path)])
    cap = capsys.readouterr()
    assert "Processing" not in cap.out and cap.err == ""
