# qctrack implementation todo

Status: `[ ]` open · `[~]` built, awaiting your test · `[x]` you tested and confirmed.
Claude marks items `[~]` when built; only you move them to `[x]` (I do it after you confirm).
Real test audio: `~/Lab/samples` (`sample.mp3`, `Track9.mp3`, `sample.ogg`, `sample.flac`). Never copied into git.
Run from source: `PYTHONPATH=src python3 -m qctrack ...`

## M0 Setup
- [x] **0.1 Dev tools installed.** `sudo dnf install python3-jinja2 python3-tqdm python3-pytest python3-hypothesis python3-mypy python3-hatchling ruff` (`ffmpeg` is already present; the README "Development" section lists the full set)
  Test: `python3 -c "import jinja2, tqdm, pytest, hypothesis"` and `ruff --version` succeed.
- [x] **0.2 Project skeleton.** `pyproject.toml`, `src/qctrack/{native,core,report,cli}`, `tests/`.
  Test: `PYTHONPATH=src python3 -m qctrack --version` prints a version; `pytest` runs (0 failures); `mypy --strict src` and `ruff check` pass.
- [x] **0.3 Real samples never enter git.** `.gitignore` covers audio files.
  Test: `git status` shows no audio files after tests run.

## M1 Vertical slice (Q-A4)
- [x] **1.1 Format detection (F1.10).** By content, never extension.
  Test: copy `sample.mp3` to `/tmp/x.ogg`; `qctrack scan /tmp/x.ogg --quick` reports MP3. `sample.flac` is reported Unsupported (FLAC), not an error.
- [x] **1.2 mpg123 binding.** Decodes with RVA off and QUIET.
  Test: `python3 -m pytest tests/test_mpg123.py -s` prints the dB match against `ffmpeg` for both MP3 samples (expect >100 dB; I measured 122.3 and 121.0) and all tests pass.
- [x] **1.3 Vorbis/Ogg binding.**
  Test: `python3 -m pytest tests/test_vorbisfile.py -s` prints the dB match against `ffmpeg` for `sample.ogg` (expect >100 dB; I measured 135.7) and all tests pass, including a generated chained stream (stereo 44.1 kHz, mono 22.05 kHz, stereo 44.1 kHz) whose samples equal each part decoded alone.
- [x] **1.4 Handles freed.**
  Test: `python3 -m pytest tests/test_resources.py -s`: 200 repeated opens of each format (abandoned mid-stream, and with decode errors) plus 10 full decodes leave open files at +0 and memory growth under 5 MB (I measured ≤0.1 MB).
- [x] **1.5 Streamed 64 KB blocks.**
  Test: same command; each full decode of `sample.mp3`, `Track9.mp3` and `sample.ogg` peaks under 150 MB (I measured 37 MB, including Python and NumPy; 36 MB by hand). Or by hand: `/usr/bin/time -v python3 -c 'import sys; sys.path.insert(0,"src"); from qctrack.native.mpg123 import Mpg123Decoder as D; [None for _ in D("/home/sasikala/Lab/samples/Track9.mp3").blocks()]' 2>&1 | grep Maximum`.

## M2 Measurements (pass 1 and 2)
- [x] **2.1 MP3 frame scanner + LAME tag (F1.01–F1.08, F2.01–F2.03).**
  Test: `scan sample.mp3 -v` shows bitrate, mode, encoder, lowpass and declared vs counted length; results agree with `mediainfo`/`mp3guessenc` if installed (neither is on this machine; the tests compare against `ffprobe` and Mutagen instead). Also `python3 -m pytest tests/test_mp3scan.py -s`.
- [x] **2.2 Ogg Vorbis header/page checks (F2.05, F2.14).**
  Test: `scan sample.ogg -v` agrees with `ogginfo` (not installed here; `sudo dnf install vorbis-tools` to compare; the tests compare against `ffprobe` and Mutagen instead). Also `python3 -m pytest tests/test_oggscan.py -s`.
- [x] **2.3 Tags and stored gain (F1.09).**
  Test: tags shown by `scan -v` match `mutagen-inspect` for each sample. Also `python3 -m pytest tests/test_tags.py -s`. None of the samples carry ReplayGain tags; the tests cover them with generated files.
