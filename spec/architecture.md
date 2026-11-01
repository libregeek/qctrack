# qctrack Architecture

Nov 1, 2026 · Phase 1 · Based on [requirements.md](requirements.md) and [quality-evaluation-matrix.md](quality-evaluation-matrix.md)

## 1. Summary

qctrack is a Python application that decodes audio with each format's dedicated C library, called through `ctypes`. It analyses the samples with NumPy, reads tags and headers with Mutagen, and measures loudness with `libebur128`. It stores what it measures, then computes scores from those measurements as a separate, pure step.

Four decisions shape the design:

| Decision | Reason |
| --- | --- |
| Use the dedicated decoder libraries | They were the most accurate in testing (section 6.4), and accuracy is the first priority. They also install in about 3 MB. |
| Write the application in Python | The results match any other language because the same C libraries do the work. Python makes the rules quicker to tune, which is the main way to reduce false positives, and it has the simplest Fedora packaging. |
| Analyse each file in a worker process | A crash or hang in a C decoder must cost one file, not the scan (REQ-ANL-03, REQ-NFR-05). |
| Keep measuring and scoring separate | `rescore` must work without touching audio (REQ-PRF-07), and every score must be explainable from stored values (REQ-NFR-11). |

Priorities, in order: accuracy, few false positives, small footprint, Fedora compatibility. Memory safety and scan speed rank lower because the tool is run occasionally. Fedora is the target and Ubuntu may follow.

## 2. Dependency rules

Every dependency must meet all of these:

1. It is free and open source, with a license compatible with GPLv3. Nothing proprietary, including at build or test time.
2. It is packaged in the official Fedora repositories. RPM Fusion and COPR do not count.
3. It is actively maintained upstream. Section 6 records the one accepted exception, `libebur128`.
4. It is checked for before any functionality is written. Code is written in-house only when no qualifying library exists, and section 7 records why.

Because footprint matters, a dependency must also earn its size. The Python standard library comes first, and SciPy (68 MB) is not used.

No Fedora-specific code goes into the package, so the Fedora rule affects only which versions and packages are used. Native libraries are loaded by their upstream library names, which keeps an Ubuntu port simple.

## 3. Project layout

```text
qctrack/
├── pyproject.toml             package metadata, hatchling build
├── qctrack.spec               Fedora RPM spec
├── src/qctrack/
│   ├── native/                all ctypes code: one module per C library
│   ├── core/                  detection, scanners, factors, scoring, profiles
│   ├── report/                HTML, CSV and JSON writers
│   ├── cli/                   arguments, discovery, workers, cache, progress
│   └── data/default-profile.toml
└── tests/
```

| Package | Responsibility | Depends on |
| --- | --- | --- |
| `qctrack.native` | `ctypes` signatures and small wrappers that always free their handles | Native libraries |
| `qctrack.core` | Format detection, passes 1–3, factors, profiles, scoring. No terminal or process code, so a later GUI can reuse it (REQ-PLT-02). | `native`, NumPy, Mutagen |
| `qctrack.report` | Report writers | `core`, Jinja2, Pillow |
| `qctrack.cli` | Arguments, file discovery, worker pool, cache, progress, exit codes | `core`, `report`, tqdm |

## 4. Runtime design

```text
                         qctrack (parent process)
   ┌───────────────────────────────────────────────────────────────┐
   │ argparse ─► os.walk ─► cache lookup ─► job queue ─► results   │
   │                        (sqlite3)          │            │      │
   └───────────────────────────────────────────┼────────────┼──────┘
                      file path over pipe      │            │ measurements
                                               ▼            │ over pipe
   ┌───────────────────────────────────────────────────┐    │
   │ worker × N   (time and memory limits)             │────┘
   │  Pass 1: detect format, scan raw bytes and headers│
   │  Pass 2: decode once, stream PCM to every factor  │
   └───────────────────────────────────────────────────┘

   Measurements ─► Pass 3: derived factors, profile, gates, rating
                      └─► screen output, report.html, report.csv, report.json
```

