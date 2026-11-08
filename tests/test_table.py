from qctrack.cli.table import format_table


def test_columns_align_and_right_align() -> None:
    out = format_table(["File", "Score"], [["a", "9.5"], ["longer", "100.0"]], right=[1])
    assert out == ["File    Score", "------  -----", "a         9.5", "longer  100.0"]


def test_wide_table_shrinks_the_widest_column_with_an_ellipsis() -> None:
    rows = [["/very/long/folder/name/track.mp3", "x"]]
    out = format_table(["File", "N"], rows, shrink=[0], keep_end=[0], max_width=20)
    assert all(len(line) <= 20 for line in out)
    assert out[2].startswith("…") and out[2].split()[0].endswith("track.mp3")


def test_shrinking_stops_at_a_minimum_width() -> None:
    out = format_table(["A", "B"], [["x" * 50, "y"]], shrink=[0], max_width=5)
    assert len(out[2].split()[0]) == 10


def test_other_columns_clip_from_the_end() -> None:
    out = format_table(["T"], [["a very long title indeed"]], shrink=[0], max_width=12)
    assert out[2] == "a very long…"
