"""Exit codes of ``qctrack scan`` (REQ-CLI-10)."""

from pathlib import Path

import pytest

from helpers import encode
from qctrack.cli import main as cli
from qctrack.cli.main import main


@pytest.fixture(scope="module")
def good(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A genuine 320 kbps MP3 that scores about 97."""
    return encode(tmp_path_factory.mktemp("good") / "a.mp3", "-c:a", "libmp3lame", "-b:a", "320k")


@pytest.fixture(scope="module")
def fake(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A band-limited file claiming 320 kbps: gate G4; content to 12 kHz gives O3 = 0, so it
    scores at most 70 (profile 2.0 no longer caps G4)."""
    out = tmp_path_factory.mktemp("fake") / "f.mp3"
    return encode(out, "-c:a", "libmp3lame", "-b:a", "320k", broadband=False)


def test_0_when_every_file_is_analysed(good: Path) -> None:
    assert main(["scan", str(good)]) == 0


def test_1_when_a_file_is_unsupported_or_an_error(good: Path, tmp_path: Path) -> None:
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio" * 100)
    assert main(["scan", str(good), str(junk)]) == 1
    assert main(["scan", str(good), str(tmp_path / "missing.mp3")]) == 1


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["scan"],
        ["profile"],
        ["scan", "--bogus", "x.mp3"],
        ["scan", "--fail-below", "high", "x.mp3"],
        ["scan", "--fail-below", "101", "x.mp3"],
        ["scan", "--fail-below", "-1", "x.mp3"],
    ],
)
def test_2_on_usage_errors(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        code = main(argv)
        raise SystemExit(code)  # no-command case returns 2 instead of exiting
    assert exc.value.code == 2
    capsys.readouterr()


def test_2_on_a_bad_profile(good: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[ratings]\nA = 10\n")
    assert main(["scan", "--profile", str(bad), str(good)]) == 2
    assert f"{bad}:2:" in capsys.readouterr().err


def test_3_on_a_fatal_error(
    good: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(cli, "score_measured", boom)
    assert main(["scan", str(good)]) == 3
    err = capsys.readouterr().err
    assert "fatal error" in err and "disk on fire" in err and "Traceback" not in err


def test_4_when_a_track_scores_below_the_threshold(
    good: Path, fake: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan", "--fail-below", "75", str(good), str(fake)]) == 4
    assert "1 track(s) scored below 75" in capsys.readouterr().err


def test_fail_below_passes_when_every_score_reaches_it(good: Path) -> None:
    assert main(["scan", "--fail-below", "90", str(good)]) == 0
    assert main(["scan", "--fail-below", "0", str(good)]) == 0


def test_fail_below_wins_over_unsupported(fake: Path, tmp_path: Path) -> None:
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio" * 100)
    assert main(["scan", "--fail-below", "90", str(fake), str(junk)]) == 4


def test_unscored_files_never_count_as_below(tmp_path: Path) -> None:
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio" * 100)
    assert main(["scan", "--fail-below", "100", str(junk)]) == 1