### 4.1 Pipeline

1. **Detect:** in-house code reads the file's leading bytes and, for Ogg, the first packet of each stream. The file extension is never consulted to decide a format (REQ-FMT-04), so Mutagen's name-based `File()` helper is not used. When scanning a folder, a fixed list of audio extensions only chooses which files to examine; files named on the command line are always examined. Opus, FLAC and Speex streams become Unsupported in phase 1 (REQ-FMT-02).
2. **Pass 1:** a per-codec scanner walks the raw bytes and headers, and Mutagen reads tags and encoder details. `--quick` stops here (REQ-ANL-02).
3. **Pass 2:** decode once and send fixed-size NumPy blocks to each `PcmFactor`, so memory stays bounded (REQ-NFR-03).
4. **Pass 3:** in the parent, compute derived factors and apply the profile. This step is a pure function of the measurements and the profile.

### 4.2 Core types

```python
@dataclass(frozen=True)
class Measurement:
    """One raw result per factor, serialised into report.json and the cache."""
    factor: FactorId
    value: MeasuredValue

class PcmFactor(Protocol):
    """Receives every decoded block; memory use must not grow with track length."""
    def process(self, block: npt.NDArray[np.float32]) -> None: ...
    def finish(self) -> Measurement: ...

def score(measurements: Sequence[Measurement], profile: Profile) -> TrackScore:
    """Pure: the same inputs always give the same output (REQ-ANL-05)."""
```

`rescore` reads the measurements from `report.json` and calls `score` again, so it needs no audio and no worker.

### 4.3 Reading files safely

- **Read-only:** Python and the decoder libraries open files read-only (REQ-ACC-01, REQ-ACC-03). No code path opens an analysed file for writing.
- **No network:** nothing in the application or its dependencies is asked to use the network, and no network module is imported (REQ-NFR-07).
- **Quiet decoders:** `libmpg123` runs with `MPG123_QUIET`, so errors arrive as return codes rather than terminal output.
- **No gain applied:** `libmpg123` runs with its RVA setting off, so stored gain never alters measured samples. A wrong setting here changed a measured peak from 1.25 to 1.13 in the prototype, so a test must cover it.

### 4.4 Worker isolation

Workers use `multiprocessing` with a `forkserver` that preloads NumPy, so each worker starts quickly and shares those pages.

| Control | How | Requirement |
| --- | --- | --- |
| Crash containment | If a worker dies, its file becomes Error and the parent starts a new worker | REQ-ANL-03, REQ-NFR-05 |
| Time budget | The parent kills a worker that exceeds the per-file limit | REQ-NFR-05 |
| Memory budget | `RLIMIT_AS` on each worker, through the standard `resource` module | REQ-NFR-03, REQ-NFR-05 |

Each worker is single-threaded, with the BLAS thread count set to 1. Parallelism comes from the number of workers, which keeps the budgets per file.

There is no seccomp sandbox. It would add complexity for a tool that runs occasionally, and memory safety ranks below accuracy for this project. A decoder bug triggered by a hostile file would therefore run with the user's full permissions, which is an accepted risk (Q-A3).

### 4.5 Cache, resume and scale

- **Location:** an SQLite database at `$XDG_DATA_HOME/qctrack/cache.db`, never beside the audio (REQ-ACC-04). The standard `sqlite3` module provides it.
- **Key:** path, size, modification time and analyser version (REQ-NFR-15).
- **Value:** measurements only, never scores, so a profile change never invalidates the cache.
- **Resume:** each result is committed as it arrives, so an interrupted scan restarts where it stopped. Ctrl-C finishes the write in progress and exits.
- **Scale:** results go to SQLite and the reports stream from it in path order, so parent memory does not grow with library size (REQ-NFR-14).

### 4.6 Storage-aware parallelism

