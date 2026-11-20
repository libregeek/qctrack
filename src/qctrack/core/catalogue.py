"""Names and plain-language explanations of the factors and objectives, for reports
(REQ-HTM-04). The wording follows the quality evaluation matrix (spec/); keep them in step."""

from __future__ import annotations

OBJECTIVE_NAMES = {
    "O1": "Metadata & encoding",
    "O2": "Technical integrity",
    "O3": "Authenticity & transcode",
}

#: factor ID -> (name, why it matters)
FACTORS: dict[str, tuple[str, str]] = {
    "F1.01": (
        "Nominal & average bitrate",
        "Headline quality indicator users expect.",
    ),
    "F1.02": (
        "Bitrate mode",
        "CBR / VBR / ABR changes how bitrate should be judged.",
    ),
    "F1.03": (
        "Codec version & sample rate",
        "Low sample rates cap available bandwidth.",
    ),
    "F1.04": (
        "Channel layout",
        "Mono music, or stereo at very low bitrates, degrades quality.",
    ),
    "F1.05": (
        "Encoder identity & version",
        "Encoder quality varies between implementations and versions.",
    ),
    "F1.06": (
        "Encoder quality setting",
        "Reveals the intended quality level (V0, -q 6, etc.).",
    ),
    "F1.07": (
        "Declared bandwidth / lowpass",
        "Encoder's own bandwidth setting; compared with measured cutoff in F3.03.",
    ),
    "F1.08": (
        "Declared vs counted length",
        "Mismatch suggests editing, joining or a bad header.",
    ),
    "F1.09": (
        "Tags & stored gain",
        "Tag health and existing loudness normalisation values.",
    ),
    "F1.10": (
        "Real format detection",
        "Files are often renamed; .ogg can hold Vorbis, Opus, FLAC or Speex.",
    ),
    "F1.11": (
        "Bit depth",
        "Lossless resolution claim (16 / 24-bit).",
    ),
    "F2.01": (
        "Stream structure validity",
        "Core structural integrity of the container and frames.",
    ),
    "F2.02": (
        "Truncation",
        "Incomplete downloads or cut files.",
    ),
    "F2.03": (
        "Unidentified data",
        "Garbage between frames or appended junk.",
    ),
    "F2.04": (
        "Embedded audio checksum",
        "Proves audio is unchanged since encoding.",
    ),
    "F2.05": (
        "Block-level CRC",
        "Bit errors inside individual frames or pages.",
    ),
    "F2.06": (
        "Decode errors",
        "Damage that structure checks alone don't show.",
    ),
    "F2.07": (
        "Clipping",
        "Audible distortion from overloaded mastering or encoding.",
    ),
    "F2.08": (
        "Peak & true peak",
        "Headroom and inter-sample overs.",
    ),
    "F2.09": (
        "Integrated loudness",
        "Over-compression and level consistency.",
    ),
    "F2.10": (
        "Dynamic range",
        "Squashed dynamics (loudness war).",
    ),
    "F2.11": (
        "DC offset",
        "Faulty source or processing.",
    ),
    "F2.12": (
        "Channel anomalies",
        "Dead channel, fake stereo, inverted polarity.",
    ),
    "F2.13": (
        "Silence & dropouts",
        "Gaps, excessive padding, cut-outs mid-track.",
    ),
    "F2.14": (
        "Ogg stream layout",
        "Chained or multiplexed streams and granule gaps break players and length reporting.",
    ),
    "F3.01": (
        "Measured spectral cutoff",
        "Real audio bandwidth, the main evidence of source quality.",
    ),
    "F3.02": (
        "Claimed vs effective quality",
        "Core fake-bitrate / fake-lossless verdict.",
    ),
    "F3.03": (
        "Declared vs measured bandwidth",
        "High declared bandwidth with low real content means a re-encode.",
    ),
    "F3.04": (
        "Cutoff sharpness",
        "Lossy encoders leave a brick wall; natural roll-off is gradual.",
    ),
    "F3.05": (
        "High-band holes & flicker",
        "Lossy coding leaves gaps and on/off energy in upper bands over time.",
    ),
    "F3.06": (
        "Upsampling detection",
        "Content resampled from a lower-rate source. Reported only for MP3 and Vorbis, where "
        "an encoder's own lowpass looks the same.",
    ),
    "F3.07": (
        "Double-compression markers",
        "Direct evidence of re-encoding from coefficient statistics.",
    ),
    "F3.08": (
        "Spectrogram view",
        "Visual evidence users trust and can verify.",
    ),
    "F3.10": (
        "Fake bit depth",
        "24-bit files that only contain 16-bit audio.",
    ),
}
