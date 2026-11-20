# Specification review: false positives and gaps

Nov 20, 2026 · Review of `requirements.md`, `quality-evaluation-matrix.md`, `architecture.md` and `accuracy-findings.md` from a media-format perspective

## Scope and approach

This review asks one question of every factor, threshold, weight and gate: **can a genuine, well-made track be rated poor because of it?** Phase 1 formats (MP3, Ogg Vorbis) are covered in depth, and phase 2 (FLAC, Opus) gets early warnings in section 7. Findings are about the specification only. Section 8 lists what to check in the code next.

Severity reflects the worst effect on a genuine track:

| Severity | Effect on a good track |
| --- | --- |
| Critical | Can be capped at D or F by a gate, or rated D from content alone |
| High | Typically loses a grade, or gets a misleading Fail verdict |
| Medium | Loses a few points, or gets a misleading Warn |
| Low | Inconsistency or wording; no direct score effect |

Assumptions in the worked examples are stated inline. Exact LAME lowpass values below come from LAME's `optimum_bandwidth()` table in `libmp3lame/lame.c` and should be confirmed against the LAME version used in T11.

## Summary

| ID | Severity | Area | Problem in one line |
| --- | --- | --- | --- |
| C1 | Critical | Scoring model | The spectral cutoff is counted in up to five places (F3.01, F3.02, F3.03, F3.06, G4); natural band-limiting alone gives D |
| C2 | Critical | O3 | Protection rules are referenced but never defined; nothing separates codec cutoffs from natural roll-off |
| C3 | Critical | F3.04 | Direction of scoring undefined; steep walls are normal in every genuine lossy file |
| C4 | Critical | F3.01/F3.02/G4 | Narrow, LAME-only, single-clip bands plus a known measurement bias can trigger G4 on genuine files |
| C5 | Critical | F3.01/F3.02 | Absolute cutoff thresholds ignore sample rate; 32 kHz and lower files always fail and get G4 |
| C6 | Critical | F2.02/G2 | Partial last MP3 frame or missing Ogg EOS flag is "truncation" and caps at F |
| C7 | Critical | F1.01 | Average bitrate can include cover art and header packets, inflating the claimed band and causing G4 |
| C8 | Critical | F1.10 | AAC (ADTS), MP2 or MP4 named `.mp3` may be parsed as MP3 and fail as desync (G2) instead of being Unsupported |
| H1 | High | F2.07 | Lossy decoder overshoot is counted as clipping |
| H2 | High | F2.04 | LAME music-CRC mismatch is "not a gate" but still scores Fail at weight 10 |
| H3 | High | F2.03 | Percentage-of-file junk threshold fails padding, duplicate tags and misplaced cover art |
| H4 | High | F1.05/F1.06 | Missing or non-LAME encoder information gives Warn instead of N/A |
| H5 | High | F3.06 | "Upsampling" Fail for any genuine encode whose lowpass is near 16 kHz |
| H6 | High | F1.04/F2.12 | Mono and mono-in-stereo masters are penalised as defects |
| H7 | High | F2.10 | Loudness range Fail is a mastering-taste judgement and breaks on short tracks |
| H8 | High | F2.06 | Start-of-file reservoir errors in cut files; truncation counted twice |
| M1 | Medium | F2.08 | True-peak overshoot on lossy files (known, decided under Q-A10) |
| M2 | Medium | F1.08 | No tolerance; behaviour without a Xing header undefined |
| M3 | Medium | F2.14 | Ogg Skeleton, non-zero start granule and legal end trimming may be flagged |
| M4 | Medium | F1.01 | ffmpeg `-q:a` VBR files have no preset code, so they are judged on average bitrate |
| M5 | Medium | F3.01 | Averaged spectrum, channel mix-down and no "insufficient evidence" outcome |
| M6 | Medium | Vorbis | No Vorbis tables yet; average instead of nominal bitrate; the fallback is unknown |
| M7 | Medium | F2.13 | Internal silence > 0.5 s Warns on classical, live and spoken content |
| M8 | Medium | F1.03 | Rates above 48 kHz have no verdict; no speech/podcast profile |
| L1–L6 | Low | Documents | Contradictions between requirements and matrix |

## 1. Systemic issues in the scoring model

### C1. The spectral cutoff is counted up to five times

**Spec:** requirements 5.3 and 5.4; matrix F3.01–F3.06.

In phase 1, O3 for MP3 contains F3.02 (30), F3.01 (25), F3.03 (10), F3.04 (10) and F3.06 (10). F3.05 and F3.07 are not yet evaluated. Four of these five factors are functions of the same measured cutoff, about 88% of O3's weight, and G4 reads the same value again. O3 is 45% of the final score. A track whose content naturally stops early therefore fails four times and is then capped.

**Worked example.** A genuine LAME 3.100 320 kbps CBR encode of a recording whose content rolls off gently to about 15.5 kHz (a 1960s analog master or an old film soundtrack). Assume clean integrity and stereo.

| Factor | Measured | Score |
| --- | --- | --- |
| F3.01 cutoff | 15.5 kHz, below the 16.5 kHz Fail point | 0 |
| F3.02 claimed vs effective | High vs Very low (3 bands) | 0, triggers G4 |
| F3.03 declared vs measured | 20.5 kHz declared, Δ 5 kHz | 0 |
| F3.04 sharpness | Gentle (assume scored as good) | 100 |
| F3.06 upsampling | Nothing above 16 kHz | 0 |
| **O3** | (10 × 100) / 85 | **11.8** |

Final score: 0.25 × 100 + 0.30 × 100 + 0.45 × 11.8 = **60.3 (C)**, then G4 caps it at **59 (D)**. The file is a faithful encode of its source; the D comes from the content.

**A genuine 128 kbps file.** LAME 128 kbps CBR, measured at 16.15 kHz (the `Track9.mp3` case in Q-A5). F3.01 = 0, F3.02 = Warn 50, F3.03 = Pass, F3.06 borderline. O3 = 41–53, which the old F3.09 thresholds call "likely transcode" or "suspicious". The authenticity verdict on a genuine file is wrong, even though the final grade (C) is defensible.

**Recommendations**

1. Make O3 measure *consistency* only. F3.01 becomes Info in O3 (its absolute value is a bandwidth-quality fact, already reflected in O1 through bitrate), or is scored relative to the claimed band instead of on an absolute scale.
2. Treat F3.03, F3.04 and F3.06 as *evidence feeding F3.02*, not as independently weighted factors. One combined "transcode likelihood" verdict, with its reasons listed, is easier to explain (REQ-NFR-11) and avoids compounding.
3. Apply G4 only when the combined evidence is strong (see C2). With compounding removed, the gate becomes the only hard penalty.

