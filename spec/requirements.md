# qctrack Requirements

Nov 1, 2026 · @Sasikala

## 1. Purpose and scope

Phase 1 delivers a command-line utility (qctrack) that gives every audio track a weighted quality score (0–100) and a rating built from measurable quality factors, and writes the results as a self-contained HTML report and a CSV file. A default set of weights ships with the package, and users can change the weights later. The project is fully free and open source software (FOSS) and never modifies, plays or uploads the analysed files. A GUI front end follows in a later phase on the same analysis core.

**Goals**

- Measure technical and perceptual-proxy quality of tracks without listening to them.
- Detect fake-bitrate files (low-quality sources re-encoded to a higher bitrate) and, from release 2, fake lossless files.
- Explain every score: each rating traces back to factor scores and measured values.
- Write per-track results as an HTML report and as CSV, with album, title, file path, factor scores and the final weighted score.

**Out of scope**

- Editing, repairing, re-tagging or converting files (tools like MP3val can fix files; this app must not).
- Audio playback as part of analysis.
- Subjective judgements of musical or mastering taste.
- Full-reference perceptual metrics (PEAQ, ViSQOL) that need the original lossless master. These may be reconsidered later as an optional feature.

## 2. Definitions

| Term | Meaning |
| --- | --- |
| Objective | One of three quality areas: O1 Metadata & encoding, O2 Technical integrity, O3 Authenticity & transcode. |
| Factor | A single measurable property within an objective, identified as F\<objective>.\<nn> (e.g. F3.01 Measured spectral cutoff). |
| Measured value | The raw result of a factor (e.g. 16 200 Hz, −8.4 LUFS, 3 clipped runs). |
| Verdict | Pass, Warn, Fail or Info, derived from the measured value and the factor's thresholds. |
| Factor score | 0–100 value derived from the measured value. Info factors are reported but not scored. |
| Weight | Relative importance of a scored factor within its objective, and of each objective in the final score. |
| Objective score | Weighted average of the factor scores in one objective (0–100). |
| Final weighted score | Weighted average of the three objective scores after gating rules (0–100). |
| Rating | Grade A–F derived from the final weighted score. |
| Coverage | Share of total weight that was actually evaluated for a track (factors not applicable to the codec are excluded). |
| Scoring profile | A versioned set of weights, thresholds and gates. The app ships a default profile; users can load their own. |
| Decode | Silent, in-memory conversion of the audio to PCM samples for analysis. Not playback. |

## 3. Phases, platforms and formats

Phase 1 is a Linux command-line utility that analyses MP3 and Ogg Vorbis and writes HTML and CSV reports; FLAC, Opus and a GUI come later.

| Phase | Deliverable | Platforms | Formats | Outputs |
| --- | --- | --- | --- | --- |
| 1 | CLI utility with bundled default weights | Linux (other desktop OSes: Q9) | MP3, Ogg Vorbis | HTML, CSV |
| 2 | Lossless and modern codec support | Same as phase 1 | + FLAC, Opus | HTML, CSV |
| 3 | Desktop GUI on the same core | Linux | All | In-app report, HTML, CSV |

The order of phases 2 and 3 is open (Q10). Fedora is the target and Ubuntu may follow.

| Format | Phase | Containers / extensions | Notes |
| --- | --- | --- | --- |
| MP3 | 1 | .mp3 | MPEG-1/2/2.5 Layer III; LAME/Xing/Info tags carry encoder, preset and lowpass. |
| Ogg Vorbis | 1 | .ogg, .oga | Vendor string and Vorbis comments; no stored preset or lowpass, so quality is estimated from bitrate and spectrum. |
| FLAC | 2 | .flac, Ogg FLAC | Focus shifts to "is this really lossless": MD5 check, fake-lossless and fake bit-depth detection. |
| Opus | 2 | .opus, often .ogg | Always decodes at 48 kHz; each packet declares its bandwidth. |

