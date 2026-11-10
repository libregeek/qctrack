"""Spectrogram images (todo 5.4, REQ-CLI-11, REQ-HTM-05)."""

import base64
import io
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from helpers import encode
from qctrack.cli.main import main
from qctrack.core.cache import ResultCache
from qctrack.core.spectrogram import (
    MAX_COLUMNS,
    WIDTH,
    SpectrogramAnalyzer,
    row_fraction,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("sglib")
    encode(root / "a.mp3", "-c:a", "libmp3lame", "-b:a", "128k")
    encode(root / "b.ogg", "-c:a", "libvorbis", "-q:a", "5")
    return root


def run(library: Path, out: Path, *extra: str, cache: Path | None = None) -> int:
    out.mkdir(exist_ok=True)
    args = ["scan", str(library), "--silent", "-o", str(out), *extra]
    if cache is None:
        args.append("--no-cache")
    return main(args)


def embedded(html: str) -> list[str]:
    return re.findall(r"data:image/png;base64,[A-Za-z0-9+/=]+", html)


def tracks_data(html: str) -> list[dict[str, Any]]:
    block = re.search(r'<script id="data"[^>]*>(.*?)</script>', html, re.S)
    assert block is not None
    tracks: list[dict[str, Any]] = json.loads(block.group(1))["tracks"]
    return tracks


def test_images_appear_in_the_html_only_with_the_flag(library: Path, tmp_path: Path) -> None:
    assert run(library, tmp_path / "off", "--html") == 0
    plain = (tmp_path / "off" / "report.html").read_text()
    assert "data:image/png" not in plain
    assert run(library, tmp_path / "on", "--html", "--spectrograms") == 0
    html = (tmp_path / "on" / "report.html").read_text()
    sgs = [t["sg"] for t in tracks_data(html) if "sg" in t]
    assert len(sgs) == 2
    for sg in sgs:
        png = base64.b64decode(sg["src"].split(",", 1)[1])
        assert png.startswith(PNG_MAGIC) and len(png) < 100_000
        assert sg["ticks"] and sg["dur"]
    assert "data:image/png" not in plain and len(html) < 400_000


def test_the_picture_does_not_break_the_csp_or_the_escaping(library: Path, tmp_path: Path) -> None:
    run(library, tmp_path, "--html", "--spectrograms")
    html = (tmp_path / "report.html").read_text()
    assert "img-src data:" in html and "default-src 'none'" in html
    block = re.search(r'<script id="data"[^>]*>(.*?)</script>', html, re.S)
    assert block is not None and "<" not in block.group(1)


def test_json_keeps_the_picture_so_rescore_can_draw_it(library: Path, tmp_path: Path) -> None:
    run(library, tmp_path, "--json", "--spectrograms")
    assert main(["rescore", "--silent", "-o", str(tmp_path), "--html", "--spectrograms"]) == 0
    html = (tmp_path / "report.html").read_text()
    assert len(embedded(html)) == 2
    main(["rescore", "--silent", "-o", str(tmp_path), "--html"])  # without the flag: none
    assert embedded((tmp_path / "report.html").read_text()) == []


def test_json_without_the_flag_holds_no_picture(library: Path, tmp_path: Path) -> None:
    run(library, tmp_path, "--json")
    doc = json.loads((tmp_path / "report.json").read_text())
    assert all(t["measured"]["measurements"]["spectrogram"] is None for t in doc["tracks"])


def test_rescore_warns_when_the_report_has_no_pictures(
    library: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(library, tmp_path, "--json")
    main(["rescore", "--silent", "-o", str(tmp_path), "--html", "--spectrograms"])
    assert "no spectrograms" in capsys.readouterr().err


def test_quick_scan_ignores_the_flag_with_a_warning(
    library: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(library, tmp_path, "--quick", "--html", "--spectrograms") == 0
    assert "needs a full scan" in capsys.readouterr().err
    assert embedded((tmp_path / "report.html").read_text()) == []


def test_cache_entry_without_a_picture_is_not_reused_when_one_is_wanted(
    library: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "qctrack" / "cache.db"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    run(library, tmp_path / "1", "--html", cache=db)
    run(library, tmp_path / "2", "--html", "--spectrograms", cache=db)
    assert len(embedded((tmp_path / "2" / "report.html").read_text())) == 2
    run(library, tmp_path / "3", "--html", cache=db)  # entries now hold pictures: stripped
    assert embedded((tmp_path / "3" / "report.html").read_text()) == []
    with ResultCache(db) as cache:
        assert cache.get(library / "a.mp3", False, True) is not None


def test_analyzer_memory_is_bounded_and_the_image_is_small() -> None:
    rate = 8000
    a = SpectrogramAnalyzer(rate)
    rng = np.random.default_rng(1)
    for _ in range(600):  # 600 s of noise in 1 s blocks
        a.feed(rng.normal(0, 0.1, (rate, 2)).astype(np.float32))
        assert len(a._columns) <= MAX_COLUMNS + 64  # noqa: SLF001
    result = a.result()
    assert result is not None
    assert result.png.startswith(PNG_MAGIC) and result.rate == rate
    assert abs(result.duration_s - 600) < 1
    from PIL import Image

    image = Image.open(io.BytesIO(result.png))
    assert image.size[0] <= WIDTH


def test_too_little_audio_gives_no_picture() -> None:
    a = SpectrogramAnalyzer(44100)
    a.feed(np.zeros((100, 2), dtype=np.float32))
    assert a.result() is None


def test_a_tone_is_drawn_at_its_log_frequency_height() -> None:
    from PIL import Image

    rate = 44100
    t = np.arange(rate * 4) / rate
    tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)[:, None]
    a = SpectrogramAnalyzer(rate)
    a.feed(tone)
    result = a.result()
    assert result is not None
    image = np.asarray(Image.open(io.BytesIO(result.png)))
    brightest_row = int(image.mean(axis=1).argmax())
    height = 1 - (brightest_row + 0.5) / image.shape[0]
    assert height == pytest.approx(row_fraction(1000, rate), abs=0.03)