### C2. Protection rules are referenced but never specified

**Spec:** requirements 8.1 ("Protection rules"); matrix F3.09 ("protection rules for natural low-bandwidth sources").

No document defines them. Without them, *any* track lacking high frequencies, for whatever reason, is treated as a transcode. The physical difference between the two cases is well known and measurable:

| Property | Codec lowpass (genuine or transcode) | Natural band-limiting |
| --- | --- | --- |
| Slope at the edge | Steep: tens of dB within a few hundred Hz | Gradual: a few dB per kHz |
| Position over time | Fixed frequency in every frame | Moves with the music |
| Above the edge | Digital silence or near-floor in every frame | Hiss, noise or occasional content |
| Max-hold spectrum | Still shows the wall | Wall disappears or moves up |

**Recommended protection rules (draft for T10 tuning)**

| Rule | Condition | Effect |
| --- | --- | --- |
| P1 Natural roll-off | F3.04 slope below the "natural" threshold, or the max-hold spectrum shows content or noise above the averaged edge | F3.02 at most Warn; no G4; verdict text "band-limited source", not "transcode" |
| P2 Encoder lowpass match | Measured edge within tolerance of the file's own declared lowpass (F1.07) | F3.02 Pass, whatever the generic table says |
| P3 Sample rate | Sample rate below 44.1 kHz | Judge the cutoff relative to Nyquist (see C5) |
| P4 Unknown table | Encoder is not LAME, or the codec has no tuned table (Vorbis before T11) | F3.02 at most Warn; no G4 |
| P5 Low evidence | Too little high-frequency energy even below the edge, or too few loud frames, or duration under about 30 s | F3.01 "insufficient evidence", F3.02 N/A; coverage drops so G5 marks it Provisional |
| P6 Wording | Any F3.02 Fail | Say "content consistent with a lower-quality or band-limited source", never "fake", because a label master made from a lossy file is indistinguishable from a user transcode (REQ-NFR-12) |

G4 should then require F3.02 Fail **and** a steep wall **and** none of P1–P5.

### C3. F3.04 cutoff sharpness has no defined direction for lossy formats

**Spec:** requirements 5.3 gives weight 10, "Numeric"; matrix F3.04 gives Info thresholds only (> 30 dB/kHz "lossy origin likely", < 10 dB/kHz "natural").

Every genuine MP3 and Vorbis file has a steep wall at its own encoder lowpass. "Lossy origin likely" is therefore true of every input in phase 1 and says nothing about transcoding. If the code scores "sharp" as bad, every genuine lossy file loses up to 10 O3 points. If it scores "sharp" as good, a transcode looks more genuine.

**Recommendation:** for lossy files, sharpness is meaningful only for a wall found *below* the file's own lowpass. Use it as evidence for P1 in C2, not as a scored factor. Keep the matrix wording for FLAC (phase 2), where a steep wall genuinely suggests a lossy source.

### C4. MP3 cutoff bands: narrow, single-encoder, single-clip, and biased measurement

**Spec:** matrix "MP3 cutoff bands for F3.02"; architecture Q-A5.

LAME's CBR lowpass table against the band edges:

| LAME CBR | Lowpass | Claimed band | Effective band | Margin to the band edge |
| --- | --- | --- | --- | --- |
| 128 kbps | 17.0 kHz | Low | Low (16.5–17.25) | 500 Hz above the lower edge |
| 160 kbps | 17.5 kHz | Medium | Medium (≥ 17.25) | 250 Hz |
| 192 kbps | 18.6 kHz | Medium | Medium | 1.35 kHz |
| 224 kbps | 19.4 kHz | Medium | Medium (just below 19.5) | 100 Hz below High |
| 256 kbps | 19.7 kHz | High | High (≥ 19.5) | 200 Hz |
| 320 kbps | 20.5 kHz | High | High | 1.0 kHz |

Problems:

- **Measurement bias.** Q-A5 records that the current edge rule reads a genuine 128 kbps file at 16.15 kHz, about 850 Hz below LAME's 17.0 kHz lowpass. With that bias, genuine 160 and 256 kbps files fall one band and get a Warn as a matter of routine. The band edges and the measurement rule must be calibrated together, with the same rule used in T11 as in production.
- **The Low band is 750 Hz wide.** "Two bands lower" (Fail and G4) can mean a difference of only about 2.3 kHz. A gate should not depend on a 750 Hz band.
- **LAME only.** FhG, iTunes, Helix, Xing and old LAME versions (3.90–3.97 presets) use different lowpass policies. Old Xing encoders, for example, are known to cut around 16 kHz at most bitrates. A genuine 256 kbps file from such an encoder gets G4.
- **User lowpass.** LAME users sometimes set `--lowpass`. The LAME tag records it (F1.07), but F3.02 does not use it.

**Recommendations**

1. When F1.07 is present, judge the measured edge against *the file's own declared lowpass* (P2). Use the generic band table only when nothing is declared.
2. Gate on a **Hz deficit** (for example, measured edge ≥ 3 kHz below the expected lowpass for the claimed bitrate) rather than a band count.
3. Apply P4: no G4 for non-LAME or unknown encoders until T11 builds tables for them.

### C5. Cutoff thresholds ignore the sample rate

**Spec:** matrix F3.01 MP3 row and F3.02 bands; F1.03.

At 32 kHz the Nyquist limit is 16 kHz, so the measured cutoff is always below 16.5 kHz: F3.01 = 0, F3.02 = Fail, **G4**. At 22.05 or 24 kHz (MPEG-2 and 2.5) the same happens. The file has already been penalised once by F1.03 for its sample rate. Encoder lowpass at these rates is also bounded by Nyquist, so the absolute table does not apply.

**Recommendation:** for sample rates below 44.1 kHz, express the cutoff as a fraction of Nyquist, or make F3.01 and F3.02 relative to the encoder's expected lowpass at that rate (P3). F1.03 alone carries the sample-rate penalty.

## 2. Authenticity (O3), other findings

### H5. F3.06 "upsampling" duplicates the cutoff for lossy files

**Spec:** matrix F3.06 ("empty spectrum above 11 / 16 kHz with a near-zero noise floor"; Pass/Fail only).

A genuine LAME V5–V7 or 96–112 kbps encode has an encoder lowpass at or below about 16 kHz, so the spectrum above it is empty with a near-zero floor. That is exactly the F3.06 signature. F3.06 Fail (score 0, weight 10) is then a third penalty for the same cutoff. True upsampling detection in a lossy file mostly cannot be separated from the encoder lowpass.

