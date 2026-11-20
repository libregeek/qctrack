"""HTML report (todo 5.3, REQ-HTM-01..04, 06..08)."""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from helpers import encode
from qctrack.cli.main import main
from qctrack.core.analyze import TrackReport, measure_file, score_measured
from qctrack.core.profile import load_default
from qctrack.report.html_report import write_html

HOSTILE_TITLE = '<script>window.pwned=1</script><img src=x onerror="window.pwned=1">'
HOSTILE_ALBUM = '</script><script>window.pwned=1</script>&amp; "q" \u2028 end'


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("hlib")
    encode(
        root / "a.mp3",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        "-metadata",
        f"title={HOSTILE_TITLE}",
        "-metadata",
        f"album={HOSTILE_ALBUM}",
        "-metadata",
        "artist=Zed",
    )
    encode(root / "b.ogg", "-c:a", "libvorbis", "-q:a", "5", "-metadata", "artist=Amy")
    encode(root / "c.flac", "-c:a", "flac")
    (root / "<b>weird&name.mp3").write_bytes(b"not audio" * 100)
    return root


@pytest.fixture(scope="module")
def report_html(library: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("hout")
    main(["scan", str(library), "--no-cache", "--silent", "--html", "-o", str(out)])
    return out / "report.html"


def test_report_is_one_file_with_no_external_references(report_html: Path) -> None:
    html = report_html.read_text()
    assert html.startswith("<!DOCTYPE html>")
    assert not re.search(r"(src|href)\s*=\s*[\"']?(https?:|//|file:)", html)
    assert "url(" not in html and "@import" not in html
    assert "default-src 'none'" in html  # the browser itself refuses any fetch
    assert len(re.findall(r"<script", html)) == 2  # the data block and the one program


def test_markup_in_tags_and_file_names_is_escaped(report_html: Path) -> None:
    html = report_html.read_text()
    assert "<img" not in html and 'onerror="window' not in html
    assert html.count("<script") == 2 and html.count("</script>") == 2
    assert "&lt;script&gt;window.pwned=1&lt;/script&gt;" in html
    assert "&lt;b&gt;weird&amp;name.mp3" in html
    data = re.search(r'<script id="data"[^>]*>(.*?)</script>', html, re.S)
    assert data is not None
    block = data.group(1)
    assert "<" not in block and ">" not in block and "&" not in block
    assert "\u2028" not in block
    json.loads(block)  # still valid JSON


def test_summary_table_and_details_are_present(report_html: Path) -> None:
    html = report_html.read_text()
    assert "Profile default@2.0" in html and load_default().content_hash[:12] in html
    assert re.search(r"Scanned \d{4}-\d\d-\d\d \d\d:\d\d:\d\d[+-]\d\d:\d\d", html)
    assert html.count("<tr data-i=") == 4
    assert "Score distribution" in html and "Show as table" in html
    data = json.loads(re.search(r'<script id="data"[^>]*>(.*?)</script>', html, re.S).group(1))  # type: ignore[union-attr]
    assert len(data["tracks"]) == 4
    assert data["factors"]["F3.01"][0] == "Measured spectral cutoff"
    assert [o[0] for o in data["objectives"]] == ["O1", "O2", "O3", "O4"]
    # Best copy and action: columns, an action filter, counts per action, detail line.
    assert '<button type="button">Best copy</button>' in html and 'id="action"' in html
    assert 'data-action="Keep"' in html and "<span>Keep</span>" in html
    assert any(t.get("adv") and t["adv"][1] == "Keep" for t in data["tracks"])
    scored = data["tracks"][1]  # a.mp3; tracks are in path order and "<" sorts first
    assert scored["f"] and scored["f"][0][0] == "F1.01"


def test_default_name_and_stdout(
    library: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ogg = str(library / "b.ogg")
    main(["scan", ogg, "--no-cache", "--silent", "--html", "-o", str(tmp_path)])
    assert (tmp_path / "report.html").is_file()
    main(["scan", ogg, "--no-cache", "--html=-"])
    out = capsys.readouterr().out
    assert out.startswith("<!DOCTYPE html>") and "files:" not in out
    assert main(["scan", str(library), "--html=-", "--csv=-"]) == 2


def test_empty_scan_still_renders(tmp_path: Path) -> None:
    out = tmp_path / "e.html"
    main(["scan", str(tmp_path / "nothing.mp3"), "--silent", f"--html={out}"])
    assert "Score distribution" in out.read_text()  # an Error row, no scores


def test_ten_thousand_tracks_stay_under_20_mb(library: Path, tmp_path: Path) -> None:
    profile = load_default()
    base = score_measured(measure_file(library / "a.mp3"), profile)
    reports = [_clone(base, i) for i in range(10_000)]
    out = tmp_path / "big.html"
    start = time.monotonic()
    with open(out, "w", encoding="utf-8") as f:
        write_html(f, reports, profile, analyzed_at="2026-11-08T12:00:00+00:00")
    assert time.monotonic() - start < 30
    assert out.stat().st_size < 20 * 1024 * 1024, out.stat().st_size


def _clone(r: TrackReport, i: int) -> TrackReport:
    from dataclasses import replace

    return replace(r, path=Path(f"/music/album {i // 12}/track {i:05d}.mp3"))


BROWSER = shutil.which("chromium-browser") or shutil.which("chromium")

DRIVER = """
<script>
(function () {
  var out = {};
  function rowsText() {
    return Array.from(document.querySelectorAll("#tracks tbody tr:not(.detail)"))
      .filter(function (r) { return !r.hidden; })
      .map(function (r) { return r.cells[4].textContent.split("/").pop(); });
  }
  out.initial = rowsText();
  var scoreTh = document.querySelector('th[data-col="6"] button');
  scoreTh.click(); out.scoreAsc = rowsText();
  scoreTh.click(); out.scoreDesc = rowsText();
  document.querySelector('th[data-col="3"] button').click(); out.artistAsc = rowsText();
  var q = document.getElementById("q");
  q.value = "vorbis"; q.dispatchEvent(new Event("input")); out.filtered = rowsText();
  q.value = ""; q.dispatchEvent(new Event("input"));
  var sel = document.getElementById("rating");
  sel.value = "Unsupported"; sel.dispatchEvent(new Event("change")); out.unsupported = rowsText();
  out.count = document.getElementById("count").textContent;
  sel.value = ""; sel.dispatchEvent(new Event("change"));
  var first = Array.from(document.querySelectorAll("#tracks tbody tr"))
    .find(function (r) { return r.cells[4].textContent.endsWith("a.mp3"); });
  first.querySelector("button.toggle").click();
  var d = document.querySelector("tr.detail");
  out.detail = d ? d.textContent : null;
  out.detailCells = d ? d.querySelectorAll("td").length : 0;
  first.querySelector("button.toggle").click();
  out.closed = document.querySelector("tr.detail") === null;
  out.pwned = window.pwned === undefined ? null : window.pwned;
  out.titles = Array.from(document.querySelectorAll("#tracks tbody tr"))
    .map(function (r) { return r.cells[2].textContent; });
  var pre = document.createElement("pre"); pre.id = "result";
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})();
</script>
"""


@pytest.mark.skipif(BROWSER is None, reason="needs Chromium (test-only, optional)")
def test_sorting_filtering_and_details_in_a_real_browser(report_html: Path, tmp_path: Path) -> None:
    assert BROWSER
    page = tmp_path / "driven.html"
    page.write_text(report_html.read_text().replace("</body>", DRIVER + "</body>"))
    run = subprocess.run(
        [BROWSER, "--headless", "--no-sandbox", "--disable-gpu", "--dump-dom", f"file://{page}"],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    m = re.search(r'<pre id="result">(.*?)</pre>', run.stdout, re.S)
    assert m, run.stderr[-500:]
    import html as htmllib

    out = json.loads(htmllib.unescape(m.group(1)))
    assert out["initial"] == ["<b>weird&name.mp3", "a.mp3", "b.ogg", "c.flac"]
    assert out["scoreAsc"][-2:] == ["c.flac", "<b>weird&name.mp3"] or out["scoreAsc"][0] == "a.mp3"
    assert out["scoreAsc"][:2] == ["a.mp3", "b.ogg"]  # 93 < 98; unscored rows come last
    assert out["scoreDesc"][:2] == ["b.ogg", "a.mp3"]
    assert out["artistAsc"][:2] == ["b.ogg", "a.mp3"]  # Amy before Zed; empty artists last
    assert out["filtered"] == ["b.ogg"]
    assert set(out["unsupported"]) == {"c.flac", "<b>weird&name.mp3"}
    assert out["count"] == "2 of 4 tracks"
    assert "F1.01" in out["detail"] and "What it checks" in out["detail"]
    assert out["closed"] is True
    assert out["pwned"] is None  # hostile tags never ran
    assert HOSTILE_TITLE in out["titles"]  # shown as text
