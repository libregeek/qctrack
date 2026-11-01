# MP3 Quality Evaluation Matrix

Three quality **objectives**, each broken into measurable **factors**, mapped across four formats. Release 1 covers MP3 and Ogg Vorbis; release 2 adds FLAC and Opus. All analysis is read-only; nothing is played or written.

Thresholds are starting points to tune against a labelled test set per codec. Lossy codecs need their own cutoff-to-quality tables: an MP3 table will misjudge Vorbis and Opus. Show verdicts as Pass, Warn, Fail or Info, and never present a single heuristic as proof. "Poor" in the tables below is reported as a Fail verdict.

## Format roadmap

| Format | Release | Decoder | Notes |
| --- | --- | --- | --- |
| MP3 | 1 | `libmpg123` | MPEG-1/2 Layer III, LAME tag |
| Ogg Vorbis | 1 | `libogg`, `libvorbis` | .ogg and .oga, Vorbis comments |
| FLAC | 2 | `libFLAC` | Native .flac and Ogg FLAC |
| Opus | 2 | `libopus`, `libogg` | .opus, often also .ogg |

Release 1 must still detect Opus in .ogg files (F1.10) and label it "supported in a later release", rather than failing or misreporting it as Vorbis. For FLAC the focus shifts from "how good is this encode" to "is this really lossless": MD5 verification, fake-lossless and fake bit-depth detection.

## Pipeline

1. Detect the real format first (F1.10) and route to the codec's parser.
2. Pass 1: header and raw-byte factors over the whole library.
3. Pass 2: decode once and stream chunks to every PCM factor (FFT, loudness, clipping, channels).
4. Pass 3: derived factors and the scoring profile.

## MP3 bitrate bands

These bands set F1.01 and feed F3.02. The names describe the bitrate only. They avoid Excellent, Good, Fair, Poor and Bad because those are the labels of the A–F track ratings.

| Band | Average bitrate | F1.01 verdict | F1.01 score |
| --- | --- | --- | --- |
| High | ≥ 256 kbps, or LAME V0–V2 | Pass | 100 |
| Medium | 160–255 kbps | Warn | 75 at 160, rising to 100 at 256 |
| Low | 128–159 kbps | Warn | 50 at 128, rising to 75 at 160 |
| Very low | < 128 kbps | Fail | 0 at 64 or below, rising to 50 at 128 |

Medium runs to 255 kbps so that VBR averages between 225 and 255 kbps, which are common, have a band. A file with a V0–V2 preset in its LAME tag is High whatever its average, because VBR averages vary with the music.

### MP3 cutoff bands for F3.02

F3.02 compares the band a file claims with the band its measured cutoff (F3.01) supports. One band lower is a Warn. Two or more bands lower is a Fail, which triggers gate G4.

| Effective band | Measured cutoff | Measured at (LAME, one test clip) |
| --- | --- | --- |
| High | ≥ 19.5 kHz | 224, 256 and 320 kbps; V0 |
| Medium | 17.25–19.5 kHz | 144, 160 and 192 kbps; V2, V4 |
| Low | 16.5–17.25 kHz | 128 kbps; V6 |
| Very low | < 16.5 kHz | 112 kbps and below |

The claimed band comes from the bitrate table, except that a LAME preset sets it directly, so a preset is not judged against a band it never aims for:

| Preset | Claimed band for F3.02 |
| --- | --- |
| V0, V1 | High |
| V2, V3, V4 | Medium |
| V5 and lower quality | Low |

These cutoffs and presets come from a single clip, and V1, V3, V5 and V7 were not measured, so they are starting values to tune with T10 and T11.

Formats below list the method for each format. "Adapted" means the factor applies with a codec-specific method or limited value.

## O1 Metadata & encoding

What the file declares about how it was encoded. No audio decoding needed.

### F1.01 Nominal & average bitrate

Headline quality indicator users expect.