The default for `--jobs` comes from `/proc/self/mountinfo` and `/sys/block/*/queue/rotational` (REQ-NFR-16):

| Storage | Default workers |
| --- | --- |
| SSD or NVMe | One per CPU core |
| Spinning disk or network share | One or two |
| Unknown | The conservative setting above |

### 4.7 Determinism

- Results are sorted by path, regardless of the order workers finish in.
- Measured values are rounded to a fixed precision per factor before scoring, so small floating-point differences between CPUs do not change a score (REQ-NFR-10).
- Python's `json` round-trips floats exactly, so `rescore` sees exactly the stored values.
- `libmpg123` and `libvorbisfile` output `f32` samples that keep values above full scale, which F2.07 and F2.08 need. Through `ctypes`, test files boosted into clipping decoded with peaks of 1.2537 (MP3) and 1.5351 (Vorbis).

### 4.8 Footprint

Measured on about 4 minutes of audio with Fedora's packages, using the matrix's methods for F2.07–F2.12 and F3.01. Spectrogram images, frame scanning and the other factors are not included.

| Item | Result | Target |
| --- | --- | --- |
| Peak memory per worker | About 50 MB with a 64 KB block size | Under 150 MB |
| Time, MP3 | 2.62 s | At most 3 s (REQ-NFR-02) |
| Time, Vorbis | 2.94 s | At most 3 s (REQ-NFR-02) |
| Share used by true peak | About 1 s | |
| Extra disk on the development machine | 44 MiB for NumPy, which this machine lacks | |
| Disk on an empty Fedora root | 228 MiB for NumPy, Mutagen and Jinja2, including Python and glibc | |

The empty-root figure is the worst case. A normal Fedora system already has Python and glibc, and how much of the rest it has varies.

Block size is the main memory control: a 1 MB block used 91 MB in an earlier test. Blocks are therefore fixed at 64 KB. Vorbis output must be gathered into these blocks, because passing the decoder's small chunks straight to NumPy took 4.31 s rather than 2.94 s.

## 5. Factor implementation

This covers the phase 1 factors. F1.11 and F3.10 are FLAC only and arrive in phase 2. F2.11, F2.13, F3.05 and F3.07 have Later priority and plug into the same `PcmFactor` interface.

| Factors | Source |
| --- | --- |
| F1.10 Real format | In-house detection (section 4.1) |
| F1.01–F1.04 Bitrate, mode, codec, channels | Mutagen, plus the in-house scanners |
| F1.05–F1.07 Encoder, quality, lowpass | Mutagen for encoder details and the Vorbis vendor string; a small in-house parser for LAME fields Mutagen does not expose, such as lowpass. Frame-pattern fingerprinting for files with no encoder string is left for later. |
| F1.08 Declared vs counted length | Scanner frame or granule count against the declared length, not the decoder's output length |
| F1.09 Tags and stored gain | Mutagen |
| F2.01–F2.03 Structure, truncation, unidentified data | In-house MP3 scanner; `libogg` for Ogg pages. Known tags, including Lyrics3 and APE, count as metadata rather than damage. |
| F2.04 Embedded audio checksum | CRC-16 over the audio, using `python3-crcmod` if it qualifies (Q-A6) |
| F2.05 Block-level CRC | CRC-16 for MP3 frames; `libogg` page checks for Ogg |
| F2.06 Decode errors | `libmpg123` and `libvorbisfile` return codes, classified by the scanner so that known tags do not count as errors |
| F2.07, F2.12 Clipping, channel anomalies | In-house NumPy analysis |
| F2.08–F2.10 Peak, loudness, dynamic range | `libebur128`: true peak, integrated loudness, loudness range. Peak-to-loudness ratio is derived from these. |
| F2.14 Ogg stream layout | `libogg` |
| F3.01, F3.04, F3.06 Cutoff, sharpness, upsampling | In-house analysis on `numpy.fft` |
| F3.02, F3.03, F3.09 Derived factors | Pure functions over the measurements |
| F3.08 Spectrogram | `numpy.fft`, with Pillow for the image |

