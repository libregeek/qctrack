# Labelled test corpus

Nov 21, 2026 · Requirements for the corpus behind T10, T11 and T18 (`requirements.md` section 9), review question R5 (`spec-review-false-positives.md` section 10) and Q-A10 (`accuracy-findings.md`)

**Status:** deferred. The origin of most files at hand is unknown, so they cannot be labelled reliably. The owner will collect samples of known origin later. Until then, the synthetic cases in `tests/fp_cases.py` stand in (see "Synthetic stand-ins" below).

## 1. Purpose

The thresholds in the default profile and the protection rules are starting values tuned on synthetic signals and a handful of real files. Synthetic signals show *whether* a rule can misfire, not how often it does on real music. The corpus is how the thresholds get settled:

| Use | Task | What it needs from the corpus |
| --- | --- | --- |
| Tune the cutoff, fade and content-above-the-wall thresholds that set G4 | T10 | Genuine encodes and transcodes of known origin, including dark and band-limited sources |
| Build MP3 and Vorbis cutoff-to-quality tables | T11 | Encode ladders (several bitrates and quality levels) from the same lossless sources |
| Settle the true-peak overshoot curve for lossy files (F2.08) | T10, Q-A10 | Source/encode pairs: the lossless source and its encodes |
| Check the false-positive cases of the review | T18, R5 | The sample types in section 4 marked R5 |
| Check `compare` and `--duplicates` | T18 | Several copies of the same recording |

## 2. Copyright and location

The corpus holds copyrighted recordings. These rules also appear in `architecture.md` section 8.

- It lives outside the repository: `QCTRACK_SAMPLES`, default `~/Lab/samples`. A subfolder such as `~/Lab/samples/corpus/` keeps it apart from the loose samples used today.
- Audio never enters git, the source tarball or the RPM. `.gitignore` covers the audio formats and `labels.csv`.
- Findings, tests and golden files may name corpus files and quote measured values, but never contain their audio.
- Tests that use the corpus are skipped when it is missing, so the suite passes on any machine.
- Claude reads the corpus folder only with the owner's permission, since it lies outside the project.

## 3. Labels

Every file is described by one row of `labels.csv`, kept in the corpus folder. A file without a label is still useful for crash and speed testing, but it cannot settle a threshold.

| Column | Required | Values | Example |
| --- | --- | --- | --- |
| `file` | Yes | Path relative to the corpus folder | `ladders/cd01-t03/lame-v2.mp3` |
| `origin` | Yes | `genuine`, `transcode` or `unknown` | `genuine` |
| `chain` | Yes, for `genuine` and `transcode` | Every step from the source to this file, joined by `→` | `CD rip (cdparanoia) → LAME 3.100 -V2` |
| `source_id` | For pairs and ladders | One identifier shared by every file made from the same source | `cd01-t03` |
| `case` | No | One or more sample types from section 4, separated by spaces | `dark mono` |
| `expect` | No | The action a correct analyser gives (requirements 5.9), if known | `Keep` |
| `notes` | No | Anything else | `remastered 2004` |

**Rules for `origin`:**

- `genuine`: encoded once from a lossless source. Known only when you made the encode yourself from a source you hold, or the seller documents it (for example a store purchase).
- `transcode`: encoded from a lossy source. Known only when you made it yourself or the chain is documented.
- `unknown`: anything else, including files ripped or downloaded by other people. **Never guess.** A wrong label is worse than none, because it teaches the tuning the wrong answer.

Files of unknown origin are still worth keeping for the sample types where origin does not matter (damage, tags, cover art, encoder identity) or where the label can be made true by construction (section 5).

## 4. Sample types

Priority: **1** is needed before T10/T11 can start, **2** is needed to close a review finding, **3** is useful.