- **Input:** Header
- **Metric:** kbps (nominal, avg, min/max)
- **Effort:** Low
- **False-verdict risk:** Medium. Meaningless alone for transcodes; see F3.02.
- **Priority:** MVP
- **Reference:** mediainfo, ffprobe
- **Method:** Read declared bitrate; average = audio bytes × 8 / duration.
- **Thresholds:** Pass (High): ≥ 256 kbps, or V0–V2; Warn (Medium): 160–255 kbps; Warn (Low): 128–159 kbps; Fail (Very low): < 128 kbps

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Bitrate index per frame; average from audio bytes ÷ duration. Scored from the band table below. | — |
| Ogg Vorbis | Applies | Nominal / min / max from the identification header; average from stream bytes ÷ duration. | Pass: ≥ 160 kbps (≈ q5+); Warn: 112–159 kbps; Poor: < 112 kbps |
| FLAC | Adapted | Average reflects compression ratio, not quality. Report only. | Info: Informational for lossless |
| Opus | Applies | Average from packet sizes over duration. | Pass: ≥ 128 kbps stereo; Warn: 64–127 kbps; Poor: < 64 kbps music |

### F1.02 Bitrate mode

CBR / VBR / ABR changes how bitrate should be judged.

- **Input:** Header
- **Metric:** CBR \| VBR \| ABR
- **Effort:** Low
- **False-verdict risk:** Low. Some encoders write wrong tags; verify with variance.
- **Priority:** MVP
- **Reference:** mp3guessenc
- **Method:** Detect constant vs variable bitrate from tags, then verify with packet-size variance.
- **Thresholds:** Info: Informational; selects bitrate rules

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Xing tag = VBR, Info tag = CBR, LAME VBR-method field; per-frame variance as fallback. | — |
| Ogg Vorbis | Adapted | Vorbis is VBR by design; min/max header hints reveal managed (ABR-like) bitrate. | — |
| FLAC | Not applicable | Not applicable to lossless. |  |
| Opus | Applies | VBR / constrained VBR / CBR from packet-size variance. | — |

### F1.03 Codec version & sample rate

Low sample rates cap available bandwidth.

- **Input:** Header
- **Metric:** Codec version · Hz
- **Effort:** Low
- **False-verdict risk:** Low. Speech and podcasts may intentionally use low rates.
- **Priority:** MVP
- **Reference:** mediainfo
- **Method:** Read version and sample rate from the codec header; check consistency across the stream.
- **Thresholds:** Pass: 44.1 / 48 kHz; Warn: 32 kHz; Poor: ≤ 24 kHz

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | MPEG version, layer and sample-rate bits of the first valid frame; consistency across frames. | — |
| Ogg Vorbis | Applies | Sample rate and Vorbis version from the identification header. | — |
| FLAC | Applies | Sample rate from STREAMINFO. | Pass: ≥ 44.1 kHz; Warn: < 44.1 kHz |
| Opus | Adapted | Always decodes at 48 kHz; the header's input sample rate is informational only. | Info: Report original input rate |

### F1.04 Channel layout

Mono music, or stereo at very low bitrates, degrades quality.

- **Input:** Header
- **Metric:** channels · mode
- **Effort:** Low
- **False-verdict risk:** Low. Mono can be correct for old or speech recordings.
- **Priority:** MVP
- **Reference:** mediainfo, mp3guessenc
- **Method:** Read channel count and stereo mode.
- **Thresholds:** Pass: Stereo or joint stereo; Warn: Mono music; intensity stereo; Info: Multichannel

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Channel-mode bits; count mid/side and intensity-stereo frames. | — |
| Ogg Vorbis | Applies | Channel count from header; coupling is in the setup header and rarely needed. | — |
| FLAC | Applies | Channel count and assignment from STREAMINFO and frame headers. | — |
| Opus | Applies | Channel count and channel-mapping family from OpusHead. | — |

### F1.05 Encoder identity & version

Encoder quality varies between implementations and versions.

- **Input:** Header
- **Metric:** Encoder name + version
- **Effort:** Low–Medium
- **False-verdict risk:** Medium. Strings can be copied or missing; guesses are probabilistic.
- **Priority:** v1
- **Reference:** mp3guessenc, vorbiscomment, metaflac
- **Method:** Read the encoder or vendor string; fingerprint if missing.
- **Thresholds:** Pass: Current reference encoder; Warn: Old, obscure or unknown encoder

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | LAME tag version string; fingerprint frame patterns if absent. | Pass: LAME ≥ 3.98; Warn: Old LAME, Xing, Blade, unknown |
| Ogg Vorbis | Applies | Vendor string in the comment header (libVorbis release date, aoTuV, other tunings). | — |
| FLAC | Applies | Vendor string in VORBIS_COMMENT block (libFLAC version). | — |
| Opus | Applies | Vendor string in OpusTags (libopus version). | — |