## 6. Libraries

Versions are those in Fedora 44 on 2026-11-01. "Latest activity" is the most recent upstream commit found that day, or the latest upstream release for `mpg123`. Install sizes come from the Fedora package metadata. All licenses below are compatible with GPLv3.

### 6.1 Native libraries

| Library | Fedora package | Version | License | Latest activity | Used for |
| --- | --- | --- | --- | --- | --- |
| libmpg123 | `mpg123-libs` | 1.32.10 | GPL-2.0-or-later | 2026-08-02 (upstream 1.33.7) | MP3 decoding |
| libvorbis, libvorbisfile | `libvorbis` | 1.3.7 | BSD-3-Clause | 2026-08-04 | Vorbis decoding |
| libogg | `libogg` | 1.3.6 | BSD-3-Clause | 2026-03-02 | Ogg page parsing and CRC checks |
| libebur128 | `libebur128` | 1.2.6 | MIT | 2021-02-14 | Loudness, true peak, loudness range |
| libFLAC (phase 2) | `flac-libs` | 1.5.0 | BSD-3-Clause and others (per Fedora) | 2026-10-28 | FLAC decoding, MD5 check |
| libopus (phase 2) | `opus` | 1.6 | BSD-3-Clause AND BSD-2-Clause | 2026-10-29 | Opus decoding, with packets from `libogg` |

**`libebur128` exception:** it has had no upstream commit since 2021, so it does not meet the "actively maintained" rule. It is accepted for now because the EBU R128 method is stable, it is very small, and the only maintained alternative found in Fedora is FFmpeg's much larger filter library. Its integrated loudness and loudness range matched FFmpeg's on the test file: −14.95 LUFS and 3.66 LU, against −15.0 and 3.7 reported by FFmpeg. Its true peak of 0.9825 (−0.15 dBFS) matched FFmpeg's −0.2 dBFS.

`libFLAC` is loaded by a name that includes its major version, currently `libFLAC.so.14`, so phase 2 needs to handle that version changing.

### 6.2 Python packages: runtime

| Package | Fedora package | Version | License | Latest activity | Used for |
| --- | --- | --- | --- | --- | --- |
| NumPy | `python3-numpy` | 2.4.6 | BSD-3-Clause AND MIT AND Apache-2.0 AND (Zlib OR BSL-1.0) | 2026-11-01 | Sample analysis, FFT |
| Mutagen | `python3-mutagen` | 1.47.0 | GPL-2.0-or-later AND MIT | 2026-08-20 | Tags, encoder details, stream headers |
| Jinja2 | `python3-jinja2` | 3.1.6 | BSD-3-Clause | 2025-06-14 | HTML templates with automatic escaping |
| tqdm | `python3-tqdm` | 4.69.0 | MPL-2.0 AND MIT (per Fedora) | 2026-09-11 | Progress on standard error |
| Pillow | `python3-pillow` | 12.3.0 | MIT | 2026-11-01 | Spectrogram images. Imported only with `--spectrograms`, and a weak RPM dependency so minimal installs can skip it. |

**From the standard library:** `argparse`, `tomllib`, `csv`, `json`, `sqlite3`, `hashlib`, `multiprocessing`, `resource`, `ctypes`, `gettext`, `base64`, `html`.

Jinja2 is the weakest fit for the "actively maintained" rule, with no commit since June 2025. It is widely used and its escaping supports REQ-HTM-07, so it stays unless it goes quiet for longer.

### 6.3 Build and test tools

