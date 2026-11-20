import io
import re
from pathlib import Path

import pytest

from helpers import encode
from qctrack.cli.main import main
from qctrack.cli.style import Painter, should_color
from qctrack.cli.table import format_table

ANSI = re.compile(r"\033\[[0-9;]*m")


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_painter_wraps_when_enabled_and_is_inert_otherwise() -> None:
    assert Painter(True)("x", "bold", "green") == "\033[1;32mx\033[0m"
    assert Painter(False)("x", "bold", "green") == "x"
    assert Painter(True)("x") == "x" and Painter(True)("", "red") == ""


def test_rating_colours_differ_and_use_only_basic_codes() -> None:
    p = Painter(True)
    shown = {r: p.rating(r, r) for r in "ABCDF"}
    assert len(set(shown.values())) == 5
    for text in shown.values():
        assert set(re.findall(r"\033\[([0-9;]*)m", text)) <= {"1;32", "32", "33", "31", "1;31", "0"}


@pytest.mark.parametrize(
    ("mode", "env", "tty", "expected"),
    [
        ("auto", {}, True, True),
        ("auto", {}, False, False),
        ("auto", {"NO_COLOR": "1"}, True, False),
        ("auto", {"TERM": "dumb"}, True, False),
        ("always", {"NO_COLOR": "1"}, False, True),
        ("never", {}, True, False),
    ],
)
def test_should_color(mode: str, env: dict[str, str], tty: bool, expected: bool) -> None:
    stream = Tty() if tty else io.StringIO()
    assert should_color(mode, stream, env) is expected


def test_styled_cells_keep_columns_aligned() -> None:
    def style(col: int, row: int, text: str) -> str:
        return Painter(True)(text, "red") if col == 1 else text

    rows = [["a", "9.5"], ["bb", "100.0"]]
    styled = format_table(["N", "Score"], rows, right=[1], style=style)
    plain = format_table(["N", "Score"], rows, right=[1])
    assert [ANSI.sub("", line) for line in styled] == plain


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("lib")
    encode(root / "a.mp3", "-c:a", "libmp3lame", "-b:a", "320k")
    encode(root / "fake.mp3", "-c:a", "libmp3lame", "-b:a", "320k", broadband=False)
    (root / "junk.mp3").write_bytes(b"not audio" * 100)
    return root


def test_color_always_adds_escapes_but_not_to_the_text(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["scan", "--color", "always", "-v", str(library)])
    colored = capsys.readouterr().out
    main(["scan", "--color", "never", "-v", str(library)])
    plain = capsys.readouterr().out
    assert "\033[" in colored and "\033[" not in plain
    assert ANSI.sub("", colored) == plain  # colour never changes the layout or wording


def test_default_output_is_plain_when_piped(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["scan", str(library)])
    assert "\033[" not in capsys.readouterr().out


def test_ratings_get_their_own_colours(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["scan", "--color", "always", str(library)])
    out = capsys.readouterr().out
    rows = {Path(ANSI.sub("", ln).split()[0]).name: ln for ln in out.splitlines() if ".mp3" in ln}
    assert "\033[1;32mA" in rows["a.mp3"]  # bold green A badge
    assert re.search(r"\033\[32m\d+\.\d\033\[0m", rows["a.mp3"])  # its score: plain green
    assert "\033[33mC" in rows["fake.mp3"]  # yellow C (G4 no longer caps it at D)
    assert "\033[36mUnsupported" in rows["junk.mp3"]  # cyan, apart from the grades


def test_bad_color_value_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["scan", "--color", "rainbow", "x.mp3"])
    assert exc.value.code == 2


def test_actions_get_their_own_colours(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Greens for keep, red for replacing a lossy source; the summary line matches."""
    main(["scan", "--color", "always", str(library)])
    out = capsys.readouterr().out
    rows = {Path(ANSI.sub("", ln).split()[0]).name: ln for ln in out.splitlines() if ".mp3" in ln}
    assert "\033[31mReplace: lossy source" in rows["fake.mp3"]
    summary = next(ln for ln in out.splitlines() if ANSI.sub("", ln).startswith("Actions:"))
    assert "\033[31mReplace: lossy source 1\033[0m" in summary


def test_action_palette() -> None:
    p = Painter(True)
    assert p.action("Keep") == "\033[1;32mKeep\033[0m"
    assert p.action("Keep: source-limited") == "\033[32mKeep: source-limited\033[0m"
    assert p.action("Replace: damaged  ") == "\033[1;31mReplace: damaged  \033[0m"
    assert p.action("Undetermined") == "\033[36mUndetermined\033[0m"
    assert Painter(False).action("Keep") == "Keep"
