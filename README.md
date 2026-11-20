# qctrack

qctrack gives every audio track a quality score from 0 to 100 and a rating from A to F, and tells you whether a better copy is worth looking for. It does this without playing the track. It detects fake-bitrate files (low-quality sources re-encoded to a higher bitrate), ranks copies of the same track, explains every result, and writes a self-contained HTML report, a CSV file and a JSON file.

- **Formats (current release):** MP3 and Ogg Vorbis. FLAC and Opus are detected and reported as "Supported in a later release".
- **Platform:** Linux (x86-64 and aarch64).
- **Safe by design:** your files are opened read-only and are never modified, played or uploaded. qctrack uses no network and sends no telemetry.
- **License:** GPLv3 (see [LICENSE](LICENSE)).

> **Status:** version 0.1.0, phase 1 under active development; no release has been published yet. Run it from the source folder (below). The default scoring weights are proposals still awaiting approval, and the thresholds are being tuned against labelled files, so scores may shift between versions. Progress is tracked in [todo.md](todo.md); the design is in [spec/requirements.md](spec/requirements.md) and [spec/architecture.md](spec/architecture.md).

## Get the source

```bash
git clone https://github.com/libregeek/qctrack.git
cd qctrack
```

## Install the dependencies

qctrack is written in Python and uses each format's dedicated decoder library, so there is nothing to compile. It targets Fedora, and Ubuntu support may follow.

```bash
sudo dnf install git python3-numpy python3-mutagen python3-jinja2 python3-tqdm \
    mpg123-libs libvorbis libogg libebur128
```

Add `python3-pillow` if you want spectrogram images (`--spectrograms`).

## Run

Scan a folder (recursive) and print the results on screen:

```bash
qctrack scan ~/Music
```

Write reports as well:

```bash
qctrack scan ~/Music --html --csv --json              # report.html, report.csv, report.json
qctrack scan ~/Music --html=library.html -o ~/reports --silent
qctrack scan a.mp3 b.ogg --quick --csv=-              # CSV to standard output
```

Compare copies of one track, or find copies across a library:

```bash
qctrack compare old/song.mp3 new/song.ogg             # which copy to keep, and why
qctrack scan ~/Music --duplicates --html              # group copies and rank each group
```

When running from the source folder without installing, use `PYTHONPATH=src python3 -m qctrack` in place of `qctrack`.

### Common options

| Option | What it does |
| --- | --- |
| `--html[=FILE]`, `--csv[=FILE]`, `--json[=FILE]` | Write a report file. Without a name it writes `report.html`, `report.csv` or `report.json`. Use the `=` form for a custom name. |
| `-o, --output-dir DIR` | Folder for the report files (default: current folder). |
| `--quick` | Fast header-only scan. Ratings are marked Provisional. |
| `--spectrograms` | Embed spectrogram images in the HTML report (larger file). |
| `-j, --jobs N` | Number of parallel workers (default: chosen to suit your storage). |
| `--timeout SECONDS` | Time budget per file (default 300). A file that takes longer is reported as an Error. |
| `-v, --verbose` / `--silent` | More or less terminal output. |
| `--profile PATH` | Use your own scoring profile. |
| `--color auto\|always\|never` | Colour the output. The default `auto` colours a terminal and stays plain when piped or when `NO_COLOR` is set. |
| `--follow-links` | Follow symbolic links to folders (off by default). |
| `--fail-below SCORE` | Exit with code 4 if any track scores below `SCORE`. Useful in scripts. |
| `--duplicates` | (`scan`, `rescore`) Group copies of the same track (same artist and title tags, durations within 2 s) and rank each group. |
| `--no-cache` | Analyse every file again instead of reusing cached results. |

`compare` takes the same report and profile options plus `-j`, `--timeout` and `--no-cache`. Run `qctrack --help`, `qctrack scan --help` or `man ./man/qctrack.1` for the full list. Results are cached in `~/.local/share/qctrack/cache.db`, never next to your music, so a second scan of the same files is fast. Existing report files with the same name are overwritten without asking.

## Understanding the results

The score is a weighted average of four objectives (default weights in brackets):

- **Metadata & encoding (25):** bitrate, encoder, quality setting, sample rate. A mono file is judged as stereo at twice its bitrate.
- **Technical integrity (25):** stream structure, truncation, decode errors, channel problems.
- **Bandwidth & authenticity (30):** how high the audible content actually reaches.
- **Mastering (20):** clipping, true peak, loudness and how hard the master is limited (peak-to-loudness ratio).

Real damage (a broken or truncated stream) caps the score at 39. A suspected fake (content that stops well below what the bitrate claims) does not lower the score by itself, because the file sounds like its source; it shows up in the action instead.

| Rating | Score | Meaning |
| --- | --- | --- |
| A | 90–100 | Excellent |
| B | 75–89 | Good |
| C | 60–74 | Fair |
| D | 40–59 | Poor |
| F | 0–39 | Bad |

Other statuses:

- **Provisional:** not enough was checked for a firm grade (for example after `--quick`).
- **Unsupported:** the format is not analysed in this release.
- **Error:** the file could not be analysed. The rest of the scan continues.

Ratings are estimates. Old, lo-fi or band-limited recordings can look like transcodes, so treat a low score as a prompt to look closer, not as proof. Open the HTML report to see each factor's measured value and a plain-language explanation.

The score and rating answer "how good does this file sound?". A separate **action** answers "should I look for a better copy?":

| Action | Meaning | What to look for |
| --- | --- | --- |
| Replace: damaged | The file is broken (cut off, corrupt) | The same release again |
| Replace: lossy source | It claims high quality but was made from a lower-quality file | A copy from a different source |
| Replace: low bitrate | Genuine, but encoded at a low setting | The same release at a higher bitrate, or lossless |
| Consider another master | The encode is fine, the mastering is not (clipping, heavy limiting) | A different release or remaster |
| Undetermined | The tool cannot tell (quick scan, or a dark source that looks like a re-encode) | Check the spectrogram, or run a full scan |
| Keep: source-limited | High frequencies are faint in the recording itself | Nothing; a better copy is unlikely |
| Keep | Nothing to improve | Nothing |

Re-encoding a file you already have to a higher bitrate never helps: lost detail cannot be restored, and the result is a transcode. `-v` and the reports show the reason for each action, and a best-copy verdict (Best available likely, Low-bitrate encode, Lossy or band-limited source, Undetermined).

Flags note things that never change the score, for example `mono_content`, `short_track`, `low_sample_rate`, `unknown_encoder`, `cover_art_large` or `different_masters`. They appear in the `flags` column of the CSV and in the HTML details.

`compare` and `--duplicates` rank copies by integrity first, then authenticity, then how high the content reaches and the bitrate, then mastering. Differences too small to matter (under 1 kHz of bandwidth or 10 mastering points) count as equal, and the result says "Equivalent". It also warns when the copies look like different masters or different tracks.

### Before you discard a track

A "Replace" action, even with a low score, is not a reason to delete a file on its own:

- **It means "look for a better copy", not "delete this one".** Until you have a better copy, the file you hold is the best one available, and a 128 kbps track still beats no track, especially when a better copy may never turn up.
- **A low score does not mean the file is useless.** Damage can be small (a missing last few seconds caps the score at 39 but leaves the song playable), and a low-bitrate or lossy-sourced file still plays; it just sounds dull.
- **The verdicts are estimates.** Check the reason, the spectrogram (`--spectrograms`) or simply listen before deciding.

A safer rule:

1. Find another copy.
2. Run `qctrack compare old.mp3 new.mp3`.
3. If it says "keep new.mp3" and names a clear step (integrity, authenticity or encode quality), discard the old one.
4. If it says "Equivalent", keep whichever you like; the swap is not worth it.

Use scores and actions to decide where to spend effort looking for better copies, and `compare` to decide what to discard once you have something to compare against. qctrack itself never deletes, moves or changes any file.

## Change the weights

The default scoring weights ship with qctrack. To change them:

```bash
qctrack profile show-default > my-weights.toml    # copy the defaults
# edit my-weights.toml; you only need to keep the values you change
qctrack scan ~/Music --profile my-weights.toml --html
```

Save the file as `~/.config/qctrack/profile.toml` to use it automatically.

Two common changes: set `O4 = 0` under `[objectives]` to leave mastering out of the score, and adjust the `[advice]` table to change when "Consider another master" applies and how far apart copies must be before `compare` prefers one.

You can also re-score a finished scan with a different profile without analysing the audio again:

```bash
qctrack rescore --profile my-weights.toml --html --csv    # reads report.json
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | All files were analysed. |
| 1 | Some files were Error or Unsupported. |
| 2 | Usage or profile error (including `compare` with fewer than two files). |
| 3 | Fatal error, such as an unwritable output folder. |
| 4 | A track scored below `--fail-below`. This takes precedence over code 1. |

## Development

Install the build and test tools on Fedora, in addition to the runtime dependencies above:

```bash
sudo dnf install python3-pytest python3-hypothesis python3-mypy python3-hatchling ruff \
    ffmpeg-free python3-pillow
```

`ffmpeg-free` is used only by tests, to cross-check decoded audio and loudness. For the RPM and man page you also need `pyproject-rpm-macros` and `python3-argparse-manpage`.

```bash
PYTHONPATH=src python3 -m pytest      # tests
mypy --strict src                     # type checks
ruff check . && ruff format --check src tests   # lint and formatting
```

Test audio is not stored in the repository. Tests generate the files they need with FFmpeg, and a few real-file tests read from a folder of your own samples (`QCTRACK_SAMPLES`, default `~/Lab/samples`) and are skipped when it is missing. `tests/fp_cases.py` holds a false-positive regression corpus; `python3 tools/accuracy/fp_corpus.py OUT_DIR` writes those files and prints how each one scores. Progress is tracked in [todo.md](todo.md).

## Contributing

Requirements and design decisions are in [spec/requirements.md](spec/requirements.md). Issues and pull requests are welcome.