**Recommendation:** for lossy formats, make F3.06 Info, or report it only when the wall lies exactly at a standard half-rate (11.025, 12, 16, 22.05 kHz) *and* well below the encoder's own lowpass. Keep the scored form for FLAC in phase 2.

### M5. Spectrum method can mistake missing content for a cutoff

**Spec:** matrix F3.01 method ("average magnitude over loud frames; scan down from Nyquist").

- **Averaging hides the encoder wall.** High-frequency content such as cymbals appears only in some frames, so an average under-represents it and pulls the edge down. A codec lowpass is absolute: no frame has content above it. A max-hold or 95th-percentile spectrum finds the encoder's wall reliably and separates it from content that is merely sparse.
- **Channel mix-down.** If L and R are summed before the FFT, out-of-phase high-frequency content cancels. Use per-channel power, summed in the power domain, or take the maximum of the channels.
- **No "insufficient evidence" outcome.** Solo bass, cello, voice, lullabies and quiet classical have no high-frequency content to measure. The method then reports a low cutoff instead of "unknown". See P5 in C2.
- **Short tracks.** Interludes and intros of a few seconds give too few loud frames for a stable average.

### F3.05 (Later, weight 10): a warning before it is built

Genuine MP3 at 128–192 kbps *always* shows holes and flicker above about 16 kHz: this is LAME's sfb21 behaviour at those bitrates. Holes are expected in the codec under test and are not evidence of a second encode. For lossy files, F3.05 needs a per-bitrate expectation, or it should stay Info.

## 3. Technical integrity (O2)

### C6. G2 truncation gate is triggered by harmless endings

**Spec:** matrix F2.02 (MP3: "last frame complete"; Ogg: "final page … carries the end-of-stream flag"); requirements G2 (cap 39, F).

- **MP3:** an incomplete final frame is very common in good files: rippers, older encoders, tag editors and lossless splitters leave one. It loses at most about 26 ms, usually of encoder padding. `mpck` and `mp3val` report it routinely, and users know to ignore it.
- **Ogg:** some encoders and stream recorders never set the EOS flag on the final page, even when the page is complete and the granule position is consistent. `ogginfo` reports only a warning.

Either way, a complete, good-sounding track is graded **F**.

**Recommendation:** measure truncation in milliseconds of audio lost. Make "less than one frame or page, no declared length shortfall" a Warn with no gate, and missing EOS with a complete final page an Info. Apply G2 only when the loss is material, for example when the declared length (Xing frames or granule) exceeds what is present by more than about 1 s or 1%, or the final frame is cut inside real audio.

### C8 (integrity effect). Misdetected formats reach the MP3 scanner

See section 5. An AAC (ADTS) file named `.mp3` has a 0xFFF sync word and can pass a naive MPEG sync check. It then shows persistent desync, F2.01 Fail and **G2**, when it should be Unsupported.

### H1. F2.07 counts lossy overshoot as clipping

**Spec:** matrix F2.07 (runs of three or more samples at |x| ≥ 0.999 in a float decode; Fail > 0.1% of samples).

A modern master limited to −0.1 or −0.3 dBFS comes out of an MP3 or Vorbis decoder with frequent overshoots above 1.0 (accuracy-findings: up to +0.7 dB at 128 kbps, +1.5 dB at Vorbis q3). These form runs at or above 0.999 that are not flat-topped clipping. A source that really clipped, on the other hand, loses its flat tops in lossy coding, so run-counting cannot see it reliably anyway. With about 21 million samples in a 4-minute stereo track, 0.001% (the Warn threshold) is only about 210 samples. F2.07 also overlaps F2.08.

**Recommendations**

- For lossy formats, report samples above 1.0 as "decoder overshoot, may clip on fixed-point playback" (Info or Warn), not as clipping.
- Detect mastered clipping from the sample-value histogram: a spike at the maximum level compared with its neighbours. This survives lossy coding better than run length.
- Never let F2.07 and F2.08 both penalise the same peaks.

### H2. F2.04 MP3 music-CRC mismatch: the text and the weights contradict each other

**Spec:** requirements 5.4 says a mismatch is "not a gate … reported as 'modified since encoding'". The 5.3 table and matrix F2.04 still score it Categorical, Fail = 0, weight 10.

mp3gain, AACGain-style tools and lossless trimming are widespread, so a large share of good libraries would lose 10 O2 points.

**Recommendation:** for MP3, a mismatch is Info (weight 0) with the flag `modified_since_encoding`. Where an APEv2 `MP3GAIN_UNDO` or `MP3GAIN_MINMAX` tag is present, say "gain-adjusted with mp3gain" instead. Keep the scored Fail and G3 for FLAC MD5.

### H3. F2.03 junk threshold is a percentage of the file

**Spec:** matrix F2.03 (Fail > 0.1%).

0.1% of a 5 MB file is 5 KB. Common harmless cases that exceed it:

- zero padding between an ID3v2 tag and the first frame, or a tag whose declared size is shorter than its padding;
- duplicate prepended ID3v2 tags left by buggy taggers, often carrying a cover image;
- appended ID3v2.4 tags with a footer, Lyrics3v1, MusicMatch tags, and APEv1;
- an ID3v2 tag in the middle of the stream in joined files or stream recordings.

None of these affects the audio.