- **REQ-PH-01** Phase 1 shall be a command-line utility only, with no GUI.
- **REQ-PLT-01** Phase 1 shall run on Linux x86-64 and aarch64.
- **REQ-PLT-02** The analysis core shall be a library separate from the CLI, so the phase 3 GUI reuses it unchanged.
- **REQ-FMT-01** Phase 1 shall fully analyse MP3 and Ogg Vorbis files.
- **REQ-FMT-02** Phase 1 shall detect Opus, FLAC and Speex streams (including Opus or FLAC inside .ogg files) and report them as "Supported in a later release", without scoring them and without misreporting them as Vorbis.
- **REQ-FMT-03** Phase 2 shall add full analysis of FLAC (native and Ogg) and Opus.
- **REQ-FMT-04** The format shall be determined from file content, never from the file extension.

## 4. Functional requirements

### 4.1 File access and safety

- **REQ-ACC-01** Files shall be opened read-only. The app shall never request write access to analysed files or their folders.
- **REQ-ACC-03** On Linux, files shall be opened with read-only flags.
- **REQ-ACC-04** The app shall not write temporary files next to analysed files. Any cache shall live in the app's own data directory.
- **REQ-ACC-05** The app may compute a SHA-256 of each file before and after analysis and include it in the report, so users can verify files are unchanged.
- **REQ-ACC-06** The user shall be able to analyse single files, multiple files, or a folder recursively.

### 4.2 Analysis pipeline

1. **Detect** the real format from file content (F1.10) and route to the codec's parser.
2. **Pass 1 (no decode):** evaluate all header and raw-byte factors.
3. **Pass 2 (silent decode):** decode once and stream samples to every PCM factor (spectrum, loudness, clipping, channels).
4. **Pass 3 (derived):** evaluate derived factors, apply the scoring profile, compute objective scores, final score and rating.

- **REQ-ANL-01** Passes shall run in the background, show progress, and be cancellable and resumable for large libraries.
- **REQ-ANL-02** The user shall be able to run a quick scan (pass 1 only) or a full scan (all passes). Quick-scan ratings shall be marked provisional.
- **REQ-ANL-03** A failure in one file shall not stop the scan; the file is reported with an error status.
- **REQ-ANL-04** Codec-specific methods and thresholds shall be used where they differ (e.g. MP3 and Vorbis need separate cutoff-to-quality tables).
- **REQ-ANL-05** Results shall be deterministic: the same file and the same scoring profile always give the same scores.
- **REQ-ANL-06** For each track the HTML report shall include a detail section with every factor's measured value, verdict, score, weight and a spectrogram (F3.08).

### 4.3 Factor catalogue

Formats: MP3, VRB (Ogg Vorbis), FLC (FLAC), OPS (Opus); a format in brackets uses an adapted method. Input: H = header, B = raw bytes, P = decoded PCM, D = derived. Full method and threshold details are kept in the companion [MP3 Quality Evaluation Matrix](quality-evaluation-matrix.md).

| ID | Factor | Formats | Input | Priority |
| --- | --- | --- | --- | --- |
| F1.01 | Nominal & average bitrate | MP3, VRB, (FLC), OPS | H | MVP |
| F1.02 | Bitrate mode | MP3, (VRB), OPS | H | MVP |
| F1.03 | Codec version & sample rate | MP3, VRB, FLC, (OPS) | H | MVP |
| F1.04 | Channel layout | All | H | MVP |
| F1.05 | Encoder identity & version | All | H | v1 |
| F1.06 | Encoder quality setting | MP3, (VRB), (OPS) | H | v1 |
| F1.07 | Declared bandwidth / lowpass | MP3, OPS | H | v1 |
| F1.08 | Declared vs counted length | All | H | MVP |
| F1.09 | Tags & stored gain | All | H | v1 |
| F1.10 | Real format detection | All | B | MVP |
| F1.11 | Bit depth | FLC | H | MVP |
| F2.01 | Stream structure validity | All | B | MVP |
| F2.02 | Truncation | All | B | MVP |
| F2.03 | Unidentified data | All | B | MVP |
| F2.04 | Embedded audio checksum | MP3, FLC | B (FLC: P) | v1 |
| F2.05 | Block-level CRC | (MP3), VRB, FLC, OPS | B | MVP |
| F2.06 | Decode errors | All | P | MVP |
| F2.07 | Clipping | All | P | MVP |
| F2.08 | Peak & true peak | All | P | v1 |
| F2.09 | Integrated loudness | All | P | MVP |
| F2.10 | Dynamic range | All | P | v1 |
| F2.11 | DC offset | All | P | Later |
| F2.12 | Channel anomalies | All | P | v1 |
| F2.13 | Silence & dropouts | All | P | Later |
| F2.14 | Ogg stream layout | VRB, (FLC), OPS | H | MVP |
| F3.01 | Measured spectral cutoff | All | P | MVP |
| F3.02 | Claimed vs effective quality | All | D | MVP |
| F3.03 | Declared vs measured bandwidth | MP3, OPS | D | v1 |
| F3.04 | Cutoff sharpness | All | P | v1 |
| F3.05 | High-band holes & flicker | MP3, VRB, FLC, (OPS) | P | Later |
| F3.06 | Upsampling detection | MP3, VRB, FLC, (OPS) | P | v1 |
| F3.07 | Double-compression markers | MP3, (VRB), (FLC), (OPS) | P | Later |
| F3.08 | Spectrogram view | All | P | MVP |
| F3.09 | Authenticity sub-score | All | D | v1 |
| F3.10 | Fake bit depth | FLC | P | v1 |