| Tool | Fedora package | Version | License | Latest activity | Used for |
| --- | --- | --- | --- | --- | --- |
| RPM macros | `pyproject-rpm-macros` | 1.24.0 | MIT | Not checked | Building the RPM |
| hatchling | `python3-hatchling` | 1.31.0 | MIT | Not checked | Package build backend |
| argparse-manpage | `python3-argparse-manpage` | 4.7 | Apache-2.0 | 2026-10-14 | Man page (REQ-CLI-13) |
| pytest | `python3-pytest` | 8.4.2 | MIT | 2026-10-30 | Tests |
| Hypothesis | `python3-hypothesis` | 6.123.0 | MPL-2.0 | 2026-10-30 | Scoring and parser properties, fuzz-style input |
| mypy | `python3-mypy` | 1.18.2 | MIT and Apache-2.0 | 2026-10-30 | Type checks |
| Ruff | `ruff` | 0.16.10 | MIT and others (per Fedora) | 2026-11-01 | Lint and formatting |
| FFmpeg | `ffmpeg-free` | 8.1.3 | GPL-3.0-or-later | 2026-11-01 | Test-only cross-check of decoded audio and loudness |

### 6.4 How the decoder was chosen

Tests used FFmpeg's reference files and 400 MP3s plus 17 Ogg files from a real library. Symphonia 0.6.1 and FFmpeg 8.1.3 ran as builds or commands on this machine. The dedicated decoders were tested through the `mpg123`, `oggdec` and `flac` commands, and `libopus` through FFmpeg.

| Test | Symphonia | FFmpeg | Dedicated libraries |
| --- | --- | --- | --- |
| MP3: 11 ISO files | 7 match | 8 match | 10 match |
| Vorbis: 20 reference files | 18 match | 20 match | 20 match |
| FLAC: 32-bit stereo, 3 files | 1 correct | 3 correct | 3 correct |
| Opus: RFC 8251 vectors | No decoder | Native decoder poor on SILK and hybrid; `libopus` good | `libopus` good |
| Real MP3s, against FFmpeg | 399 of 400 at 90 dB or better | Reference | All 400 at 116 dB or better |
| Install size | About 3 MB binary | About 250 MB, 133 packages on the test machine | About 3 MB of libraries |
| In Fedora | No | Yes | Yes |

The FFmpeg size includes RPM Fusion's `libavcodec-freeworld`, so it is an upper estimate. The test machine has that package installed.

**Verdicts from the matrix:** F2.07–F2.12 and F3.01 were then computed by the matrix's methods through each decoder, on 100 MP3s and 17 Ogg files. No verdict differed between any two decoders, out of 819 per pair, and the largest cutoff difference was 11 Hz. The cutoff rule was a prototype, and the other PCM factors were not compared.

So on ordinary files the decoder does not change these verdicts, although PCM factors carry about 58% of the final score's weight. The choice of decoder rests on four other points:

- **Rare valid files:** Symphonia misdecodes some with no error, which would produce a wrong verdict.
- **F2.06 decode errors:** this is the factor most affected by the decoder. On 400 MP3s, `libmpg123` warned on 22, FFmpeg on 12 and Symphonia on none, so its raw count is not comparable across decoders and qctrack needs the scanner to classify errors.
- **Later formats:** `libFLAC` provides the MD5 check for F2.04 and `libopus` handles Opus, which Symphonia cannot decode.
- **Footprint and Fedora:** see the table above.

**Why Python stays:** in the full pass most time goes to C code. True peak alone takes about 1 s of the 2.62 s, and `mpg123` decoding takes about 0.3 s, so Rust with the same libraries would save less than first estimated. Python's weak spot is the frame scanner at about 0.1 s per file, which is small within a full scan. Most factors with medium or high false-verdict risk are heuristics that need repeated tuning, which suits Python.

Vector 12 of the Opus set scored about 36 dB for `libopus`, `opus-decoder` and `ropus`, which points to that reference or the test setup rather than the decoders. The official `opus_compare` tool was not run.

### 6.5 Considered and rejected