**Recommendation:** classify non-audio bytes as zero padding, recognised but misplaced metadata, or unknown. Only unknown bytes *inside* the audio run (between frames) suggest damage or joining; score those, and report the rest as Info. Use an absolute threshold (bytes or frames' worth) rather than a percentage.

### H6. Mono content is penalised twice

**Spec:** matrix F1.04 (Warn "mono music"); F2.12 (Warn "corr ≈ 1 (fake stereo)").

Recordings made before the late 1960s, much film and broadcast material, and many reissues are mono masters. In a stereo file they get F2.12 Warn. As a native mono file they get F1.04 Warn, and F2.12's behaviour on one channel is undefined. Neither is a fidelity defect. Mono in a stereo container only wastes bits, and joint stereo makes that nearly free.

**Recommendations**

- F2.12: correlation ≈ 1 → Info "mono content in a stereo file". Keep Fail for a dead channel (relative RMS, for example more than 40–60 dB below the other channel over the whole track) and for correlation < −0.5 (polarity error).
- F1.04: mono → Info. Keep the intensity-stereo Warn.
- F2.12 is N/A for one-channel files.

### H7. F2.10 dynamic range is a taste judgement

**Spec:** matrix F2.10 (Poor < 3 LU loudness range); requirements 1 puts "mastering taste" out of scope.

Loudness range (LRA) describes macro-dynamics. Electronic, rock, pop and much devotional or film music sit at 2–5 LU *by design*. LRA also needs several 3-second windows. A 20-second interlude, or a track with one steady level, reads near 0 LU and Fails.

**Recommendation:** make F2.10 Info, as F2.09 is. If it stays scored, cap it at Warn and set it to N/A below a minimum duration (for example 30 s of gated audio).

### H8. F2.06 decode errors: false starts and double counting

**Spec:** architecture section 5 ("the scanner's truncated tail counts as one error (overlaps F2.02 by design)").

- **Double counting.** A truncated tail costs F2.02 (with gate G2) and also F2.06. Count it once.
- **Cut files.** Files split losslessly (mp3DirectCut, mp3splt) start with frames whose bit reservoir points before the start of the file. Decoders report this once or twice at the start, and the audible effect is a few milliseconds at most. Confirm which mpg123 messages this produces, and make errors confined to the first two frames Info.
- **Vorbis.** `OV_HOLE` on the first page of a file cut from a live stream (non-zero start granule) is not damage.

### M1. F2.08 true peak (already decided)

Covered in accuracy-findings Q-A10: the decision is to keep the curve until T10. Two points to add for that work. True peak must never be scored as clipping (see H1). Overshoot also grows when a file is quieter at the codec's lowpass, so a curve based on bitrate alone will remain approximate.

### M3. F2.14 Ogg stream layout

**Spec:** matrix F2.14 (Pass: one stream, continuous granules; Fail: granule gaps).

- An **Ogg Skeleton** stream alongside Vorbis is legal and is Xiph's recommendation for `.oga`. It must count as Pass, not "multiplexed".
- A **non-zero first granule position** (a stream recorded from mid-broadcast) is legal, as is a final granule smaller than the decoded total (end trimming). Neither is a gap.
- Chained streams: Warn is reasonable, but it should not also produce F1.08 and F2.02 penalties for the same join.

### M7. F2.13 internal silence (Later)

A gap of more than 0.5 s below −60 dBFS is normal in classical music, live recordings, hidden tracks, spoken word and Indian classical alaap. Make it Info, or require a much longer gap (several seconds) together with abrupt edges (a dropout starts and ends suddenly, while a musical rest fades).

## 4. Metadata and encoding (O1)

### C7. Average bitrate must exclude every non-audio byte

**Spec:** matrix F1.01 ("average = audio bytes × 8 / duration"; Vorbis "average from stream bytes ÷ duration").

The formula is right but loosely worded, and the error it invites leads to a gate:

- **MP3:** an embedded cover image of 1–5 MB in ID3v2 APIC, or in a duplicate tag, makes a genuine 128 kbps file look like 200+ kbps. The claimed band becomes Medium, F3.02 measures it two bands low, and G4 applies.
- **Vorbis:** cover art in `METADATA_BLOCK_PICTURE` sits base64-encoded *inside the comment header packet*, which is part of the Ogg stream. Counting "stream bytes" includes it.
- **Duration:** must exclude encoder delay and padding (from the LAME tag) and must not come from mpg123's padded length (accuracy-findings already notes this).

**Recommendation:** define F1.01 as the sum of audio frame sizes (MP3, excluding the Xing/Info frame) or audio packet sizes (Vorbis, excluding the three header packets), divided by the trimmed duration. Add a test with a large cover image.

### H4. Missing encoder information must be N/A, not Warn

**Spec:** matrix F1.05 (Warn "unknown encoder"); F1.06 (no rule for "not recorded").

- iTunes, FhG and most hardware or store encoders write no LAME tag. Under "Warn: unknown" every such file loses up to 30 O1 points (F1.05 and F1.06 together) for being made with a different tool.
- ffmpeg's `libmp3lame` writes a LAME tag whose encoder string starts `Lavc` or `Lavf`. It is LAME and should be recognised as such.
- LAME 3.97 was the recommended version for years. Its difference from 3.98+ is not worth a Warn on its own.

**Recommendations**

- F1.06 not recorded → N/A, excluded and renormalised (REQ-SCR-04).
- F1.05 unknown → Info. Warn only for encoders with a known quality problem (old Xing, Blade, Shine, FhG "fast" modes, LAME before 3.90).
- F1.06 Pass list: add 256 and 224 CBR and ABR ≥ 224. State what CBR 160–192 is (Warn, matching F1.01).
- Note that F1.01 and F1.06 largely measure the same thing (weight 30 + 20). Consider reducing F1.06 or making it Info when F1.01 already places the file.

### M4. VBR files without a preset code

**Spec:** matrix MP3 bitrate bands ("a file with a V0–V2 preset in its LAME tag is High whatever its average").

ffmpeg (`-q:a 0`) and some front ends set the VBR quality directly, so the LAME tag's preset field is empty. A V0 encode of sparse material (solo piano, old mono recordings) can average 150–190 kbps and lands in Medium or Low. The Xing header's quality indicator (the VBR scale field) and the LAME tag's VBR method still reveal the V-level.

**Recommendation:** derive the V-level from the Xing quality field when the preset code is absent, and use it as the preset would be used.

### M2. F1.08 declared vs counted length

- A CBR file without a Xing/Info header has no declared length. F1.08 must be N/A, not Fail.
- The Xing frame count may or may not include the Xing frame itself, depending on the encoder. Allow ±1 frame for Pass.
- If the score is categorical, any mismatch below 1% is a flat 50. Use the numeric curve so that one frame in 10 000 scores about 100.

### M6. Ogg Vorbis specifics

- **No tables yet (T11).** Until there are Vorbis cutoff and q-level tables, F3.02 for Vorbis must not fall back to the MP3 table (P4). Code check needed.
- **Use the nominal bitrate.** libvorbis writes a nominal bitrate that maps directly to the q-level in quality mode. The average from stream bytes swings with content (and with cover art, C7). Score F1.01 and F1.06 from the nominal value when the file was encoded in quality mode.
- **Lowpass per q-level** is defined in libvorbis' setup tables (`lib/modes/setup_44.h` and the 48 kHz equivalents). Build the T11 table from those values and confirm by measurement, rather than measuring alone.
- **Noise normalisation** at low q fills the band edge with noise, so the edge rule must be tuned separately for Vorbis.

### M8. Sample rates and content types

- F1.03 Pass lists only 44.1 and 48 kHz. A 96 kHz Vorbis file has no defined verdict. Use Pass for 44.1 kHz and above.
- There is no speech or podcast profile. Audiobooks and podcasts at 22–32 kHz mono and 48–64 kbps are correctly encoded and will score D or F. Either ship a `speech` profile or state in the report that the default profile assumes music.

## 5. Format detection and parsing gaps

### C8. Detection must reject look-alikes before the MP3 scanner sees them

**Spec:** matrix F1.10 (MP3: "MPEG frame sync after any ID3v2 tag"); REQ-FMT-02 names only Opus, FLAC and Speex.

| Look-alike | Why it fools a naive check | Required outcome |
| --- | --- | --- |
| AAC in ADTS (`.aac` renamed `.mp3`) | Same 0xFFF sync; layer bits are 00 | Unsupported |
| MP4/M4A named `.mp3` (common from video downloaders) | `ftyp` box; no sync at offset 0, but random 0xFFE bytes later | Unsupported |
| MPEG Layer I or II named `.mp3` (DVB and old rips) | Valid MPEG audio frames | Unsupported, or analysed as MP2 later; never "broken MP3" |
| MP3 inside RIFF/WAVE (format tag 0x55) | RIFF header, then MP3 frames | Unsupported, or unwrap; decide |
| MP3 preceded by junk with no ID3v2 | First sync is not at offset 0 | MP3, found by requiring several consecutive valid frames |
| WMA, APE, WavPack, plain WAV | Other magic numbers | Unsupported |

**Recommendations**

- Require a run of consecutive valid frames (for example four, with consistent version, layer and sample rate) before declaring MP3.
- Define how Unknown is reported: F1.10 says "Fail", but G1 only covers "not supported in this release". Unknown files should be Unsupported or Error, never a scored track with a 0 in F1.10.
- Never send an Unsupported file to the integrity scanners.

### Parsing details that affect F2.01

- The MP3 header consistency check should cover version, layer and sample rate only. Bitrate (VBR), padding and mode extension change frame to frame by design, and some encoders switch between stereo and joint stereo per frame.
- Free-format MP3 (bitrate index 0) is legal and must be handled, or reported as Unsupported, not as desync.
- ID3v2 features: extended header, footer (v2.4), unsynchronisation, and tags whose declared size is wrong. A wrong tag size is a metadata problem, not stream damage.

## 6. Inconsistencies between documents

| ID | Where | Inconsistency | Suggested resolution |
| --- | --- | --- | --- |
| L1 | F3.04 | Matrix: Info thresholds. Requirements 5.3: weight 10, Numeric. | See C3 |
| L2 | F2.09 | Matrix: Pass/Warn. Requirements: Info, weight 0. | Matrix to Info only, so reports don't show an alarming Warn |
| L3 | F2.04 | Requirements 5.4: MP3 mismatch "not a gate, modified". 5.3 and matrix: Fail = 0, weight 10. | See H2 |
| L4 | F3.01 | General row: Warn 17–19.5, Poor ≤ 16.5 (16.5–17 undefined). MP3 row: Warn 16.5–19.5. | Use one definition |
| L5 | F1.10 | Matrix: Warn "supported later", Fail "unknown or mismatched". G1: Unsupported, not scored. "Mismatched" is undefined (extension use is forbidden by REQ-FMT-04). | See C8 |
| L6 | Coverage | Factors with Later priority (F2.11, F2.13, F3.05, F3.07) carry weights. It is unstated whether they count as "applicable, not evaluated" (lowering coverage for every track) or are excluded. | State that unimplemented factors are excluded from both sums |

## 7. Early warnings for phase 2 (FLAC and Opus)

These are outside phase 1 but follow the same false-positive pattern and are cheaper to fix in the spec now.

- **Genuine CD anti-alias walls.** Many genuine CD masters have a steep wall at about 20–21.5 kHz from the ADC or sample-rate converter. With F3.01 Warn below 21 kHz and F3.04 "sharp = lossy", these look like fake lossless. FLAC thresholds need a "CD anti-alias" exception at about 20 kHz and above.
- **Genuinely band-limited lossless sources.** CDs mastered from 32 kHz broadcast or DAT masters, or from early digital masters, stop at about 15–16 kHz with a steep wall. They are not transcodes from the user's point of view. P6 wording applies.
- **Hi-res from 44.1 or 48 kHz projects.** Most 88.2/96 kHz store releases are mixed at 44.1 or 48 kHz. F3.06 "upsampled" is factually right but should be Info, not a Fail, unless the store claims otherwise.
- **F3.10 fake bit depth.** Use the loudest sections and allow for dither. A 24-bit file whose low bits carry only TPDF dither from a 16-bit source is padded. A 24-bit file with processing after capture is not.
- **Opus.** 96–128 kbps stereo is widely regarded as transparent for most music, so Warn below 128 kbps is strict. Opus fullband stops at 20 kHz by design, so judge F3.01 against the TOC bandwidth only, as the matrix already says.

## 8. Code cross-check list (next step)

| # | Check in the code | Related finding |
| --- | --- | --- |
| 1 | How many factor scores and gates the measured cutoff feeds; weights actually used in `default-profile.toml` | C1 |
| 2 | Whether any protection rule exists; whether G4 requires anything beyond F3.02 Fail | C2 |
| 3 | F3.04 scoring direction and curve | C3 |
| 4 | Band edges vs the edge rule in `core/spectrum.py`; whether F1.07 lowpass is used by F3.02 | C4 |
| 5 | F3.01/F3.02 behaviour at 32, 24 and 22.05 kHz | C5 |
| 6 | What the MP3 scanner calls truncation; Ogg EOS handling; G2 trigger | C6 |
| 7 | Bytes counted in F1.01 for MP3 (tags, Xing frame) and Vorbis (header packets) | C7 |
| 8 | Detection of ADTS, MP4, MP2, RIFF-wrapped MP3, junk before sync; reporting of Unknown | C8 |
| 9 | F2.07 threshold and whether values above 1.0 are counted for lossy formats | H1 |
| 10 | F2.04 MP3 score and weight | H2 |
| 11 | F2.03 classification of padding, duplicate and misplaced tags | H3 |
| 12 | F1.05/F1.06 outcome when there is no LAME tag; `Lavc`/`Lavf` recognition | H4 |
| 13 | F3.06 rule for lossy files | H5 |
| 14 | F1.04 and F2.12 verdicts for mono and mono-in-stereo; F2.12 on one channel | H6 |
| 15 | F2.10 scoring and minimum duration | H7 |
| 16 | F2.06: double count with F2.02; start-of-file reservoir messages | H8 |
| 17 | F1.08 tolerance and the no-Xing case | M2 |
| 18 | Vorbis F3.02 fallback table; F1.01 nominal vs average | M6 |
| 19 | Treatment of Later-priority factors in coverage | L6 |

## 9. Decisions and proposed scoring structure

Nov 20, 2026 · answers from the product owner to R1–R3, and the structure proposed in response. The structure needs approval before `requirements.md` is changed.

### 9.1 Decisions

| ID | Decision | Effect on this review |
| --- | --- | --- |
| R1 | The tool answers both "is this the best copy I could get?" and "how good does this file sound?" | One blended number cannot answer both without false positives (C1). Proposal: report them separately (9.2). |
| R2 | Mastering quality is in scope | Requirements section 1 must drop "mastering taste" from Out of scope. H7 and M1 now call for robust mastering measures, not for removal (9.3). |
| R3 | The risky cases are flagged separately; whether they affect the score is open | Proposal: flag only, with no score effect, unless the flag describes an audible defect (9.4). |

### 9.2 Two headline results

| Result | Question it answers | Built from | Gates |
| --- | --- | --- | --- |
| **Sound score** (0–100, A–F) | How good does this file sound? | O1 Encoding, O2 Integrity, new O4 Mastering, and the measured bandwidth (F3.01) as a fidelity factor | G2 (damage), G3 (FLAC MD5) |
| **Best-copy verdict** | Could a better copy exist? | O3 transcode evidence after the protection rules (C2), plus bitrate and encoder | G4 moves here: it sets the verdict instead of capping the sound score |

Best-copy verdict values:

| Verdict | Meaning | Example |
| --- | --- | --- |
| Best available likely | No evidence of a lower-quality source; the encode is high quality | LAME V0 of a CD; LAME 320 of a 1960s recording with a natural roll-off |
| Low-bitrate encode | Genuine encode, but at a low setting | Genuine LAME 128 kbps |
| Lossy or band-limited source | A steep wall well below the file's own encoder lowpass (F3.02 Fail with protection rules passed) | 320 kbps made from a 128 kbps file, or a label master made from a lossy file |

How this removes the C1 false positive: the natural-roll-off 320 kbps example in C1 gets a Sound score that reflects its limited bandwidth through F3.01 alone (one factor, no gate), and a Best-copy verdict of "Best available likely". A real transcode gets the same lower Sound score, which is correct because it sounds like its source, and "Lossy or band-limited source".

CSV and HTML gain a `best_copy` column next to `rating`. `--fail-below` keeps using the Sound score; a `--fail-on-transcode` option could use the verdict.

### 9.3 O4 Mastering

Move the mastering factors out of O2, so integrity (is the file damaged?) and mastering (was it mastered well?) are scored apart.

| Factor | Measure | Rule for avoiding false positives |
| --- | --- | --- |
| F2.07 Clipping | Histogram spike at the maximum sample level; flat-top runs for lossless | Lossy decoder overshoot above 1.0 is not clipping (H1) |
| F2.08 True peak | dBTP | Codec-aware curve from T10 (M1); never scored together with F2.07 for the same peaks |
| F2.09 Loudness | LUFS | Scored only at the extremes (for example above −7 LUFS); between that and quiet, Info |
| F2.10 Dynamics | Peak-to-loudness ratio (PLR) as the main measure; LRA as Info | N/A below a minimum gated duration (H7); PLR is less genre-dependent than LRA |

Proposed default objective weights: O1 25, O2 25, O3 bandwidth fidelity 30, O4 Mastering 20. These are a starting point for T06-style approval, and users can set O4 to 0 in their profile.

### 9.4 Flags that do not affect the score

| Flag | Condition | Why no score effect |
| --- | --- | --- |
| `mono_content` | Mono file, or stereo with correlation ≈ 1 (H6) | Not audible as a defect |
| `band_limited_source` | Protection rule P1 or P5 applied | Bandwidth already counts once in F3.01 |
| `non_lame_encoder`, `unknown_encoder` | H4 | The encoder identity is not a sound property |
| `gain_adjusted` | mp3gain APE tags or LAME CRC mismatch (H2) | Lossless change |
| `modified_since_encoding` | LAME CRC mismatch without mp3gain tags | Lossless edit likely |
| `low_sample_rate` | Below 44.1 kHz | F1.03 already scores it |
| `short_track` | Below the minimum duration for spectral and dynamics factors | Factors become N/A; coverage shows it |
| `cover_art_large` | Embedded art above a size limit | Informational; F1.01 must exclude it either way (C7) |

The rule: a flag affects the score only when it describes something audible, such as a dead channel, inverted polarity or clipping. Users who want any flag to count can set a weight for it in their profile.

### 9.5 Replacement advice, comparing copies and duplicates

The product owner's goal is to decide whether to prefer another copy over the one they have. Three features were approved on Nov 20, 2026 and written into `requirements.md`:

| Feature | Requirements |
| --- | --- |
| Replacement action per track: Replace (damaged), Replace (lossy source), Replace: low bitrate, Consider another master, Keep (source-limited), Keep | Sections 4.5 (REQ-ADV), 5.9 |
| `qctrack compare FILE FILE...`: ranks copies and names the one to keep, with "Equivalent, keep either" for ties | Sections 4.4 (REQ-CLI-14), 4.5 (REQ-CMP), 5.10 |
| `scan --duplicates`: groups copies across the library by normalised tags and duration, then ranks each group | Sections 4.4 (REQ-CLI-15), 4.5 (REQ-DUP), 5.10 |

The two results (9.2), O4 (9.3) and the flags (9.4) are in requirements sections 5.2–5.4, 5.7, 5.8 and 5.11. Weights and thresholds await approval under T17.

## 10. Open questions for the product owner

| ID | Question | Why it matters |
| --- | --- | --- |
| R1 | Resolved (9.1): both purposes; proposal in 9.2 | |
| R2 | Resolved (9.1): mastering is in scope; mono is a flag only (9.4) | |
| R3 | Resolved in part (9.1): risky cases are flagged; score effect proposed in 9.4 | |
| R6 | Structure approved and written into `requirements.md` (9.5); weights and thresholds tracked as T17 | |
| R4 | Is a `speech` profile wanted in phase 1, or a report note that the default assumes music? | M8 |
| R5 | Can the T10 corpus include old analog-sourced recordings, mono reissues, non-LAME encodes (iTunes, FhG), 32 kHz files, mp3gain-adjusted files and files with large cover art? | These are the false-positive cases above; without them, tuning will not catch them. |

## 11. Code cross-check results

Nov 20, 2026 · `src/qctrack` at commit `a9e7ca4`, default profile 1.0 (the three-objective model; the dual-result model of section 9 is not implemented yet).

**Method.** Each item in section 8 was checked by reading the code, and every finding that depends on behaviour was also run. 37 synthetic files were generated in the scratchpad with FFmpeg 8.1.3 (`libmp3lame`, `libvorbis`, `aac`): 30 s of stereo pink noise with slow level changes, encoded or modified per case, then scanned with `qctrack scan --no-cache --csv-values`. Synthetic signals show *whether* a rule can misfire, not how often it does on real music. The `lame` command-line encoder is not installed, so every MP3 here carries FFmpeg's LAME tag (see N3). The real samples in `~/Lab/samples` were not used, because they lie outside the project folder.

### 11.1 Results per check

| # | Finding | Status | Evidence | Effect on a good track |
| --- | --- | --- | --- | --- |
| 1 | C1 cutoff counted many times | **Confirmed, worse than the spec** | `factors.py` `_spectral_factors`: O3 for MP3 holds F3.01, F3.02, F3.06 and F3.03 (when a lowpass is stored), all from the cutoff; F3.04 and F3.05 are left out, so the cutoff drives 100% of O3 | See N1: genuine 320 kbps file → D |
| 2 | C2 protection rules | **Partly present** | `spectrum.py:51` `MIN_DROP_DB = 20`: a fall under 20 dB per kHz is not a cliff, so a 6 dB/octave roll-off from 7 kHz scored 98.4 A. Nothing else protects; G4 needs only F3.02 Fail (`scoring.py:180`) | Steeper roll-offs pass through, see N1 |
| 3 | C3 F3.04 direction | **Not an issue in code** | F3.04 is left out (`factors.py` module docstring) | None; the spec contradiction (L1) remains |
| 4 | C4 bands, LAME only, bias | **Confirmed in code, mild in tests** | Bands match the matrix (`authenticity.py`); F3.02 ignores the declared lowpass; `EDGE_DROP_DB = 45`. Measured: 128 → 16.84, 160 → 17.53, 224 → 19.60, 256 → 19.58 kHz, all in their own band. 256 kbps sits 80 Hz above the High edge | No wrong verdict on this signal; little margin at 160 and 256 kbps |
| 5 | C5 low sample rates | **Confirmed** | `authenticity.py:38` evaluates F3.02 at 32 kHz. `d_32k_160.mp3`: cutoff 15.41 kHz → "claims medium, content supports very_low" | **G4, 59 D** for a genuine 32 kHz 160 kbps encode |
| 6 | C6 truncation gate | **Confirmed** | MP3: any missing byte (`mp3scan.py:159`). Ogg: missing EOS alone (`oggscan.py:120`). `i_trunc_320.mp3` (last 200 bytes cut, one 26 ms frame) and `p_vorbis_noeos.ogg` (complete file, EOS flag cleared) | **G2, 39 F** for both |
| 7 | C7 bitrate includes non-audio bytes | **MP3 fine; Vorbis affected** | MP3 averages frame-header bitrates, so tags never count. Vorbis uses all page bytes of the stream (`oggscan.py:136`), including the comment header and any embedded cover art | Vorbis bitrate reads high, which only makes F1.01 too generous; no false positive today |
| 8 | C8 look-alike formats | **Not reproduced** | `detect.py` rejects ADTS (layer bits 00), needs two consecutive valid frames, and reports Layer I/II separately. AAC in MP4 and 16-bit WAV, both renamed `.mp3`, came out Unsupported | None seen. Risk remains for random data matching two frames within the first 64 KB, since container magic numbers (RIFF, `ftyp`) are not checked first |
| 9 | H1 overshoot counted as clipping | **Not reproduced; false negative found instead** | `pcm.py:25` counts only `|x| >= 0.999`. A master hard-clipped at −0.1 dBFS (flat tops at 0.989) read 0.0000% clipped at 128 kbps, 320 kbps and Vorbis q4 | No false positive; real clipping just under full scale is missed (N4) |
| 10 | H2 LAME music CRC | **Not implemented** | No F2.04 factor exists yet | None today; apply H2 when it is built |
| 11 | H3 junk percentage | **Confirmed, small** | Zero padding before the first frame counts as junk (`mp3scan.py:556`). `j_pad4k_320.mp3` (4 KB of zeros after the ID3v2 tag): F2.03 = 36.7 | 98.4 → 97.2, still A (weight 5) |
| 12 | H4 encoder information | **Confirmed** | `factors.py:206`: anything but `LAMEx.y` ≥ 3.98 is Warn. Every FFmpeg-made MP3 reads `Lavc63.1.` → F1.05 = 50. F1.06 is left out when no preset is stored (correct), but 224 and 256 kbps CBR presets are Warn (`factors.py:229`) | About −1.5 points on every FFmpeg, iTunes or FhG file |
| 13 | H5 upsampling false alarm | **Confirmed, severe for Vorbis** | `spectrum.py:59` tests 32 kHz at 44.1 kHz: a cliff in 14.72–16.0 kHz reads "resampled from 32000". Hit: `s_112.mp3`, `t_96.mp3`, `m_vorbis_q1.ogg` | MP3 112 and 96 kbps lose about 10 points. Vorbis q1: O3 = 0, **44.4 D** (see N2) |
| 14 | H6 mono penalised | **Confirmed, small** | `factors.py:130, 160` mono → F1.04 Warn; `pcm.py:33` correlation ≥ 0.999 → F2.12 Warn | Mono 94.5 A; mono-in-stereo 97.3 A |
| 15 | H7 dynamics | **Confirmed** | Steady, loud material: LRA 0.0 LU → F2.10 = 0. No minimum duration, but a 6 s track still read 7.8 LU | 98.4 → 96.9 (weight 4); becomes significant once O4 exists |
| 16 | H8 decode-error double count | **Confirmed** | `decode_errors.py:88, 95` add the truncated tail to F2.06 as well as F2.02 | Inside a file that G2 has already capped |
| 17 | M2 F1.08 | **Correct** | Left out when nothing is declared; Xing frame excluded from the count; the cut file still matched its declared 1150 frames | None |
| 18 | M6 Vorbis fallback | **Correct, but O3 shrinks to one factor** | No MP3 table is used for Vorbis: F3.01 and F3.02 are left out (`factors.py:305`) | See N2 |
| 19 | L6 Later factors and coverage | **Correct** | Unimplemented factors are left out of both sums | None |

### 11.2 New findings from testing

**N1 (Critical). LAME's sfb21 cut creates a transcode-like wall in genuine high-bitrate files.** At 44.1 kHz, MP3's last scalefactor band (sfb21) starts at about 16 kHz. When the content there is weak relative to the rest of the frame, LAME codes nothing in it, even at 320 kbps. A natural, gradual roll-off becomes a steep wall at about 16 kHz: exactly where a 128 kbps transcode would put one.

| File (genuine FFmpeg/LAME 320 kbps CBR) | Source roll-off | Measured | Result |
| --- | --- | --- | --- |
| `gentle6dB_7000_320.mp3` | 6 dB/octave from 7 kHz | No cliff | 98.4 A |
| `k_gentle_320.mp3` | 24 dB/octave from 11 kHz | Cliff 17.26 kHz, F3.02 Warn | 81.6 B |
| `gentle_9000_320.mp3` | 24 dB/octave from 9 kHz | Cliff 16.26 kHz, F3.02 Fail | **59 D, G4** |
| `gentle_7000_320.mp3` | 24 dB/octave from 7 kHz | Cliff 16.16 kHz, F3.02 Fail | **59 D, G4** |
| `gentle_5000_320.mp3` | 24 dB/octave from 5 kHz | Cliff 16.10 kHz, F3.02 Fail | **59 D, G4** |

In the 11 kHz case the averaged spectrum falls from −112.7 dB at 16.5 kHz to −164 dB at 18 kHz: the encoder made a cliff out of a slope. Dark-sounding sources (older analog masters, many film soundtracks, solo voice or instruments) can therefore be flagged as transcodes at any bitrate.

Consequences for the protection rules (C2):

- P1 cannot rely on slope alone, because the encoder sharpens the slope.
- A useful discriminator is the **level of the plateau just below the wall, relative to the loud part of the spectrum**. In `k_gentle_320.mp3` the plateau sits about 35 dB below the 10 kHz level; in a real transcode the content runs at nearly full level up to the wall (`a_320.mp3`: −74 dB at both 10 kHz and 19 kHz). A wall reached only after the content has already faded is the sfb21 cut, not a transcode.
- A wall at 15.9–16.3 kHz in a file whose bitrate makes the High band its claim needs an explicit "possible sfb21 cut" rule before G4 may apply.

**N2 (High). For Vorbis, O3 is a single factor.** With F3.01 and F3.02 left out, O3 for Vorbis is F3.06 alone, renormalised to 100% of O3 and 45% of the final score. One false "resampled" verdict sets O3 to 0 (`m_vorbis_q1.ogg`: 44.4 D). A pass gives O3 = 100, so a genuine q2 Vorbis at 96 kbps scored 91.6 A while F1.01 gave it 33. Until the Vorbis tables exist (T11), O3 should be N/A for Vorbis rather than resting on F3.06.

**N3 (High, affects C4, P2 and M4). FFmpeg's LAME tag stores no lowpass, no preset and no VBR quality.** Files made with FFmpeg's `libmp3lame` (`a_320.mp3`, `c_v0_ffmpeg.mp3`) have lowpass 0, preset 0, VBR method "Unknown" and Xing quality 0. For these files F3.03 never runs, protection rule P2 has nothing to compare, and V-levels cannot be recovered. Many batch converters and download tools use FFmpeg, so the generic band table is the only evidence for a large share of real files. Files from the `lame` command-line encoder were not tested.

**N4 (Medium, false negative). Clipping just below full scale is missed.** A master hard-clipped at 0.989 (−0.1 dBFS), which is typical of loud masters, read 0% clipped after lossy encoding. Flat tops at any level, found from the sample-value histogram or run detection at the file's own maximum, would catch it. This matters more once O4 scores mastering.

**N5 (Medium). An Ogg Skeleton stream makes a Vorbis file Unsupported.** `detect.py:192` takes the first stream's codec and marks the file unsupported unless every stream is Vorbis. A `.oga` that starts with a Skeleton stream (Xiph's recommendation for that extension) is therefore never analysed. Found by reading the code; not tested.