- [x] **2.4 Clipping, channel anomalies (F2.07, F2.12).** `src/qctrack/core/pcm.py`: streaming analysers (measurements only; Pass/Warn/Fail scoring comes later).
  Test: `python3 -m pytest tests/test_pcm.py -s`: generated MP3 and Ogg files (hard-clipped sine vs. quiet sine) report clipped runs and the clean ones report none; runs are counted across block edges; dead channel, fake stereo and inverted polarity are flagged. Real samples print their figures. `Track9.mp3` is clean (1 run of 3 samples, 0.0000%); `sample.mp3` shows 0.0125% and `sample.ogg` 0.19% (loud masters / decoder overshoot, see Q-A10).
- [x] **2.5 Peak, loudness, dynamic range (F2.08–F2.10) via libebur128.** `native/ebur128.py` (binding) and `core/loudness.py` (streaming analyser: sample peak, true peak, integrated LUFS, LRA, PLR).
  Test: `python3 -m pytest tests/test_loudness.py -s`: a 997 Hz sine at −23 dBFS reads −23.0 LUFS, true peak exceeds sample peak on an inter-sample-over signal, and the three real samples print their figures next to `ffmpeg -af ebur128=peak=true`. LUFS and true peak agree within 0.1 on all three. LRA agrees within 0.1 on `Track9.mp3` but is 0.14 (`sample.mp3`) and 0.17 (`sample.ogg`) lower than ffmpeg's; the same PCM gives the same value through libebur128, so the difference is ffmpeg's own LRA code, not the decoder. The test allows 0.25 for LRA.
- [x] **2.6 Spectral cutoff and derived factors (F3.01–F3.04, F3.06).** `core/spectrum.py` (streaming analyser: cutoff, sharpness, upsampling evidence) and `core/authenticity.py` (F3.02 claimed vs effective band, F3.03 declared vs measured lowpass; pure functions).
  Test: `python3 -m pytest tests/test_spectrum.py -s`: noise encoded to 128 kbps and then re-encoded to 320 kbps is flagged (cutoff ≈16.9 kHz against a claimed High band: Fail, gate G4), the 128 kbps original and a genuine 320 kbps encode are Pass. The real samples print their figures: `Dooreyaanu Keralam.mp3` and `sample.mp3` (both 320 kbps) cut at ≈16.2–16.3 kHz and are flagged Fail; `Sundara Ravanan.mp3` (320 kbps, LAME lowpass 20.5 kHz) reads 20.3 kHz and passes. Caveat: `Track9.mp3` (128 kbps) cuts at 16.15 kHz, just under the matrix's 16.5 kHz Low band, so it gets a Warn; this is a table-tuning question for T11, not a decoder fault.
- [x] **2.7 Decode errors (F2.06).** `core/decode_errors.py` classifies decoder output; `native/_stderr.py` and `Mpg123Decoder(path, capture_messages=True)` collect libmpg123's text, because it returns OK while concealing damage (see architecture §4.3).
  Test: `python3 -m pytest tests/test_decode_errors.py -s`: a truncated MP3 and a truncated Ogg report one error each; trailing ID3v1, APE and Lyrics3 tags (alone and combined) report 0; bursts of corruption are counted (MP3: broken frames and resyncs; Ogg: one hole per damaged page); all real samples report 0. Or by hand: truncate a copy of `sample.mp3` (`head -c 5000000 ~/Lab/samples/sample.mp3 > /tmp/t.mp3`) and run the `mp3_errors` helper from the test file on it. There is no CLI output for F2.06 yet (the pass-2 pipeline arrives in M4).