### F1.06 Encoder quality setting

Reveals the intended quality level (V0, -q 6, etc.).

- **Input:** Header
- **Metric:** Preset / quality level
- **Effort:** Low–Medium
- **False-verdict risk:** Low. Only some encoders record it.
- **Priority:** v1
- **Reference:** mp3guessenc
- **Method:** Read stored preset, or estimate from header fields.
- **Thresholds:** Pass: High-quality preset; Warn: Low-quality or fast preset

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | LAME tag: VBR quality, preset code, encoding flags. | Pass: V0–V2, 320 CBR, insane/extreme; Warn: V4 or lower, fast mode |
| Ogg Vorbis | Adapted | Estimate -q level from nominal bitrate (no explicit field). | Pass: ≈ q5 or higher; Warn: ≈ q3 or lower |
| FLAC | Not applicable | Compression level does not affect quality. |  |
| Opus | Adapted | ENCODER_OPTIONS comment, when opusenc wrote it. | — |

### F1.07 Declared bandwidth / lowpass

Encoder's own bandwidth setting; compared with measured cutoff in F3.03.

- **Input:** Header
- **Metric:** Hz or bandwidth class
- **Effort:** Low
- **False-verdict risk:** Low. Not stored by every codec.
- **Priority:** v1
- **Reference:** mp3guessenc, opusinfo
- **Method:** Read the encoder's declared audio bandwidth where the format stores it.
- **Thresholds:** Info: Reference value for F3.03

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | LAME tag lowpass byte × 100 Hz. | — |
| Ogg Vorbis | Not applicable | Not stored in the stream. |  |
| FLAC | Not applicable | Not applicable to lossless. |  |
| Opus | Applies | Per-packet TOC byte gives bandwidth: NB, MB, WB, SWB or FB. | Pass: Fullband (FB) for music; Warn: SWB or narrower for music |

### F1.08 Declared vs counted length

Mismatch suggests editing, joining or a bad header.

- **Input:** Header
- **Metric:** Δ samples, Δ seconds
- **Effort:** Low
- **False-verdict risk:** Low. Some tools never update headers after edits.
- **Priority:** MVP
- **Reference:** mpck, ogginfo, flac -t
- **Method:** Compare the length the header declares with what is actually present.
- **Thresholds:** Pass: Exact match; Warn: Mismatch < 1%; Fail: Mismatch ≥ 1%

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Xing frame/byte counts vs frames counted in pass 1. | — |
| Ogg Vorbis | Applies | Final page granule position vs decoded sample count. | — |
| FLAC | Applies | STREAMINFO total samples vs decoded sample count. | — |
| Opus | Applies | Final granule position minus pre-skip vs decoded sample count. | — |

### F1.09 Tags & stored gain

Tag health and existing loudness normalisation values.

- **Input:** Header
- **Metric:** Tag list, validity, gain dB
- **Effort:** Low–Medium
- **False-verdict risk:** Low. Tags do not affect audio quality; keep separate in UI.
- **Priority:** v1
- **Reference:** mutagen, exiftool
- **Method:** Parse tags, validate structure, read stored gain fields.
- **Thresholds:** Pass: Valid tags; Warn: Malformed, duplicate or foreign tags

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | ID3v1, ID3v2, APEv2; ReplayGain frames. | — |
| Ogg Vorbis | Applies | Vorbis comments; REPLAYGAIN_* fields. | — |
| FLAC | Applies | VORBIS_COMMENT and PICTURE blocks; flag ID3 tags wrongly attached to FLAC. | — |
| Opus | Applies | OpusTags comments; R128_TRACK_GAIN; output gain in OpusHead. | — |

### F1.10 Real format detection

Files are often renamed; .ogg can hold Vorbis, Opus, FLAC or Speex.