**N6 (Low). Without a cliff, the cutoff is taken as Nyquist and fed to F3.02.** At 32 kHz that makes the effective band Very low even for a file with no detectable wall. This is the same defect as C5 and is fixed by the same change.

### 11.3 Priorities for fixing

| Priority | Fix | Findings |
| --- | --- | --- |
| 1 | Truncation: gate only on material loss; missing EOS on a complete final page becomes Warn or Info | C6, H8 |
| 2 | G4 needs strong evidence: plateau-level rule for sfb21 walls, no F3.02 below 44.1 kHz, F3.02 only from a real cliff. Not gating FFmpeg-tagged files was considered and rejected, because FFmpeg is also the usual tool for making transcodes; the plateau rule needs no encoder information | N1, C5, N6, N3 |
| 3 | F3.06 Info for lossy formats; O3 N/A for Vorbis until T11 | H5, N2 |
| 4 | Dual-result model (section 9) | C1 structurally |
| 5 | F1.05 recognises `Lavc`/`Lavf` as LAME and treats unknown as Info; mono and mono-in-stereo become flags | H4, H6 |
| 6 | Clipping by flat-top detection; dynamics by peak-to-loudness ratio with a minimum duration (part of O4) | N4, H7 |
| 7 | Skeleton streams accepted; container magic numbers checked before the MPEG search; zero padding not counted as junk; Vorbis bitrate excludes header packets | N5, C8, H3, C7 |