- **REQ-FAC-01** Every factor in the catalogue shall output a measured value, a verdict and, if scored, a factor score.
- **REQ-FAC-02** Factors that do not apply to a track's codec shall be marked N/A and excluded from scoring.
- **REQ-FAC-03** F3.09 is now the O3 objective score defined in section 5, not a separate rule set.

### 4.4 Command-line interface (phase 1)

Typical use:

```bash
qctrack scan ~/Music                          # recursive; results on screen
qctrack scan ~/Music --html --csv --json      # also writes report.html, report.csv, report.json
qctrack scan ~/Music --html=library.html -o ~/reports --silent
qctrack scan a.mp3 b.ogg --quick --csv=-      # CSV to standard output
qctrack profile show-default > my-weights.toml
qctrack rescore --profile my-weights.toml --html --csv   # reads report.json
```

- **REQ-CLI-01 Inputs** `scan` accepts any mix of files and folders. Folders are always scanned recursively. Symbolic links to folders are not followed unless `--follow-links` is given, to avoid loops.
- **REQ-CLI-02 Screen output (default)** Results are printed on screen: one line per track with file, album, title, final score, rating and gates, followed by a summary of counts per rating, unsupported and errors.
- **REQ-CLI-03 Report files** `--html[=FILE]`, `--csv[=FILE]` and `--json[=FILE]` write report files in any combination, in addition to the screen output. Without a file name they write `report.html`, `report.csv` and `report.json`. A custom name must use the `=` form (`--csv=library.csv`), so an input path is never mistaken for a file name. `--csv=-` writes CSV to standard output, which turns the screen output off.
- **REQ-CLI-04 Output folder** `-o, --output-dir DIR` sets where default-named report files are written; the default is the current folder.
- **REQ-CLI-05 Silent and verbose** `--silent` shows no results or progress on the terminal; errors still go to standard error and the exit code reports the outcome. `-v, --verbose` adds per-factor detail to the screen output.
- **REQ-CLI-06 JSON and rescore** `report.json` holds every measurement, factor score, objective score and the profile used. `rescore [FILE]` reads it (default `report.json` in the output folder) and recomputes scores and reports with another profile, without re-analysing audio. `rescore` takes the same output options as `scan`.
- **REQ-CLI-07 Profile** `--profile PATH` selects a scoring profile; otherwise the user profile (REQ-PRF-03) or the bundled default is used.
- **REQ-CLI-08 Quick scan** `--quick` runs pass 1 only; ratings are marked Provisional.
- **REQ-CLI-09 Parallelism** `-j, --jobs N` sets parallel workers; the default adapts to the storage (REQ-NFR-16). Progress goes to standard error, and only when it is an interactive terminal.
- **REQ-CLI-10 Exit codes** 0 all files analysed; 1 some files were Error or Unsupported; 2 usage or profile error; 3 fatal error such as an unwritable output. `--fail-below SCORE` returns 4 if any track scores below SCORE, for scripted checks.
- **REQ-CLI-11 Spectrograms** `--spectrograms` embeds per-track spectrogram images in the HTML report; off by default.
- **REQ-CLI-12 Overwriting** Existing report files with the same name are overwritten without asking.
- **REQ-CLI-13 Help** `--help` on every command, a `--version` flag, and a man page.