- **Input:** Raw bytes
- **Metric:** Container + codec
- **Effort:** Low
- **False-verdict risk:** Low. Very low.
- **Priority:** MVP
- **Reference:** file, ffprobe
- **Method:** Sniff magic bytes and the first codec packet; ignore the extension.
- **Thresholds:** Pass: Matches a supported codec; Warn: Supported in a later release; Fail: Unknown or mismatched

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | MPEG frame sync after any ID3v2 tag. | — |
| Ogg Vorbis | Applies | OggS capture pattern, then \x01vorbis identification packet. | — |
| FLAC | Applies | fLaC marker, or Ogg stream whose first packet starts \x7FFLAC. | — |
| Opus | Applies | Ogg stream whose first packet starts OpusHead. | — |

### F1.11 Bit depth

Lossless resolution claim (16 / 24-bit).

- **Input:** Header
- **Metric:** bits
- **Effort:** Low
- **False-verdict risk:** Low. Declared value can be padded; see F3.10.
- **Priority:** MVP
- **Reference:** metaflac
- **Method:** Read bits per sample.
- **Thresholds:** Info: 16 or 24 bit; verified by F3.10

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Not applicable | Lossy; no bit depth. |  |
| Ogg Vorbis | Not applicable | Lossy; no bit depth. |  |
| FLAC | Applies | STREAMINFO bits per sample. | — |
| Opus | Not applicable | Lossy; no bit depth. |  |

## O2 Technical integrity

Whether the stream is structurally sound and the audio signal is technically healthy.

### F2.01 Stream structure validity

Core structural integrity of the container and frames.

- **Input:** Raw bytes
- **Metric:** Invalid units, desync points
- **Effort:** Low–Medium
- **False-verdict risk:** Low. Low; well-specified formats.
- **Priority:** MVP
- **Reference:** mpck, mp3val, ogginfo, flac -t
- **Method:** Walk every frame or page; validate headers, lengths and continuity.
- **Thresholds:** Pass: 0 errors; Warn: 1–3 recoverable; Fail: Persistent desync

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Frame sync word and header-computed lengths; header consistency across frames. | — |
| Ogg Vorbis | Applies | OggS capture per page, page sequence numbers, stream serial, header packets present. | — |
| FLAC | Applies | Frame sync code and header CRC-8 for every frame; metadata block chain. | — |
| Opus | Applies | OggS pages and sequence numbers; OpusHead and OpusTags present. | — |

### F2.02 Truncation

Incomplete downloads or cut files.

- **Input:** Raw bytes
- **Metric:** Missing bytes / ms
- **Effort:** Low
- **False-verdict risk:** Low. Low.
- **Priority:** MVP
- **Reference:** mpck, ogginfo
- **Method:** Check the stream ends cleanly.
- **Thresholds:** Pass: Complete; Fail: Truncated

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Last frame complete and matches header length. | — |
| Ogg Vorbis | Applies | Final page complete and carries the end-of-stream flag. | — |
| FLAC | Applies | Final frame complete; decoded total matches STREAMINFO. | — |
| Opus | Applies | Final page complete and carries the end-of-stream flag. | — |

### F2.03 Unidentified data

Garbage between frames or appended junk.

- **Input:** Raw bytes
- **Metric:** bytes, % of file
- **Effort:** Low
- **False-verdict risk:** Low. Unknown tag formats may count as junk.
- **Priority:** MVP
- **Reference:** mpck
- **Method:** Sum bytes that are neither valid frames/pages nor known metadata.
- **Thresholds:** Pass: 0%; Warn: ≤ 0.1%; Fail: > 0.1%

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Bytes outside frames, ID3 and APE tags. | — |
| Ogg Vorbis | Applies | Bytes outside Ogg pages. | — |
| FLAC | Applies | Bytes outside metadata blocks and frames. | — |
| Opus | Applies | Bytes outside Ogg pages. | — |

### F2.04 Embedded audio checksum

Proves audio is unchanged since encoding.

