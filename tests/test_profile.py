from pathlib import Path

import pytest

from qctrack.cli.main import main
from qctrack.core.profile import ProfileError, default_profile_text, load_default, load_profile


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "p.toml"
    p.write_text(text)
    return p


def test_default_loads_with_spec_values() -> None:
    p = load_default()
    assert p.label == "default@1.1"
    assert p.objectives == {"O1": 25, "O2": 30, "O3": 45}
    assert p.factors["F3.02"] == 30 and p.factors["F2.09"] == 0
    assert p.gates["G4_cap"] == 59 and p.gates["G5_min_coverage"] == 70
    assert p.ratings == {"A": 90, "B": 75, "C": 60, "D": 40}
    assert p.mp3_bitrate_anchors == ((64, 0), (128, 50), (160, 75), (256, 100))


def test_default_factor_weights_match_requirements_table() -> None:
    f = load_default().factors
    assert sum(v for k, v in f.items() if k.startswith("F1.")) == 100
    assert sum(v for k, v in f.items() if k.startswith("F2.")) == 108
    # 110 in the requirements table, less F3.06 (10), which profile 1.1 reports only (H5).
    assert sum(v for k, v in f.items() if k.startswith("F3.")) == 100
    assert f["F3.06"] == 0


def test_partial_profile_falls_back_to_default(tmp_path: Path) -> None:
    p = load_profile(write(tmp_path, 'name = "mine"\n[factors]\n"F3.01" = 40\n'))
    d = load_default()
    assert p.name == "mine" and p.factors["F3.01"] == 40
    assert p.factors["F3.02"] == d.factors["F3.02"] and p.ratings == d.ratings
    assert p.content_hash != d.content_hash


def test_empty_profile_equals_default(tmp_path: Path) -> None:
    assert load_profile(write(tmp_path, "")).content_hash == load_default().content_hash


def test_anchors_replace_whole_curve(tmp_path: Path) -> None:
    p = load_profile(write(tmp_path, "[bitrate.mp3]\nanchors = { 96 = 0, 192 = 100 }\n"))
    assert p.mp3_bitrate_anchors == ((96, 0), (192, 100))


@pytest.mark.parametrize(
    ("text", "line", "fragment"),
    [
        ('name = "x"\n[factors]\n"F3.01" = -5\n', 3, "must not be below"),
        ('[factors]\n"F9.99" = 5\n', 2, "unknown factor ID"),
        ('[factors]\n"F3.01" = "high"\n', 2, "must be a number"),
        ('[factors]\n\n\n"F3.01" = true\n', 4, "must be a number"),
        ("[objectives]\nO1 = 10\nO4 = 5\n", 3, "unknown key"),
        ("[ratings]\nA = 70\nB = 75\n", 2, "overlap"),
        ("[ratings]\nD = 0\n", 2, "above 0"),
        ("[gates]\nG4_cap = 150\n", 2, "above 100"),
        (
            "[bitrate.mp3]\nanchors = { 64 = 0, 128 = 50, 256 = 20 }\n",
            2,
            "keep rising or keep falling",
        ),
        ('[curves]\n"F9.99" = [[0, 100], [1, 0]]\n', 2, "unknown factor ID"),
        ('[curves]\n"F2.07" = [[0, 100]]\n', 2, "at least two points"),
        ('[curves]\n"F2.07" = [[0, 100], [0, 50]]\n', 2, "same value"),
        ('[curves]\n"F2.07" = [[0, 100], [1, 300]]\n', 2, "above 100"),
        ('[curves]\n"F2.07" = [1, 2]\n', 2, "pairs"),
        ("mystery = 1\n", 1, "unknown key"),
        ("[factors\n", 1, "invalid TOML"),
    ],
)
def test_invalid_profile_names_file_and_line(
    tmp_path: Path, text: str, line: int, fragment: str
) -> None:
    path = write(tmp_path, text)
    with pytest.raises(ProfileError) as exc:
        load_profile(path)
    assert exc.value.line == line
    assert str(exc.value).startswith(f"{path}:{line}: ")
    assert fragment in str(exc.value)


def test_missing_file_is_a_profile_error(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="cannot read"):
        load_profile(tmp_path / "nope.toml")


def test_cli_show_default_prints_the_packaged_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profile", "show-default"]) == 0
    assert capsys.readouterr().out == default_profile_text()


def test_cli_show_default_round_trips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["profile", "show-default"])
    path = write(tmp_path, capsys.readouterr().out)
    assert load_profile(path).content_hash == load_default().content_hash


def test_cli_negative_weight_exits_2_naming_file_and_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, '[factors]\n"F1.01" = -1\n')
    assert main(["profile", "validate", str(path)]) == 2
    assert f"{path}:2:" in capsys.readouterr().err


def test_cli_validate_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["profile", "validate", str(write(tmp_path, '[factors]\n"F1.01" = 1\n'))]) == 0
    assert "OK" in capsys.readouterr().out


def test_default_curves_and_vorbis_bitrate() -> None:
    p = load_default()
    assert p.curves["F2.07"] == ((0.001, 100), (0.1, 50), (1, 0))
    assert p.curves["F2.10"][0] == (1, 0)  # a rising curve: higher is better
    assert p.vorbis_bitrate_anchors == ((64, 0), (112, 50), (160, 100))


def test_a_curve_override_replaces_that_factor_only(tmp_path: Path) -> None:
    p = load_profile(write(tmp_path, '[curves]\n"F2.07" = [[0, 100], [2, 0]]\n'))
    d = load_default()
    assert p.curves["F2.07"] == ((0, 100), (2, 0))
    assert p.curves["F2.06"] == d.curves["F2.06"]


def test_merged_profile_keeps_the_default_key_order(tmp_path: Path) -> None:
    """A set union in the merge once made the saved report's key order change between runs."""
    p = load_profile(write(tmp_path, '[objectives]\nO3 = 60\n[factors]\n"F2.08" = 12\n'))
    assert list(p.objectives) == list(load_default().objectives)
    assert list(p.factors) == list(load_default().factors)