| # | Sample type | Why (finding) | How to obtain | Origin known? | Aim | Priority |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Lossless sources: CD rips to WAV or FLAC | The base for every pair and ladder | Rip your own CDs (`cdparanoia`, `flac`) | Yes | 10–20 tracks from 3–5 CDs, varied genres and eras | 1 |
| 2 | MP3 encode ladders from type 1 | T11 tables; C4 band edges; N3 | `lame` CBR 128, 160, 192, 224, 256, 320 and `-V0`, `-V2`, `-V5`; the same with FFmpeg's `libmp3lame` | Yes, by construction | Every source in type 1 | 1 |
| 3 | Vorbis encode ladders from type 1 | T11 Vorbis tables (needed before Vorbis O3 is scored); M6 | `oggenc -q` 1, 3, 5, 6, 8, or FFmpeg's `libvorbis` | Yes, by construction | Every source in type 1 | 1 |
| 4 | Transcodes with a known chain | G4 must still catch them | From type 2/3 files: MP3 128 → 320, V5 → 320, Vorbis q2 → MP3 320, AAC 128 → MP3 256 | Yes, by construction | 2–3 per chain | 1 |
| 5 | Old analog-sourced or dark recordings | N1, C2: natural roll-off must not look like a transcode | Real: CD rips of pre-1970s or analog-era releases, film soundtracks, solo voice or instruments, then encoded as type 2 | Yes, if you rip them | 5–10 tracks | 2 (R5) |
| 6 | Mono masters and mono reissues | H6: mono is a flag, not a defect | Real: CD rips of mono releases, encoded both as mono and as stereo | Yes, if you rip them | 3–5 | 2 (R5) |
| 7 | Non-LAME encoders: iTunes, FhG, others | H4, C4: lowpass policies other than LAME's | Real: store purchases, or rips made with iTunes or Windows Media Player on another machine | Yes for purchases; otherwise `unknown` | 3–5 per encoder | 2 (R5) |
| 8 | Other sample rates: 32, 22.05, 48 and 96 kHz | C5, M8 | From type 1: `lame --resample 32`, FFmpeg `-ar` | Yes, by construction | 1–2 per rate | 2 (R5) |
| 9 | mp3gain-adjusted MP3 | H2 (when F2.04 is built): the music CRC breaks but the sound does not change | Run `mp3gain -r` on copies of type 2 files | Yes, by construction | 3 | 2 (R5) |
| 10 | Large embedded cover art (1–5 MB) | C7: bitrate must not count it | Embed an image in copies of type 2 and 3 files (Mutagen, or FFmpeg with `-c copy`) | Yes, by construction | 2 per format | 2 (R5) |
| 11 | Lossless splits | H8: a file cut at its start is not damaged | Split type 2 files with FFmpeg `-c copy -ss`, or with mp3DirectCut or mp3splt on another machine | Yes, by construction | 3 | 3 |
| 12 | Real damage | G2 must still catch it | Interrupted downloads, joined files, files from failing disks; keep any you find | Often `unknown`, which is fine here | Whatever turns up | 3 |
| 13 | Loud modern masters, including clipped ones | O4 mastering factors; N4 (deferred to FLAC) | Real: CD rips of loud 2000s–2010s releases | Yes, if you rip them | 3–5 | 3 |
| 14 | Short tracks (under 30 s) | `short_track` flag; dynamics N/A | Real: interludes and intros | Any | 3 | 3 |
| 15 | Copies of the same recording | `compare` and `--duplicates` (T18) | Real: the same song from two releases (original and remaster), plus types 2 and 4 of one source | Yes for your own encodes | 3–5 groups | 3 |
| 16 | Unknown-origin files from the existing library | Crash, speed and spot checks | Whatever is at hand | `unknown` | Any | 3 |

Speech and podcast files are not needed: no speech profile is planned (R4).

Tools for making the samples, all in Fedora: `cdparanoia` 10.2, `flac` 1.5.0, `lame` 4.0, `vorbis-tools` 1.4.3 (`oggenc`), `ffmpeg-free` 9.0.2, `mp3gain` 1.6.2, `sox` 14.4.2, `python3-mutagen` 1.48.1. Not in Fedora: iTunes, FhG encoders, mp3splt, mp3DirectCut.

## 5. Labels by construction

Most of the corpus can be labelled with certainty even though the original library cannot. Start from a lossless rip you made yourself (type 1) and derive every other file from it with a known command. The label then follows from the command, not from memory:

```
ladders/cd01-t03/source.flac        genuine    CD rip (cdparanoia)
ladders/cd01-t03/lame-320.mp3       genuine    CD rip → LAME 4.0 -b 320
ladders/cd01-t03/lame-128.mp3       genuine    CD rip → LAME 4.0 -b 128
ladders/cd01-t03/t-128-320.mp3      transcode  CD rip → LAME 4.0 -b 128 → LAME 4.0 -b 320
```

A script can write these files and their `labels.csv` rows together, so the label can never drift from the file. One or two lossless rips are enough to start.

## 6. Suggested layout

```
~/Lab/samples/corpus/
  labels.csv
  sources/        type 1 (lossless rips)
  ladders/        types 2, 3, 4, 8, 9, 10, 11, one subfolder per source_id
  real/           types 5, 6, 7, 12, 13, 14, 15 as found
  unknown/        type 16
```

## 7. How the corpus will be used

- A checker tool (to be written once the corpus exists) scans the corpus, compares each file's best-copy verdict and action with its label and `expect` column, and lists every disagreement. Files labelled `unknown` are reported but never counted as right or wrong.
- Tuning changes a threshold only when the disagreements on labelled files go down and none of the synthetic cases in `tests/fp_cases.py` regress.
- Results are written up the way `accuracy-findings.md` and `spec-review-false-positives.md` section 11.4 are: measured values and labels, no audio.

## 8. Synthetic stand-ins

Until the corpus exists, `tests/fp_cases.py` (run by `tests/test_false_positives.py`) builds these cases with FFmpeg from synthetic noise. They cover the mechanisms of the sample types above, not their real-world frequency.

| Sample type | Stand-in cases |
| --- | --- |
| 2 Encode ladders | `genuine_320.mp3`, `genuine_128.mp3`, `genuine_112.mp3`, `genuine_v0_ffmpeg.mp3` |
| 3 Vorbis | `vorbis_q6.ogg`, `vorbis_q1.ogg` |
| 4 Transcodes | `transcode_128_to_320.mp3`, `transcode_vorbis_q2_to_320.mp3`, `dark_film_from_video_site_256.mp3` |
| 5 Dark sources | `dark_9000_320.mp3`, `dark_5000_320.mp3` |
| 6 Mono | `mono_160.mp3`, `mono_in_stereo_320.mp3` |
| 8 Sample rates | `genuine_32khz_160.mp3` |
| 11 Splits | `split_with_ffmpeg_320.mp3`, `split_stale_xing_320.mp3`, `vorbis_from_mid_stream.ogg` |
| 12 Damage | `half_320.mp3`, `half_vorbis.ogg`, `cut_short_frame_320.mp3`, `vorbis_no_eos.ogg`, `zero_padding_320.mp3` |
| Other formats | `aac_in_mp4_named.mp3`, `wav_named.mp3` |

No stand-in exists for types 7 (non-LAME encoders), 9 (mp3gain), 10 (cover art in the corpus test; unit tests cover it), 13, 14 or 15, or for real music of any type.