### 11.4 Real samples and the peak-hold measure

Nov 20, 2026 · `qctrack scan ~/Lab/samples` (14 files, read-only, with the owner's permission).

The fade threshold from 7.2 (12 dB) turned out too lenient on real music: genuine 320 kbps files fade 29–35 dB toward 20 kHz, and the fades at 16 kHz walls spread from 4 to 20 dB, so fade alone cannot separate a dark source from a transcode. A per-frame measure can: a file made from a lower-bitrate source has nothing above that source's lowpass in any frame, while LAME's sfb21 cut works frame by frame.

| File | Claims | Wall | Fade | Content above the wall | Outcome |
| --- | --- | --- | --- | --- | --- |
| sample.mp3 | 320 kbps | 16.3 kHz | 12.6 dB | 6.5% of loud frames | Keep: source-limited |
| Dooreyaanu Keralam | 320 kbps | 16.2 kHz | 17.0 dB | 2.7% (borderline) | Keep: source-limited |
| Bajagovindam | 242 kbps VBR | 17.0 kHz | 14.8 dB | 28.4% | Keep: source-limited |
| Adimarunge | 192 kbps | 16.15 kHz | 20.2 dB | 0.4% | Undetermined |
| Synthetic transcodes (96-128 kbps, V5, Vorbis q2) | 320 kbps | 15.5-16.9 kHz | 2-23 dB | 0-0.4% | G4, or Undetermined when faded |

These labels are evidence-based but not proof: a transcode of a VBR source, which also codes the top band only in some frames, would look like the files above. Labelled files (T10) remain the way to settle the thresholds.