## 5. Scoring and rating model

Each track gets a factor score per applicable factor, a score per objective, and a final weighted score of 0–100 mapped to a rating of A–F. The weights below are the proposed defaults and need approval (todo T06).

### 5.1 Factor scores

- **REQ-SCR-01 Numeric factors** are scored piecewise-linearly: a value at or better than the Pass threshold scores 100; a value on the Warn/Fail boundary scores 50; a value at or worse than the Fail threshold scores 0; values in between are interpolated.
- **REQ-SCR-02 Categorical factors** score Pass = 100, Warn = 50, Fail = 0.
- **REQ-SCR-05 Banded factors** may set more anchor points than REQ-SCR-01, with scores interpolated between them. MP3 F1.01 uses four bands: High (≥ 256 kbps or LAME V0–V2, Pass, 100), Medium (160–255 kbps, Warn, 75 at 160), Low (128–159 kbps, Warn, 50 at 128) and Very low (< 128 kbps, Fail, 0 at 64 or below). The bands are defined in `quality-evaluation-matrix.md`.
- **REQ-SCR-03 Info factors** are measured and reported but have weight 0 and never affect the score.
- **REQ-SCR-04** N/A factors (not applicable to the codec) and factors not evaluated (e.g. in a quick scan) are excluded, and the remaining weights are renormalised.

### 5.2 Objective and final scores

Objective score, over the applicable scored factors i in objective k:

```latex
S_k = \frac{\sum_i w_i \, s_i}{\sum_i w_i}
```

Final weighted score, over the objectives, before gating:

```latex
S_{final} = \frac{\sum_k W_k \, S_k}{\sum_k W_k}
```

Coverage shows how much of the applicable weight was actually evaluated:

```latex
C = \frac{\sum_{evaluated} W_k w_i}{\sum_{applicable} W_k w_i}
```

Default objective weights: O1 Metadata & encoding W = 25, O2 Technical integrity W = 30, O3 Authenticity & transcode W = 45. Authenticity carries the most weight because metadata can be faked and integrity issues are rare in practice.

### 5.3 Default factor weights

Weights are relative points within each objective; they are renormalised over the factors that apply to a track.

| Factor | Objective | Weight | Scoring |
| --- | --- | --- | --- |
| F1.01 Nominal & average bitrate | O1 | 30 | Banded (codec table, REQ-SCR-05) |
| F1.06 Encoder quality setting | O1 | 20 | Categorical |
| F1.03 Codec version & sample rate | O1 | 15 | Categorical |
| F1.04 Channel layout | O1 | 10 | Categorical |
| F1.05 Encoder identity & version | O1 | 10 | Categorical |
| F1.08 Declared vs counted length | O1 | 10 | Numeric |
| F1.10 Real format detection | O1 | 5 | Categorical + gate |
| F1.02, F1.07, F1.09, F1.11 | O1 | 0 | Info |
| F2.01 Stream structure validity | O2 | 20 | Numeric + gate |
| F2.02 Truncation | O2 | 15 | Categorical + gate |
| F2.06 Decode errors | O2 | 15 | Numeric |
| F2.04 Embedded audio checksum | O2 | 10 | Categorical + gate (FLAC) |
| F2.05 Block-level CRC | O2 | 10 | Numeric |
| F2.07 Clipping | O2 | 10 | Numeric |
| F2.12 Channel anomalies | O2 | 6 | Categorical |
| F2.03 Unidentified data | O2 | 5 | Numeric |
| F2.14 Ogg stream layout | O2 | 5 | Categorical |
| F2.10 Dynamic range | O2 | 4 | Numeric |
| F2.08 Peak & true peak | O2 | 3 | Numeric |
| F2.13 Silence & dropouts | O2 | 3 | Categorical |
| F2.11 DC offset | O2 | 2 | Numeric |
| F2.09 Integrated loudness | O2 | 0 | Info (genre-dependent) |
| F3.02 Claimed vs effective quality | O3 | 30 | Categorical + gate |
| F3.01 Measured spectral cutoff | O3 | 25 | Numeric (codec table) |
| F3.10 Fake bit depth | O3 | 15 | Categorical |
| F3.03 Declared vs measured bandwidth | O3 | 10 | Numeric |
| F3.04 Cutoff sharpness | O3 | 10 | Numeric |
| F3.05 High-band holes & flicker | O3 | 10 | Numeric |
| F3.06 Upsampling detection | O3 | 10 | Categorical |
| F3.07, F3.08 | O3 | 0 | Experimental / view only |