- **Input:** Raw bytes
- **Metric:** match \| mismatch \| n/a
- **Effort:** Low–Medium
- **False-verdict risk:** Low. Lossless edits (mp3gain, trimming) also break the MP3 CRC; report as modified.
- **Priority:** v1
- **Reference:** mp3guessenc, flac -t
- **Method:** Recompute the checksum the encoder stored and compare.
- **Thresholds:** Pass: Match; Fail: Mismatch; Info: No checksum stored

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | CRC-16 over audio frames vs music CRC in the LAME tag. | — |
| Ogg Vorbis | Not applicable | Format stores no whole-stream checksum. |  |
| FLAC | Applies | MD5 of decoded audio vs STREAMINFO signature. The strongest integrity check available. Input: Decoded PCM. | Pass: MD5 match; Fail: MD5 mismatch; Info: MD5 not set (all zeros) |
| Opus | Not applicable | Format stores no whole-stream checksum. |  |

### F2.05 Block-level CRC

Bit errors inside individual frames or pages.

- **Input:** Raw bytes
- **Metric:** Failed blocks
- **Effort:** Low–Medium
- **False-verdict risk:** Low. Low.
- **Priority:** MVP
- **Reference:** mpck, ogginfo, flac -t
- **Method:** Verify the CRC carried by each frame or page.
- **Thresholds:** Pass: 0; Fail: ≥ 1

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Adapted | CRC-16 only on frames with the protection bit set (rare). | Pass: 0; Fail: ≥ 1; Info: Not protected |
| Ogg Vorbis | Applies | CRC-32 on every Ogg page (mandatory). | — |
| FLAC | Applies | CRC-16 on every frame (mandatory). | — |
| Opus | Applies | CRC-32 on every Ogg page (mandatory). | — |

### F2.06 Decode errors

Damage that structure checks alone don't show.

- **Input:** Decoded PCM
- **Metric:** Error count, timestamps
- **Effort:** Low–Medium
- **False-verdict risk:** Low. Decoder strictness differs.
- **Priority:** MVP
- **Reference:** ffmpeg -v error
- **Method:** Count errors and concealed packets reported by the decoder.
- **Thresholds:** Pass: 0; Warn: 1–5; Fail: > 5

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Bad side info, Huffman or bit-reservoir errors. | — |
| Ogg Vorbis | Applies | Packet decode failures. | — |
| FLAC | Applies | Residual or subframe decode failures. | — |
| Opus | Applies | Packet decode failures; PLC invocations. | — |

### F2.07 Clipping

Audible distortion from overloaded mastering or encoding.

- **Input:** Decoded PCM
- **Metric:** Clipped runs, % samples
- **Effort:** Low–Medium
- **False-verdict risk:** Medium. Loud masters clip by design; lossy decoders can overshoot 0 dBFS.
- **Priority:** MVP
- **Reference:** ffmpeg astats
- **Method:** Float decode; count runs of ≥ 3 consecutive samples at |x| ≥ 0.999.
- **Thresholds:** Pass: < 0.001% samples; Warn: 0.001–0.1%; Fail: > 0.1%

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | — | — |
| Ogg Vorbis | Applies | — | — |
| FLAC | Applies | Integer samples: count runs at the format's maximum value. | — |
| Opus | Applies | — | — |

### F2.08 Peak & true peak

Headroom and inter-sample overs.

- **Input:** Decoded PCM
- **Metric:** dBFS, dBTP
- **Effort:** Low–Medium
- **False-verdict risk:** Low. Low.
- **Priority:** v1
- **Reference:** libebur128
- **Method:** Sample peak; true peak with 4× oversampling (BS.1770).
- **Thresholds:** Pass: ≤ −1 dBTP; Warn: −1 to +0.5 dBTP; Fail: > +0.5 dBTP

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | — | — |
| Ogg Vorbis | Applies | — | — |
| FLAC | Applies | — | — |
| Opus | Applies | — | — |

### F2.09 Integrated loudness

Over-compression and level consistency.

- **Input:** Decoded PCM
- **Metric:** LUFS
- **Effort:** Low–Medium
- **False-verdict risk:** Medium. Genre-dependent; better shown than judged.
- **Priority:** MVP
- **Reference:** libebur128, rsgain
- **Method:** EBU R128 / ITU-R BS.1770 gated loudness.
- **Thresholds:** Pass: −20 to −9 LUFS; Warn: > −9 LUFS (heavily limited); Info: < −20 LUFS (quiet)

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | — | — |
| Ogg Vorbis | Applies | — | — |
| FLAC | Applies | — | — |
| Opus | Applies | Apply OpusHead output gain before measuring. | — |