## M3 Scoring
- [~] **3.1 Default profile TOML + loader with line-numbered errors.** `data/default-profile.toml` and `core/profile.py` (load, merge over default, validate, content hash). CLI: `profile show-default` and `profile validate FILE` (the latter is how you exercise the loader until `--profile` arrives in M4).
  Test: `qctrack profile show-default` prints it; `python3 -m pytest tests/test_profile.py`; by hand, put `[factors]` and `"F1.01" = -1` in `/tmp/p.toml` and run `qctrack profile validate /tmp/p.toml`: exits 2 with `/tmp/p.toml:2:`; a file containing only `name = "mine"` validates and falls back to defaults. Loading `~/.config/qctrack/profile.toml` automatically (REQ-PRF-03) arrives with `--profile` in M4.
- [~] **3.2 Scoring, gates, ratings, coverage.** `core/scoring.py`: `score_track` (objective and final scores, gates G2-G5, rating, coverage), `unsupported_score` (G1), plus `score_verdict` and `score_piecewise` for REQ-SCR-01/02/05. It combines factor scores only; turning each measurement into a factor score (per-factor thresholds) is not built yet.
  Test: `python3 -m pytest tests/test_scoring.py -v`: the three 6.4 sample rows reproduce (95.9 A; 57.4 D with G4; Unsupported with G1).
- [ ] **3.3 Rescore.**
  Test: `scan --json`, then `rescore --profile other.toml` gives new scores with no audio access (works after temporarily renaming the sample).

## M4 CLI, workers, cache
- [~] **4.0 Factor scoring (measurements to factor scores).** `core/factors.py`: `TrackMeasurements` and `factor_results(m, profile)` for MP3 and Ogg Vorbis. The profile gained a `[curves]` table (value-to-score points per numeric factor) and `[bitrate.vorbis]`; categorical factors follow the matrix verdicts. Factors with no measurement yet (F1.09 and other Info factors, F2.11, F2.13, F3.04, F3.05, F3.10), Vorbis F1.05/F1.06/F1.08/F3.01/F3.02 (no table), and anything the file stores nothing for (F1.06 without a LAME preset, F1.08 without a declared length) are left out and never lower coverage. Scores need no serialisation yet; `Measurement` JSON arrives with 5.2.
  Test: `python3 -m pytest tests/test_factors.py -v`: a clean 320 kbps MP3 scores 100 (A); a "320" file cut off at 16 kHz gets G4 and D; a truncated one gets G2 and F; `--quick`-style input is Provisional. The curve numbers in `qctrack profile show-default` are starting values for you to review.
- [~] **4.1 `scan` on files/folders, screen output and summary.** `core/analyze.py` (`analyze_file`: detect, pass 1, one decode streamed to every analyser, then score; a bad file gives an Error report, never an exception). `scan` prints an aligned table (File, Album, Title, Score, Rating, Gates, plus a Notes column when a file is Unsupported or Error; `*` after the rating marks Provisional; long cells are shortened with … to the terminal width when it is a terminal, `cli/table.py`), preceded by a progress line `Processing n/total file: <path>` on stderr (terminal only, rewritten in place and erased before the table, `cli/progress.py`), then a summary of counts per rating, provisional, unsupported and errors. after it a `Gates:` legend line explaining only the gates that appeared (caps taken from the profile in use), and `-v` adds the old header details plus every factor's score, verdict and value. Also `--profile PATH`, and `~/.config/qctrack/profile.toml` (or `$XDG_CONFIG_HOME`) is used automatically. `--quick` already works (pass 1 only, so Provisional). Files run one at a time; workers and `--jobs` are 4.3/4.5.
  Test: `qctrack scan ~/Lab/samples` shows one line per file and a summary; `python3 -m pytest tests/test_scan.py` (builds MP3/Ogg/Opus/garbage files with ffmpeg: a genuine 320 kbps scores A, a band-limited "320" gets G4 and D). I did not run it on your real samples (outside the project folder), so please check those scores look sensible.
- [~] **4.2 Exit codes and `--fail-below`.** `scan --fail-below SCORE` (0-100; anything else is a usage error). Code 4 takes precedence over code 1 when both apply, because the caller asked for that check by name; Error and Unsupported files have no score and never count as below. Any unexpected exception prints `qctrack: fatal error: ...` (no traceback) and exits 3, as does a closed output pipe. Unwritable report folders (also code 3) arrive with the report writers in M5.
  Test: `python3 -m pytest tests/test_exit_codes.py -v`; by hand, `qctrack scan FILE; echo $?` gives 0, a text file renamed `x.mp3` gives 1, `qctrack scan --bogus x` gives 2, `qctrack scan --fail-below 99 FILE` gives 4 for a file scoring under 99.
