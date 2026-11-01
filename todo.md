# qctrack implementation todo

Status: `[ ]` open · `[~]` built, awaiting your test · `[x]` you tested and confirmed.
Claude marks items `[~]` when built; only you move them to `[x]` (I do it after you confirm).
Real test audio: `~/Lab/samples` (`sample.mp3`, `Track9.mp3`, `sample.ogg`, `sample.flac`). Never copied into git.
Run from source: `PYTHONPATH=src python3 -m qctrack ...`

## M0 Setup
- [ ] **0.1 Dev tools installed.** `sudo dnf install python3-jinja2 python3-tqdm python3-pytest python3-hypothesis python3-mypy python3-hatchling ruff` (`ffmpeg` is already present; the README "Development" section lists the full set)
  Test: `python3 -c "import jinja2, tqdm, pytest, hypothesis"` and `ruff --version` succeed.
- [ ] **0.2 Project skeleton.** `pyproject.toml`, `src/qctrack/{native,core,report,cli}`, `tests/`.
  Test: `PYTHONPATH=src python3 -m qctrack --version` prints a version; `pytest` runs (0 failures); `mypy --strict src` and `ruff check` pass.
- [ ] **0.3 Real samples never enter git.** `.gitignore` covers audio files.
  Test: `git status` shows no audio files after tests run.

## M1 Vertical slice (Q-A4)
- [ ] **1.1 Format detection (F1.10).** By content, never extension.
  Test: copy `sample.mp3` to `/tmp/x.ogg`; `qctrack scan /tmp/x.ogg --quick` reports MP3. `sample.flac` is reported Unsupported (FLAC), not an error.
- [ ] **1.2 mpg123 binding.** Decodes with RVA off and QUIET.
  Test: peak/sample count of `sample.mp3` matches `ffmpeg` output within tolerance (test prints the dB match, expect >100 dB).
- [ ] **1.3 Vorbis/Ogg binding.**
  Test: same comparison for `sample.ogg`.
- [ ] **1.4 Handles freed.**
  Test: a test decodes each file 200 times and memory stays flat.
- [ ] **1.5 Streamed 64 KB blocks.**
  Test: peak memory of one full decode of `Track9.mp3` is under 150 MB (`/usr/bin/time -v`).

## M2 Measurements (pass 1 and 2)
- [ ] **2.1 MP3 frame scanner + LAME tag (F1.01–F1.08, F2.01–F2.03).**
  Test: `scan sample.mp3 -v` shows bitrate, mode, encoder, lowpass and declared vs counted length; results agree with `mediainfo`/`mp3guessenc` if installed.
- [ ] **2.2 Ogg Vorbis header/page checks (F2.05, F2.14).**
  Test: `scan sample.ogg -v` agrees with `ogginfo`.
- [ ] **2.3 Tags and stored gain (F1.09).**
  Test: tags shown match `mutagen-inspect`.
- [ ] **2.4 Clipping, channel anomalies (F2.07, F2.12).**
  Test: a generated clipped file reports clipping; clean sample does not.
- [ ] **2.5 Peak, loudness, dynamic range (F2.08–F2.10) via libebur128.**
  Test: LUFS and LRA within 0.1 of `ffmpeg -af ebur128` on `sample.mp3`.
- [ ] **2.6 Spectral cutoff and derived factors (F3.01–F3.04, F3.06).**
  Test: a 128 kbps file re-encoded to 320 kbps is flagged; the original is not.
- [ ] **2.7 Decode errors (F2.06).**
  Test: a truncated copy of `sample.mp3` reports errors; known tags (ID3, APE, Lyrics3) are not errors.

## M3 Scoring
- [ ] **3.1 Default profile TOML + loader with line-numbered errors.**
  Test: `profile show-default` prints it; a profile with a negative weight exits 2 naming file and line; a partial profile falls back to defaults.
- [ ] **3.2 Scoring, gates, ratings, coverage.**
  Test: the worked examples in requirements section 6.4 reproduce; unit tests pass.
- [ ] **3.3 Rescore.**
  Test: `scan --json`, then `rescore --profile other.toml` gives new scores with no audio access (works after temporarily renaming the sample).

## M4 CLI, workers, cache
- [ ] **4.1 `scan` on files/folders, screen output and summary.**
  Test: `qctrack scan ~/Lab/samples` shows one line per file and a summary.
- [ ] **4.2 Exit codes and `--fail-below`.**
  Test: codes 0/1/2/3/4 each reproduced as in the README table.
- [ ] **4.3 Worker isolation.**
  Test: a deliberately crashing and a hanging file each cost one file (Error) while the others finish.
- [ ] **4.4 Cache and resume.**
  Test: second scan is near-instant; Ctrl-C mid-scan then rerun resumes; cache lives under `~/.local/share/qctrack`, not beside audio.
- [ ] **4.5 `--jobs` default by storage; `--quick`; `--follow-links`.**
  Test: `--quick` marks ratings Provisional; `-j 1` works.
- [ ] **4.6 Read-only guarantee.**
  Test: SHA-256 of samples identical before and after a full scan.

## M5 Reports
- [ ] **5.1 CSV** (RFC 4180, CRLF, N/A empty, formula escaping, `--csv=-`).
  Test: opens correctly in LibreOffice; a tag starting with `=` is escaped.
- [ ] **5.2 JSON** (`report.json`).
  Test: contains all measurements, scores and the profile hash.
- [ ] **5.3 HTML** (self-contained, sortable/filterable, detail section, dark/light, escaped tags).
  Test: open offline in a browser; no network requests; a tag containing `<script>` shows as text.
- [ ] **5.4 `--spectrograms`.**
  Test: images appear in the HTML only with the flag.

## M6 Quality and release
- [ ] **6.1 Accuracy checks on real samples** (decoder match, Q-A7, Q-A10).
  Test: I review the written findings.
- [ ] **6.2 Fuzz/corrupt-file tests, golden report tests.**
  Test: `pytest` passes.
- [ ] **6.3 Man page, `--help`, `--version`.**
  Test: `man ./qctrack.1` renders.
- [ ] **6.4 Fedora RPM.**
  Test: `rpmbuild` succeeds; install on a clean Fedora and run a scan.
