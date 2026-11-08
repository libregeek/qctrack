import subprocess
from pathlib import Path

import numpy as np
import pytest

from qctrack.cli.main import main
from qctrack.core.analyze import analyze_file
from qctrack.core.profile import load_default
from qctrack.core.scoring import Status

P = load_default()


def tone(seconds: int = 12, rate: int = 44100, broadband: bool = True) -> bytes:
    """A music-like stereo signal. Band-limited to ~12 kHz unless ``broadband``, which adds
    quiet noise so that content reaches the top of the spectrum, as real music does."""
    rng = np.random.default_rng(7)
    x = np.arange(rate * seconds) / rate
    sig = sum(
        rng.uniform(0.01, 0.05)
        * np.sin(2 * np.pi * rng.uniform(100, 12000) * x + rng.uniform(0, 6))
        for _ in range(30)
    )
    sig = (sig * (1 + 0.5 * np.sin(2 * np.pi * 2 * x))).astype(np.float32)
    pcm = np.stack([sig, sig * 0.8], axis=1)
    if broadband:
        pcm += rng.standard_normal(pcm.shape, dtype=np.float32) * np.float32(0.01)
    return pcm.astype(np.float32).tobytes()


def encode(out: Path, *codec_args: str, broadband: bool = True) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "44100", "-ac", "2", "-i", "-"]
        + list(codec_args)
        + [str(out)],
        input=tone(broadband=broadband),
        check=True,
    )
    return out


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("lib")
    encode(root / "a.mp3", "-c:a", "libmp3lame", "-b:a", "320k")
    encode(root / "low.mp3", "-c:a", "libmp3lame", "-b:a", "64k")
    encode(root / "fake.mp3", "-c:a", "libmp3lame", "-b:a", "320k", broadband=False)
    encode(root / "v.ogg", "-c:a", "libvorbis", "-q:a", "6")
    (root / "notes.mp3").write_bytes(b"this is not audio at all" * 100)
    encode(root / "o.opus", "-c:a", "libopus", "-b:a", "128k")
    return root


def test_analyze_scores_a_real_mp3(library: Path) -> None:
    r = analyze_file(library / "a.mp3", P)
    assert r.score.status in (Status.SCORED, Status.PROVISIONAL)
    assert r.score.final_score is not None and r.score.rating is not None
    assert {f.factor for f in r.factors} >= {"F1.01", "F2.06", "F2.07", "F3.01", "F3.02"}
    assert all(f.score is not None for f in r.factors)


def test_analyze_scores_a_real_ogg(library: Path) -> None:
    r = analyze_file(library / "v.ogg", P)
    assert r.score.final_score is not None
    assert "F2.05" in {f.factor for f in r.factors}


def test_genuine_320_scores_a_and_a_fake_320_is_capped_by_g4(library: Path) -> None:
    real = analyze_file(library / "a.mp3", P).score
    assert (real.rating, real.gates) == ("A", ())
    fake = analyze_file(library / "fake.mp3", P).score
    assert fake.gates == ("G4",) and fake.rating == "D"
    assert fake.final_score is not None and fake.final_score <= 59


def test_unsupported_unreadable_and_missing(library: Path, tmp_path: Path) -> None:
    assert analyze_file(library / "o.opus", P).score.status is Status.UNSUPPORTED
    assert analyze_file(library / "notes.mp3", P).score.status is Status.UNSUPPORTED
    missing = analyze_file(tmp_path / "nope.mp3", P)
    assert missing.score.status is Status.ERROR and missing.message


def test_quick_scan_is_provisional_and_skips_decoding(library: Path) -> None:
    r = analyze_file(library / "a.mp3", P, quick=True)
    assert r.score.status is Status.PROVISIONAL and "G5" in r.score.gates
    assert r.measurements is not None and r.measurements.decode_errors is None
    ogg = analyze_file(library / "v.ogg", P, quick=True)
    assert ogg.score.status is Status.PROVISIONAL


def cells(line: str) -> list[str]:
    return line.split()


def test_scan_prints_an_aligned_table_and_a_summary(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan", str(library)]) == 1  # the opus and the fake mp3 are Unsupported
    lines = capsys.readouterr().out.splitlines()
    header, rule, *rest = lines
    assert header.split()[:6] == ["File", "Album", "Title", "Score", "Rating", "Gates"]
    assert set(rule.replace(" ", "")) == {"-"}
    rows = rest[:6]
    assert [Path(r.split()[0]).name for r in rows] == sorted(
        ["a.mp3", "low.mp3", "fake.mp3", "v.ogg", "notes.mp3", "o.opus"]
    )
    # Columns line up: every row's score ends where the header's "Score" ends.
    end = header.index("Score") + len("Score")
    by_name = {Path(r.split()[0]).name: r for r in rows}
    assert by_name["a.mp3"][end - 4 : end].strip().replace(".", "").isdigit()
    assert "Unsupported" in by_name["o.opus"] and "Notes" in header
    assert "Opus" in by_name["o.opus"]
    assert lines[-1].startswith("6 files:") and "unsupported 2" in lines[-1]


def test_scan_exit_code_is_zero_when_everything_is_analysed(
    library: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["scan", str(library / "a.mp3"), str(library / "v.ogg")]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[-1].startswith("2 files:") and "Notes" not in out


def test_verbose_lists_every_factor(library: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["scan", "-v", str(library / "a.mp3")])
    out = capsys.readouterr().out
    assert "F3.02" in out and "objectives:" in out and "coverage 100%" in out


def test_scan_uses_the_given_profile_and_rejects_a_bad_one(
    library: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('[factors]\n"F1.01" = -3\n')
    assert main(["scan", "--profile", str(bad), str(library / "a.mp3")]) == 2
    assert f"{bad}:2:" in capsys.readouterr().err


def test_user_profile_is_used_automatically(
    library: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg = tmp_path / "cfg" / "qctrack"
    cfg.mkdir(parents=True)
    (cfg / "profile.toml").write_text('[factors]\n"F9.99" = 1\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert main(["scan", str(library / "a.mp3")]) == 2
    assert "unknown factor ID" in capsys.readouterr().err