### F2.10 Dynamic range

Squashed dynamics (loudness war).

- **Input:** Decoded PCM
- **Metric:** LU (LRA), dB (PLR)
- **Effort:** Low–Medium
- **False-verdict risk:** Medium. Some genres are low by design.
- **Priority:** v1
- **Reference:** libebur128
- **Method:** Loudness range (LRA, EBU 3342) and peak-to-loudness ratio.
- **Thresholds:** Pass: LRA ≥ 5 LU; Warn: LRA 3–5 LU; Poor: LRA < 3 LU

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | — | — |
| Ogg Vorbis | Applies | — | — |
| FLAC | Applies | — | — |
| Opus | Applies | — | — |

### F2.11 DC offset

Faulty source or processing.

- **Input:** Decoded PCM
- **Metric:** % of full scale
- **Effort:** Low
- **False-verdict risk:** Low. Low.
- **Priority:** Later
- **Reference:** ffmpeg astats
- **Method:** Mean sample value per channel.
- **Thresholds:** Pass: < 0.1%; Warn: 0.1–1%; Fail: > 1%

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | — | — |
| Ogg Vorbis | Applies | — | — |
| FLAC | Applies | — | — |
| Opus | Applies | — | — |

### F2.12 Channel anomalies

Dead channel, fake stereo, inverted polarity.

- **Input:** Decoded PCM
- **Metric:** RMS dB, correlation −1…1
- **Effort:** Low–Medium
- **False-verdict risk:** Medium. Mono-sourced recordings are legitimately correlated.
- **Priority:** v1
- **Reference:** ffmpeg aphasemeter
- **Method:** RMS per channel; L/R correlation; side-signal energy.
- **Thresholds:** Pass: Both active, corr > 0; Warn: Corr ≈ 1 (fake stereo); Fail: Dead channel or corr < −0.5

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | — | — |
| Ogg Vorbis | Applies | — | — |
| FLAC | Applies | — | — |
| Opus | Applies | — | — |

### F2.13 Silence & dropouts

Gaps, excessive padding, cut-outs mid-track.

- **Input:** Decoded PCM
- **Metric:** Positions, durations
- **Effort:** Low
- **False-verdict risk:** Medium. Intentional pauses in classical or spoken content.
- **Priority:** Later
- **Reference:** ffmpeg silencedetect
- **Method:** Detect runs below −60 dBFS; classify leading, trailing, internal.
- **Thresholds:** Pass: No internal gaps; Warn: Internal gap > 0.5 s; Info: Lead/trail silence

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Account for encoder delay and padding from the LAME tag. | — |
| Ogg Vorbis | Applies | — | — |
| FLAC | Applies | — | — |
| Opus | Applies | Account for pre-skip. | — |

### F2.14 Ogg stream layout

Chained or multiplexed streams and granule gaps break players and length reporting.

- **Input:** Header
- **Metric:** Streams, chain links, granule gaps
- **Effort:** Low–Medium
- **False-verdict risk:** Low. Chained streams are legal; report rather than fail.
- **Priority:** MVP
- **Reference:** ogginfo
- **Method:** Enumerate logical streams by serial; check chaining, multiplexing and granule-position continuity.
- **Thresholds:** Pass: One stream, continuous granules; Warn: Chained streams (joined files); Fail: Granule gaps or going backwards

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Not applicable | Not an Ogg format. |  |
| Ogg Vorbis | Applies | — | — |
| FLAC | Adapted | Only for Ogg FLAC; native FLAC has no pages. | — |
| Opus | Applies | — | — |

## O3 Authenticity & transcode

Whether the audio content matches the quality the file claims.

### F3.01 Measured spectral cutoff

Real audio bandwidth, the main evidence of source quality.