| Candidate | Why not |
| --- | --- |
| Symphonia, with Rust | Misdecodes some valid files with no error, has no Opus decoder and is not in Fedora. |
| FFmpeg | Accurate, but its install is far larger, and its native Opus decoder is weak unless `libopus` is selected. |
| Rust with the same libraries | Smaller and probably faster, but slower to tune and harder to package for Fedora. Gives the same audio results. |
| `python3-soundfile` with `libsndfile` | One API over similar decoders, but not tested. Direct bindings give the error codes F2.06 needs. |
| SciPy | 68 MB, and NumPy covers what is needed. |
| PyPNG | No upstream commit since April 2023. |
| `pyloudnorm`, `pyebur128`, Atheris | Not in Fedora. |
| `python3-libseccomp` | Not found in Fedora under this name, and the sandbox is not used (section 4.4). |
| Rich | Larger than tqdm for what is needed. |

## 7. Written in-house

No qualifying library was found for these, so they are project code.

| Component | Why no library |
| --- | --- |
| `ctypes` wrappers for native libraries | No Python binding package for `mpg123`, Vorbis, Ogg, `libebur128`, FLAC or Opus was found in Fedora on 2026-11-01. `python3-audioread` is packaged, but it only hands back decoded audio from whichever backend it finds. Kept to the small API surface qctrack needs. |
| Format detection | Must report Opus, FLAC and Speex inside Ogg as REQ-FMT-02 requires, and must never use the file name. |
| MP3 frame scanner and LAME field parser | Decoder libraries hide the frame-level detail F1.06–F1.08 and F2.01–F2.05 need, such as junk bytes and per-frame CRCs. |
| Spectral factors (F3.01, F3.04, F3.06) | These are qctrack's own heuristics, built on `numpy.fft`. |
| Clipping and channel analysis (F2.07, F2.12) | Short NumPy operations, with definitions specific to qctrack. |
| Scoring, gates and profile merging | Core project logic defined in section 5 of the requirements. |
| `--html[=FILE]` handling | `argparse` would take the next path as the file name, which REQ-CLI-03 forbids. A few lines expand the bare option before parsing. |
| Line numbers in profile errors | `tomllib` reports lines only for syntax errors, and REQ-PRF-05 needs them for invalid values too. |
| HTML sorting and filtering script | Plain JavaScript inlined in the report, since it must work offline with no network requests (REQ-HTM-01). |

## 8. Testing

| Layer | Approach |
| --- | --- |
| Scoring | Unit tests from the requirements tables; Hypothesis properties for score range and renormalisation |
| Native bindings | Every wrapper tested for correct signatures and for handles being freed, since a `ctypes` mistake is not caught at compile time |
| Factors | Small generated fixtures with known defects, committed under `tests/fixtures` |
| Decoder accuracy | FFmpeg's reference files, downloaded for testing rather than committed, compared against reference output |
| CLI | Exit codes, output options and overwriting, through `subprocess` |
| Reports | Golden-file tests; tag-injection tests for HTML and formula-injection tests for CSV |
| Workers | Tests that a worker which crashes, hangs or exceeds its memory limit costs one file, not the scan |
| Fuzzing | Hypothesis and a corrupted-file corpus over detection, scanners and the decode path |
| Accuracy | Labelled corpus (T10), cross-checked with the tools in section 8.3 of the requirements |

`mypy` runs in strict mode on all packages.

## 9. Build and packaging

No compile step is needed. To run from source on Fedora:

```bash
sudo dnf install python3-numpy python3-mutagen python3-jinja2 python3-tqdm \
    mpg123-libs libvorbis libogg libebur128
PYTHONPATH=src python3 -m qctrack scan ~/Music
```

Add `python3-pillow` to use `--spectrograms`.

**Distribution:** phase 1 ships a source release and a Fedora RPM built with `pyproject-rpm-macros`. The RPM is `noarch`, because it contains only Python code, so one package serves x86-64 and aarch64 (REQ-PLT-01). It declares the native libraries as explicit dependencies, because `ctypes` loading hides them from automatic detection. There is no single prebuilt binary.