### 5.4 Gating rules

Some failures make a high score misleading, so they cap the final score regardless of other factors.

| Gate | Condition | Effect |
| --- | --- | --- |
| G1 | F1.10: format not supported in this release | Not scored; status Unsupported |
| G2 | F2.01 or F2.02 Fail (desync or truncation) | Final score capped at 39 (F) |
| G3 | F2.04 Fail on FLAC (MD5 mismatch) | Final score capped at 39 (F) |
| G4 | F3.02 Fail (likely transcode or fake lossless) | Final score capped at 59 (D) |
| G5 | Coverage below 70% | Rating marked Provisional |

An MP3 LAME music-CRC mismatch is not a gate, because lossless edits such as mp3gain also change it; it is reported as "modified since encoding".

### 5.5 Rating bands

| Rating | Final score | Label |
| --- | --- | --- |
| A | 90–100 | Excellent |
| B | 75–89 | Good |
| C | 60–74 | Fair |
| D | 40–59 | Poor |
| F | 0–39 | Bad |

Non-graded statuses: Unsupported, Error (file could not be analysed), Provisional (shown alongside the grade).

### 5.6 Scoring profiles

Users change weights by editing a profile file; the package always carries the default profile.

- **REQ-PRF-01** Weights, thresholds, codec tables, gates and rating bands shall be stored in a versioned, human-readable profile file, not hard-coded. TOML is proposed (Q11).
- **REQ-PRF-02** The default profile shall be packaged with the CLI: shipped as package data (`qctrack/data/default-profile.toml`) and installed as a readable file (e.g. /usr/share/qctrack/default-profile.toml).
- **REQ-PRF-03** Users change weights by copying the default (`profile show-default`) and editing it, then either passing `--profile` or saving it as \~/.config/qctrack/profile.toml, which is used automatically.
- **REQ-PRF-04** A user profile may override only some values; anything it omits falls back to the default profile.
- **REQ-PRF-05** Profiles are validated on load. Unknown factor IDs, negative or non-numeric weights and overlapping rating bands stop the run with an error naming the file and line.
- **REQ-PRF-06** Every report records the profile name, version and a hash of its content.
- **REQ-PRF-07** Changing the profile shall rescore saved measurements without re-analysing files (REQ-CLI-06).

Profile excerpt:

```toml
name = "default"
version = "1.0"

[objectives]
O1 = 25
O2 = 30
O3 = 45

[factors]
"F1.01" = 30
"F3.01" = 25
"F3.02" = 30

[bitrate.mp3]
# average kbps = factor score; scores are interpolated between these points
anchors = { 64 = 0, 128 = 50, 160 = 75, 256 = 100 }

[gates]
G2_cap = 39
G4_cap = 59
G5_min_coverage = 70

[ratings]
A = 90
B = 75
C = 60
D = 40
```

## 6. Reporting: HTML and CSV

The report is one row per track with identity columns, factor scores, objective scores, the final weighted score and the rating, written as a self-contained HTML report and as CSV.

### 6.1 HTML report