- **Input:** Decoded PCM
- **Metric:** Hz
- **Effort:** Medium
- **False-verdict risk:** Medium. Old or band-limited recordings have natural low cutoffs.
- **Priority:** MVP
- **Reference:** Spek, fakeflac-go
- **Method:** Hann-windowed 4096-pt FFT, average magnitude (dB) over loud frames; scan down from Nyquist for a sustained energy rise.
- **Thresholds:** Pass: ≥ 19.5 kHz; Warn: 17–19.5 kHz; Poor: ≤ 16.5 kHz

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Compare against the MP3 cutoff table below. | Pass: ≥ 19.5 kHz; Warn: 16.5–19.5 kHz; Poor: < 16.5 kHz |
| Ogg Vorbis | Applies | Vorbis lowpass follows the -q level; build a Vorbis-specific table from reference encodes. | Pass: Consistent with ≈ q5+; Warn: Consistent with q3–q4; Poor: Consistent with ≤ q2 |
| FLAC | Applies | Genuine lossless reaches close to Nyquist; a hard cutoff below it indicates a lossy source. | Pass: Content or noise to ≥ 21 kHz; Warn: 19.5–21 kHz; Fail: < 19.5 kHz brick wall |
| Opus | Applies | Opus fullband stops near 20 kHz by design; judge relative to the TOC bandwidth. | Pass: Matches TOC bandwidth; Warn: 1–2 kHz below it; Fail: > 2 kHz below it |

### F3.02 Claimed vs effective quality

Core fake-bitrate / fake-lossless verdict.

- **Input:** Derived
- **Metric:** Effective quality estimate, mismatch
- **Effort:** Low–Medium
- **False-verdict risk:** High. Encoders and lowpass settings vary; tune each codec's table.
- **Priority:** MVP
- **Reference:** Fakin' The Funk (non-FOSS, for comparison)
- **Method:** Map F3.01 cutoff to the codec's expected quality band; compare with F1.01 / F1.06.
- **Thresholds:** Pass: Consistent; Warn: One band lower; Fail: ≥ 2 bands lower (likely upscale)

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | Effective band from the MP3 cutoff table vs the band claimed by bitrate or preset. Tables below. | — |
| Ogg Vorbis | Applies | Vorbis q-level bands vs nominal bitrate. | — |
| FLAC | Applies | Lossless claim: any lossy-style brick wall is a Fail (fake FLAC). | Pass: No lossy signature; Warn: Ambiguous high cutoff; Fail: Lossy signature |
| Opus | Applies | Opus bitrate/bandwidth bands; transcodes from MP3 show content ending below the coded bandwidth. | — |

### F3.03 Declared vs measured bandwidth

High declared bandwidth with low real content means a re-encode.

- **Input:** Derived
- **Metric:** Δ Hz
- **Effort:** Low–Medium
- **False-verdict risk:** Medium. Content may lack high frequencies naturally.
- **Priority:** v1
- **Reference:** mp3guessenc + Spek
- **Method:** Compare F1.07 declared bandwidth with F3.01 measured cutoff.
- **Thresholds:** Pass: Δ < 1 kHz; Warn: 1–2.5 kHz; Fail: > 2.5 kHz below declared

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | LAME lowpass vs measured cutoff. | — |
| Ogg Vorbis | Not applicable | No declared bandwidth stored. |  |
| FLAC | Not applicable | Not applicable. |  |
| Opus | Applies | TOC bandwidth (e.g. FB) vs measured cutoff. | — |

### F3.04 Cutoff sharpness

Lossy encoders leave a brick wall; natural roll-off is gradual.

- **Input:** Decoded PCM
- **Metric:** dB per kHz
- **Effort:** Medium
- **False-verdict risk:** Medium. Mastering filters can also be steep.
- **Priority:** v1
- **Reference:** fakeflac-go (drop test)
- **Method:** Slope of the averaged spectrum across the cutoff region.
- **Thresholds:** Info: > 30 dB/kHz: lossy origin likely; Info: < 10 dB/kHz: natural roll-off

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | — | — |
| Ogg Vorbis | Applies | — | — |
| FLAC | Applies | Key evidence for fake-lossless detection. | — |
| Opus | Applies | — | — |

### F3.05 High-band holes & flicker

Lossy coding leaves gaps and on/off energy in upper bands over time.