**Ubuntu (later):** the code loads libraries by name and uses no Fedora-specific paths. Package names and versions need their own review at that point, and exact suites have not been checked.

## 10. Requirements coverage

| Requirement area | Addressed by |
| --- | --- |
| REQ-PH, REQ-PLT, REQ-FMT | Sections 3, 4.1 |
| REQ-ACC | Sections 4.3, 4.5 |
| REQ-ANL | Sections 4.1, 4.4, 4.5, 4.7 |
| REQ-FAC | Sections 4.2, 5 |
| REQ-CLI | Sections 3, 4.2, 4.6, 7 |
| REQ-SCR, REQ-PRF | Sections 4.2, 6.2, 7; profiles load through `tomllib` over the packaged default |
| REQ-HTM, REQ-CSV | Sections 6.2, 7; Jinja2 escapes tag text with autoescape on |
| REQ-NFR | Sections 1, 2, 4.3–4.8, 8, 9 |

## 11. Open items and risks

| ID | Item | Proposed action |
| --- | --- | --- |
| Q-A1 | Fedora ships `mpg123` 1.32.10, while upstream is at 1.33.7. | Accept Fedora's version. Re-run the accuracy tests on each new Fedora release. |
| Q-A2 | A full scan is slow. A library of 16,178 MP3s would take roughly 4 hours on 4 cores, of which true peak is about 1.3 hours. This is an estimate from the 4-minute test. It leaves out spectrogram images and disk delays: reading only tags from that library took 8.4 minutes on a cold cache. The cache means each file is analysed only once. | Keep true peak on, because accuracy comes first. Consider an option to skip it if the time becomes a problem. |
| Q-A3 | C decoders read untrusted files with no sandbox. | Accepted for occasional use on the user's own files. Revisit if the tool is used on files from unknown sources. |
| Q-A4 | `libmpg123` and `libvorbisfile` decoding through `ctypes` agree with FFmpeg at 122 dB and 136 dB on one test file each. Still unproven: `libogg` page parsing and error reporting, how `libmpg123` reports errors, and the conformance files through the bindings rather than the command-line tools. | Build a vertical slice for one MP3 and one Vorbis file before implementing the full factor list, and repeat the accuracy tests through the bindings. |
| Q-A5 | F3.01 needs a precise rule for "a sustained energy rise". The prototype's rule marked 74 of 100 real MP3s Poor, though half of that library is 128 kbps. | Define and tune the rule with T10 and T11. |
| Q-A6 | The LAME music CRC covers the whole audio stream, and a pure Python loop may be slow. `python3-crcmod` 1.7 is in Fedora, but its upstream activity has not been checked. | Check it and measure both when F2.04 is built. |
| Q-A7 | `sin1k0db` peaks at 1.18 in FFmpeg and Symphonia but 1.03 in `mpg123`, and the reference matches `mpg123`. The cause is unknown. | Investigate with the labelled corpus, since the difference could affect clipping verdicts. |
| Q-A8 | The Python frame scanner manages about 10 files per second per core. | Accepted: quick scan is not a priority (REQ-NFR-01). A compiled helper is possible later. |
| Q-A9 | `libebur128` (last commit 2021-02-14) and Jinja2 (last commit 2025-06-14) are the weakest fits for the "actively maintained" rule. | Keep both for now and review each Fedora release. |
| Q-A10 | The matrix's F2.08 thresholds may be too strict for lossy files, because decoding overshoots the original peak. True peak above +0.5 dBTP, a Fail, was found in 43 of 100 real MP3s and 13 of 17 Ogg files. | Check against the labelled corpus. Consider codec-specific thresholds. |
| Q-A11 | The matrix's Vorbis and Opus F1.01 thresholds still have three bands, and only MP3 uses the new four. | Decide when the Vorbis and Opus tables are built (T11). |