- **REQ-HTM-01** The report is a single self-contained HTML file: all styles, scripts and images inline. It opens offline in any current browser and makes no network requests.
- **REQ-HTM-02** A summary section shows track counts per rating, unsupported and error counts, the score distribution, the profile used and the scan date.
- **REQ-HTM-03** A track table lists album, title, artist, file path, codec, final score, rating and gates, and can be sorted and filtered in the browser.
- **REQ-HTM-04** Each track has an expandable detail section: objective scores, and for every factor its measured value, verdict, score, weight and a plain-language explanation.
- **REQ-HTM-05** Spectrogram thumbnails are embedded only with `--spectrograms` (REQ-CLI-11), to keep file size small by default.
- **REQ-HTM-06** The report stays usable for 10 000 tracks: under 20 MB without spectrograms, with responsive sorting and filtering.
- **REQ-HTM-07** All text from tags and file names is escaped, so tag contents cannot inject markup or scripts.
- **REQ-HTM-08** The page prints legibly and follows the viewer's light or dark system theme.

### 6.2 CSV export

- **REQ-CSV-01** The CSV contains one row for every file scanned, including Unsupported and Error rows.
- **REQ-CSV-02** Format: RFC 4180, UTF-8, comma delimiter, header row, CRLF line endings, fields quoted when they contain commas, quotes or line breaks. An option shall add a UTF-8 byte-order mark for spreadsheet compatibility.
- **REQ-CSV-03** Numbers use a dot as the decimal separator; timestamps use ISO 8601 with time zone.
- **REQ-CSV-04** N/A and not-evaluated factors are written as empty cells, never as 0.
- **REQ-CSV-05** Cells starting with =, +, − or @ shall be escaped to prevent formula injection when opened in a spreadsheet.
- **REQ-CSV-06** Factor columns appear in catalogue order and are always present (empty where N/A), so files from different scans line up.
- **REQ-CSV-07** An option shall add a measured-value column next to each factor score.

### 6.3 Column schema

| Column | Example | Notes |
| --- | --- | --- |
| album | Kind of Blue | From tags; empty if missing |
| title | So What | From tags; empty if missing |
| artist | Miles Davis | From tags |
| track\_number | 1 | From tags |
| file\_path | /home/user/Music/Kind of Blue/01 So What.mp3 | Actual file-system path |
| file\_size\_bytes | 8712345 |  |
| sha256 | 3f1a… | Optional (REQ-ACC-05) |
| codec | MP3 | MP3, Vorbis, FLAC, Opus |
| duration\_s | 545.8 |  |
| status | Scored | Scored, Provisional, Unsupported, Error |
| F1\_01\_score … F3\_10\_score | 100 | One column per scored factor, 0–100 |
| F1\_01\_value … F3\_10\_value | 320 kbps | Optional (REQ-CSV-07) |
| O1\_score, O2\_score, O3\_score | 92.0 | Objective scores |
| final\_score | 86.4 | After gates |
| rating | B | A–F |
| gates\_applied | G4 | Semicolon-separated gate IDs |
| coverage\_pct | 100 |  |
| flags | modified\_since\_encoding | Short machine-readable notes |
| profile | default@1.0 | Scoring profile name and version |
| analyzer\_version | 1.0.0 |  |
| analyzed\_at | 2026-11-01T14:30:00+05:30 |  |

### 6.4 Sample

```csv
album,title,artist,track_number,file_path,codec,duration_s,status,F1_01_score,F2_01_score,F3_01_score,F3_02_score,O1_score,O2_score,O3_score,final_score,rating,gates_applied,coverage_pct,profile
Kind of Blue,So What,Miles Davis,1,/home/user/Music/Kind of Blue/01 So What.mp3,MP3,545.8,Scored,100,100,96,100,94.0,98.5,95.2,95.9,A,,100,default@1.0
Unknown Album,Track 07,,7,/home/user/Downloads/track07.mp3,MP3,212.4,Scored,100,100,20,0,81.5,96.0,18.3,57.4,D,G4,100,default@1.0
,,,,/home/user/Music/podcast.opus,Opus,1834.0,Unsupported,,,,,,,,,,G1,0,default@1.0
```

Row 2 shows a 320 kbps file whose content stops near 16 kHz: its O3 score drags it to D, and gate G4 guarantees it cannot score above 59 whatever the other factors say.

## 7. Non-functional requirements

Performance figures are proposed targets to be confirmed on reference devices (todo T12).