- **Input:** Decoded PCM
- **Metric:** % empty bins, variance
- **Effort:** Medium
- **False-verdict risk:** High. Sparse content (solo voice, quiet passages) mimics holes.
- **Priority:** Later
- **Reference:** FLAC Detective (rules)
- **Method:** Spectrogram of 16–20 kHz; measure temporal variance and fraction of empty bins.
- **Thresholds:** Pass: Continuous HF energy; Warn: Intermittent HF; Fail: Persistent holes

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | — | — |
| Ogg Vorbis | Applies | Vorbis noise normalisation partly fills holes; tune separately. | — |
| FLAC | Applies | Evidence of lossy origin in a lossless file. | — |
| Opus | Adapted | Opus fills bands with folded noise, so holes are less visible. | — |

### F3.06 Upsampling detection

Content resampled from a lower-rate source.

- **Input:** Decoded PCM
- **Metric:** Likely source rate
- **Effort:** Low–Medium
- **False-verdict risk:** Medium. Overlaps with low-bitrate cutoff; report together.
- **Priority:** v1
- **Reference:** Spek
- **Method:** Look for empty spectrum above 11 / 16 kHz with a near-zero noise floor.
- **Thresholds:** Pass: Not detected; Fail: Detected

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | — | — |
| Ogg Vorbis | Applies | — | — |
| FLAC | Applies | Also check 88.2/96 kHz files whose content stops near 22 kHz (upsampled CD). | — |
| Opus | Adapted | Always resampled to 48 kHz internally; compare with header input rate. | — |

### F3.07 Double-compression markers

Direct evidence of re-encoding from coefficient statistics.

- **Input:** Decoded PCM
- **Metric:** Probability
- **Effort:** High
- **False-verdict risk:** High. Research-grade; needs training data.
- **Priority:** Later
- **Reference:** Academic papers
- **Method:** Histogram analysis of quantized transform coefficients (research methods).
- **Thresholds:** Info: Experimental

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | MDCT coefficient histograms; most published research targets MP3. | — |
| Ogg Vorbis | Adapted | Little prior research. | — |
| FLAC | Adapted | Detect which lossy codec the source came from. | — |
| Opus | Adapted | Little prior research. | — |

### F3.08 Spectrogram view

Visual evidence users trust and can verify.

- **Input:** Decoded PCM
- **Metric:** Image
- **Effort:** Low–Medium
- **False-verdict risk:** Low. None; it shows what the verdict is based on.
- **Priority:** MVP
- **Reference:** Spek, SoX
- **Method:** STFT image, log frequency axis, measured cutoff line overlaid.
- **Thresholds:** Info: Evidence, not a metric

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | — | — |
| Ogg Vorbis | Applies | — | — |
| FLAC | Applies | — | — |
| Opus | Applies | — | — |

### F3.09 Overall confidence score

Superseded: REQ-FAC-03 makes this the O3 objective score.

- **Input:** Derived
- **Metric:** 0–100 + reasons
- **Effort:** Medium
- **False-verdict risk:** High. Weights must be tuned on a labelled corpus per codec.
- **Priority:** v1
- **Reference:** FLAC Detective (scoring design)
- **Method:** Weighted rules over F1–F3 results, with protection rules for natural low-bandwidth sources.
- **Thresholds:** Pass: ≥ 80 authentic; Warn: 50–79 suspicious; Fail: < 50 likely transcode

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Applies | MP3 weight set. | — |
| Ogg Vorbis | Applies | Vorbis weight set. | — |
| FLAC | Applies | Lossless weight set: lossy-origin and fake bit depth dominate. | — |
| Opus | Applies | Opus weight set. | — |

### F3.10 Fake bit depth

24-bit files that only contain 16-bit audio.

- **Input:** Decoded PCM
- **Metric:** Effective bits
- **Effort:** Low–Medium
- **False-verdict risk:** Low. Very quiet passages may use few bits legitimately; sample loud sections.
- **Priority:** v1
- **Reference:** FLAC Detective
- **Method:** Check whether the low-order bits are always zero or carry only dither noise.
- **Thresholds:** Pass: Effective = declared; Fail: 24-bit with ≤ 16 effective bits

| Format | Applies | Format-specific method | Format-specific thresholds |
| --- | --- | --- | --- |
| MP3 | Not applicable | Lossy; not applicable. |  |
| Ogg Vorbis | Not applicable | Lossy; not applicable. |  |
| FLAC | Applies | — | — |
| Opus | Not applicable | Lossy; not applicable. |  |