- [~] **4.3 Worker isolation.** `cli/workers.py`: `WorkerPool` runs passes 1 and 2 in forkserver worker processes (NumPy preloaded, BLAS threads 1, Ctrl-C ignored in workers). A worker that crashes or exceeds the time budget is killed and replaced and its file becomes an Error row (`worker crashed (SIGSEGV)`, `timed out after N s`); an exception becomes an Error too; each worker has a 2048 MB `RLIMIT_AS`. Scoring stays in the parent (`analyze.measure_file` / `score_measured`). New options: `-j/--jobs N` (default 1 until 4.5 picks it by storage) and `--timeout SECONDS` (default 300).
  Test: `python3 -m pytest tests/test_workers.py -v` injects a segfault, a hang, a memory hog and an exception into a scan alongside good files. By hand, with a file that crashes your decoder, `qctrack scan folder` still lists every other file.
- [~] **4.4 Cache and resume.** `core/cache.py` (`ResultCache`, SQLite at `$XDG_DATA_HOME/qctrack/cache.db`) and `core/codec.py` (cattrs, `Measured` to JSON and back, exact round trip; reused by 5.2). `scan` answers cache hits first, sends only misses to the workers, and commits each result as it arrives. Key: path, size, mtime, `ANALYSER_VERSION`, quick/full. New option `--no-cache`. Ctrl-C prints how many files were done and exits 130. A damaged or unwritable cache gives a warning, not a failure. Tests now use a private `XDG_DATA_HOME` (`tests/conftest.py`).
  Test: second scan is near-instant; Ctrl-C mid-scan then rerun resumes; cache lives under `~/.local/share/qctrack`, not beside audio.
- [~] **4.5 `--jobs` default by storage; `--quick`; `--follow-links`.** `core/storage.py` classifies each path named on the command line from `/proc/self/mountinfo` and `/sys/dev/block` (follows partitions and LVM/RAID members; any spinning member makes the stack spinning; tmpfs counts as solid-state). Default workers: one per CPU core (respecting CPU affinity) if every location is solid-state, otherwise 2 (spinning disk, network share, unknown); `-j N` overrides, `-v` prints the choice on stderr. `--follow-links` now also stops link loops (each real folder is entered once). `--quick` was already done in 4.1. Only the top-level paths are examined, not mounts nested inside a folder.
  Test: `--quick` marks ratings Provisional; `-j 1` works.
- [~] **4.6 Read-only guarantee.** No code change was needed: every analysed file is opened `rb` (`grep` of `src` finds no write open outside `native/`, which only reads), and the cache lives in the data directory. `tests/test_readonly.py` proves it three ways on a library of good, damaged, truncated, empty, junk and unsupported files: SHA-256, size, mtime and mode unchanged and no new files after `scan` (with `-j 2` and `--quick`); a Python audit hook sees no open-for-write under the library while `measure_file` runs (and a probe test shows the hook does catch a write); and a full scan succeeds on a read-only copy.
  Test: SHA-256 of samples identical before and after a full scan.

- [~] **4.7 Coloured, tidier terminal output.** `cli/style.py`: ratings and scores coloured by grade (A bold green, B green, C yellow, D red, F bold red; Unsupported cyan, Error red), gates yellow, headers bold, folders and notes dim, `─` rule under the header on a UTF-8 terminal, Pass/Warn/Fail coloured under `-v`. Only the 8 basic ANSI colours, bold and dim (no backgrounds, no bright colours), so your terminal theme picks the shades; the letters still carry the meaning. Auto: on for a terminal, off when piped, `NO_COLOR` set or `TERM=dumb`; `--color always|never` overrides.
  Test: `python3 -m pytest tests/test_style.py -v`; look at `qctrack scan folder` in your terminal, then `NO_COLOR=1 qctrack scan folder` and `qctrack scan folder | cat` for plain text.

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