| ID | Area | Requirement |
| --- | --- | --- |
| REQ-NFR-01 | Performance | Quick scan (pass 1) of at least 10 files per second per CPU core on a typical Linux desktop with an SSD. Quick scan is not a priority. |
| REQ-NFR-02 | Performance | Full analysis of a 4-minute track in at most 3 s per CPU core on a typical Linux desktop; scans scale with --jobs. |
| REQ-NFR-03 | Resources | Audio is decoded in streamed chunks; peak memory below 150 MB regardless of file length. |
| REQ-NFR-05 | Robustness | Malformed or hostile files never crash the app; each file has a time and memory budget, after which it is reported as Error. |
| REQ-NFR-06 | Security | Parsing and decoding use well-fuzzed libraries, isolated in per-file worker processes; the analysis core is fuzz-tested. |
| REQ-NFR-07 | Privacy | No network access, telemetry or analytics. All processing is local. |
| REQ-NFR-08 | Licensing | All code and dependencies are FOSS with compatible licenses; the license choice is recorded (todo T09). |
| REQ-NFR-09 | Distribution | Phase 1 ships as a source release and a Fedora RPM, with other distribution packages to follow. |
| REQ-NFR-10 | Portability | One shared analysis core, so a file scores identically on every supported platform. |
| REQ-NFR-11 | Transparency | Every score is explainable from the stored measured values and the scoring profile. |
| REQ-NFR-12 | Usability | The UI states that ratings are estimates and explains each verdict in plain language. |
| REQ-NFR-13 | Localisation | Report and UI strings are externalised for translation; CSV column names and CLI options stay fixed in English. |

- **REQ-NFR-14 Scale** A full scan of a library of 20 000 files totalling 120 GB shall complete without manual intervention and with memory use independent of library size.
- **REQ-NFR-15 Incremental rescans** Results are cached per file (path, size, modification time); a rescan analyses only new or changed files, and an interrupted scan resumes where it stopped.
- **REQ-NFR-16 Storage-aware I/O** Parallelism adapts to the storage: many workers on SSDs, few sequential readers on spinning disks and network shares, overridable with --jobs.

## 8. Risks, constraints and reference tools

The main risk is wrong verdicts, not wrong code; it is mitigated by a labelled test corpus and tunable profiles.

### 8.1 Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| False positives: old, lo-fi or band-limited recordings look like transcodes | High | Protection rules, Warn instead of Fail for ambiguous cases, tuning on labelled corpus (T10) |
| False negatives: transcodes from high-quality lossy sources pass | Medium | Combine cutoff, sharpness and hole factors; document the limit |
| Codec tables wrong for Vorbis and Opus | High | Build tables from reference encodes per codec (T11) |
| Malformed files crash or hang the analyser | High | Worker-process isolation, fuzzing, per-file budgets (REQ-NFR-05, 06) |
| Slow full scans on large libraries | Medium | Quick scan, background work, sampling long files |
| License incompatibility between dependencies | Medium | License review before choosing libraries (T09) |
| Users read ratings as absolute truth, or edit weights into a misleading profile | Medium | Explainable scores, provisional marking, plain-language verdicts |
| Missing or wrong tags leave album/title empty | Low | Report empty cells; optional fallback to folder and file name (Q5) |

### 8.2 Constraints

- Fully FOSS; no proprietary SDKs, services or decoders.
- No playback, no network, no file modification.
- MP3 patents have expired, so MP3 decoding is unrestricted.

### 8.3 Reference tools for validation

No single FOSS tool covers objectives O1–O3; these are used to cross-check results.

