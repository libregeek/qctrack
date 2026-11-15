# Accuracy findings (todo 6.1)

Nov 15, 2026 · covers decoder match, Q-A7 and Q-A10

**Scope and limits.** Two sources: signals generated and encoded in the scratchpad with FFmpeg (libmp3lame 128–320 kbps and V0, libvorbis q3 and q6), and the 15 real files in `~/Lab/samples` (read only, with the owner's permission; nothing copied into the project). The labelled corpus (T10) does not exist yet, so the real-file results show agreement with FFmpeg but cannot separate source loudness from codec overshoot. Scripts (run from the project root): `tools/accuracy/decoder_peaks.py FILE...`, `tools/accuracy/overshoot.py SCRATCH_DIR` and `tools/accuracy/real_files.py FILE...`.

## Decoder match

`mpg123` (through our binding) and FFmpeg give the same sample peak to four decimals on every generated MP3 (0 to −3 dBFS 1 kHz tones, 128 and 320 kbps). Where the two decodes line up (128 kbps, delay 1105 samples), the largest difference is 0.00000 and the RMS difference is 6.7e-7. **Real files (13 MP3, 1 Ogg Vorbis; `sample (Copy).mp3` is really an Ogg, byte-identical to `sample.ogg`).** Integrated loudness and true peak agree with FFmpeg's `ebur128=peak=true` to within its 0.1 rounding on all of them, at 44.1 and 48 kHz (for example `sample.mp3` −7.90 LUFS and 3.51 dBTP against −7.9 and 3.5). Sample-by-sample match against an FFmpeg decode, after removing the decoder delay: 122.1 dB (`01-Kunnimani Cheppu.mp3`, 48 kHz), 122.4 dB (`Bajagovindam.mp3`, 48 kHz), 122.3 dB (`sample.mp3`) and 119.3 dB (`Keli Vipinam-M.mp3`); `sample.ogg` was 135.7 dB earlier. Nothing suggests a decoder fault.
Note: `mpg123` returns the encoder padding (222 336 samples for a 5 s file, against FFmpeg's 220 500), so any length-based figure must use the LAME tag, as F1.08 does.

## Q-A7: `sin1k0db` peaks at 1.18 in FFmpeg but 1.03 in `mpg123`

**Not reproduced.** No generated file and none of the 14 real files shows a peak difference between `mpg123` and FFmpeg. The conformance file is not in `~/Lab/samples` either, so the cause is still unknown; it stays open until the file is supplied. Likely candidates, to test when the file is available: the FFmpeg run used a different float decoder path or applied gain, or the file's decoder-delay trimming differs. Needed: the file, then compare `decoder_peaks.py` output with `ffmpeg -af ebur128=peak=true`.

## Q-A10: true-peak thresholds against codec overshoot

Method: a synthetic soft-limited master is scaled to an exact source true peak (−1.0, −0.3, 0.0 dBTP), encoded, decoded with our bindings and measured with `LoudnessAnalyzer`. Overshoot = decoded true peak − source true peak. Profile curve for F2.08: 100 at −1 dBTP, 50 at +0.5, 0 at +3.

| Codec | Overshoot range (dB) |
| --- | --- |
| MP3 192/320 kbps | −0.06 to +0.03 |
| MP3 V0 | +0.09 to +0.28 |
| MP3 128 kbps | +0.63 to +0.69 |
| Vorbis q6 | +0.09 to +0.50 |
| Vorbis q3 | +1.19 to +1.47 |

Consequences: a master at the usual −1 dBTP ceiling comes out of 128 kbps MP3 at −0.3 dBTP and out of Vorbis q3 at +0.2 dBTP; a master at 0 dBTP reaches +0.7 and +1.2. So with the current curve (Fail band starts at +0.5, score 50) a loud but legitimate low-bitrate file is penalised for the encoder's overshoot, not for clipping at the source. This agrees with the 43 of 100 MP3s and 13 of 17 Ogg files seen earlier.

Real files: 9 of the 14 measured files have a true peak above +0.5 dBTP (range −0.70 to +3.51). The three 128 kbps MP3s read +0.95 (`Track9`), +1.34 (`adimarangai`) and +1.94 (`Bhoothaganangalkk-F`), but so do five 320 kbps files (+1.22 to +3.51). The synthetic table says 320 kbps adds almost nothing, so those peaks come from the masters themselves (loud, limited at or above 0 dBFS), not from the codec. They still score A or B because F2.08 has weight 3 (`01-Kunnimani Cheppu` has +1.92 dBTP and scores 96.4 A).

**Recommendation (needs your decision):** keep the curve for lossless-quality sources, but for MP3 below ~160 kbps and Vorbis below ~q5 shift the F2.08 points by the table's upper bound (+0.7 dB MP3 128, +1.5 dB Vorbis q3), or move to one lossy curve of 100 at 0, 50 at +1.5, 0 at +3.5. True peak must not be treated as a clipping verdict by itself; F2.07 (clipped sample runs) already covers real clipping. Not done: no profile change was made.

## Other observations from scanning the real samples

- Three files get G4 (cap 59, D): `Adimarunge.mp3`, `Dooreyaanu Keralam.mp3` and `sample.mp3`. The last two match the earlier finding that 320 kbps files cut at about 16.2 kHz are flagged as transcodes; `Adimarunge` is 192 kbps with a measured TP of +0.47. Please judge whether those three are true transcodes (T10 labels would settle it).
- `sample.flac` is Unsupported (G1) as designed, and `sample (Copy).mp3` is detected by content as Ogg Vorbis and scores 91.0 A, the same as `sample.ogg`.

## Still open for 6.1

1. Q-A7 needs the `sin1k0db` file.
2. Overshoot against real music needs source/encode pairs (T10 corpus); the real files here have no known source.
3. F2.08 decision (made): keep the current curve for now and revisit it under T10, once source/encode pairs exist. No profile change.