| Tool | Covers | License |
| --- | --- | --- |
| [mpck (Checkmate)](https://github.com/Sjord/checkmate) | MP3 frame validity, truncation (F2.01–F2.03) | GPL |
| MP3val | MP3 integrity (check mode only) | GPL |
| mp3guessenc | LAME tag, encoder detection (F1.05–F1.07) | LGPL |
| MediaInfo | Stream metadata (O1) | BSD-2 |
| ogginfo, flac -t, metaflac | Ogg structure, FLAC MD5 and headers | FOSS (Xiph) |
| libebur128, rsgain | Loudness, true peak, dynamic range | MIT / BSD |
| FFmpeg (astats, ebur128) | Clipping, peaks, decode errors | LGPL / GPL |
| Spek, SoX | Spectrogram (F3.08) | GPL |
| [fakeflac-go](https://pkg.go.dev/github.com/drichline/fakeflac-go) | Spectral cutoff scoring | GPL-3.0 |
| [FLAC Detective](https://pypi.org/project/flac-detective/0.9.1/) | Rule-based transcode scoring design | Open source |

## 9. Requirements-phase todo tracker

All drafting and approval tasks (T01–T09, T13–T16) are done and the baseline is signed off; T10–T12 remain and run alongside implementation. Update the Status column as items move.

| ID | Task | Owner | Depends on | Status |
| --- | --- | --- | --- | --- |
| T01 | Consolidate objectives and factor catalogue (sections 2, 4.3) | Claude |  | Done |
| T02 | Define phases, platforms and formats (section 3) | Claude |  | Done |
| T03 | Draft scoring model, default weights, gates and rating bands (section 5) | Claude | T01 | Done |
| T04 | Draft HTML and CSV report requirements (section 6) | Claude | T03 | Done |
| T14 | Rescope phase 1 to a CLI with HTML and CSV output and editable weights (sections 3, 4.4, 5.6) | Claude |  | Done |
| T05 | Review and approve factor catalogue and priorities | Product owner | T01 | Done |
| T06 | Approve default weights, gates and rating bands | Product owner | T03 | Done |
| T07 | Approve CSV columns and HTML report contents | Product owner | T04 | Done |
| T08 | Decide phase-1 platforms and phase order (Q9, Q10) | Product owner |  | Done |
| T09 | Choose the project license and approve dependency licensing (Q1) | Product owner |  | Done |
| T15 | Choose the final project name and check it is free (Q12) | Product owner |  | Done |
| T16 | Approve CLI commands, options and profile file format (4.4, 5.6, Q11) | Product owner | T14 | Done |
| T10 | Assemble labelled test corpus: genuine and transcoded MP3 and Vorbis files | Team | T05 | Not started |
| T11 | Build MP3 and Vorbis cutoff-to-quality tables from reference encodes | Team | T10 | Not started |
| T12 | Confirm performance targets on reference Linux machines | Team | T08 | Not started |
| T13 | Sign off requirements baseline v1.0 (gate to architecture) | Product owner | T05–T09, T15, T16 | Done |

T10–T12 can run in parallel with architecture work, since they tune values rather than structure.

## 10. Open questions

| ID | Question | Proposed default |
| --- | --- | --- |
| Q12 | Final project name. "audioqc" is already an open-source audio QC command-line tool from AMIA ([amiaopensource/audioqc](https://www.github.com/amiaopensource/audioqc), BSD-3, invoked as `audioqc`), and Eyevinn publishes an open-source "Audio QC" loudness tool ([wiki](https://github-wiki-see.page/m/EyevinnOSC/community/wiki/Service:-Audio-QC)). | Resolved: qctrack. Reserve the name on GitHub, PyPI, Fedora, Debian, Flathub and AUR before announcing |
| Q11 | Profile file format? | Resolved: TOML: readable, supports comments, easy to hand-edit |
| Q9 | Should phase 1 also ship Windows and macOS builds? | Resolved: Linux officially; other builds only if the core compiles without platform-specific work |
| Q10 | Phase 2 (FLAC, Opus) before phase 3 (GUI)? | Resolved: Yes: codec support reuses the CLI and test corpus |
| Q1 | Which license? | Resolved: GPLv3 |
| Q6 | Besides editing the profile file, should the CLI offer a command to set single weights (e.g. `profile set F3.01=30`)? | File editing only in phase 1 |
| Q5 | When album or title tags are missing, fall back to folder and file names? | No by default; optional flag, noted in the flags column |
| Q7 | Besides HTML and CSV, write JSON reports? | Resolved: report.json holds measurements and scores and is the input for rescore (REQ-CLI-06) |
| Q8 | Rating display: letter grades A–F, stars, or both? | Letter grade with label |
| Q4 | Linux UI: GUI and CLI, or CLI first? | Resolved: CLI first (phase 1) |
