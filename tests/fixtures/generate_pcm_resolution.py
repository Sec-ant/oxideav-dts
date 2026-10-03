#!/usr/bin/env python3
"""Generate original DTS Core signals and opaque FFmpeg S32 level oracles.

PCMR describes the precision of the source before lossy encoding (ETSI TS
102 114, Table 5-17). These fixtures vary that metadata while retaining the
same Core audio payload; the independently decoded signal must keep its level.
No external or user media is used. Run with FFmpeg's dca encoder available.
"""
from pathlib import Path
import math
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def ffmpeg(*args):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", *map(str, args)],
        check=True, stdout=subprocess.PIPE,
    ).stdout


def precision_variant(data, code):
    data = bytearray(data)
    at = 0
    while at < len(data):
        assert data[at:at + 4] == bytes.fromhex("7ffe8001")
        assert not data[at + 4] & 2, "generator expects CPF=0"
        size = ((int.from_bytes(data[at:at + 8], "big") >> 4) & 0x3fff) + 1
        # 88 bits precede the post-CRC window. PCMR occupies its bits 8..6.
        word = int.from_bytes(data[at + 11:at + 13], "big")
        word = (word & ~0x1c0) | (code << 6)
        data[at + 11:at + 13] = word.to_bytes(2, "big")
        at += size
    assert at == len(data)
    return data


with tempfile.TemporaryDirectory(prefix="dts-pcmr-") as temporary:
    directory = Path(temporary)
    for name, channels in [("stereo", 2), ("51", 6)]:
        pcm = directory / "source.f32"
        samples = []
        for i in range(4096):
            t = i / 48000
            for ch in range(channels):
                frequency = 60 if channels == 6 and ch == 3 else 431 + 223 * ch
                value = .22 * math.sin(2 * math.pi * frequency * t + .19 * ch)
                if not (channels == 6 and ch == 3):
                    value += .08 * math.sin(2 * math.pi * (2791 + 191 * ch) * t)
                samples.append(value)
        pcm.write_bytes(struct.pack("<" + "f" * len(samples), *samples))
        base = ffmpeg("-f", "f32le", "-ar", 48000, "-ac", channels,
                      "-i", pcm, "-c:a", "dca", "-strict", -2,
                      "-b:a", "768k", "-f", "dts", "pipe:1")
        reference = None
        for bits, code in [(16, 0), (20, 2), (24, 6)]:
            path = ROOT / f"pcmr_{name}_{bits}.dts"
            path.write_bytes(precision_variant(base, code))
            decoded = ffmpeg("-cpuflags", 0, "-core_only", 1, "-f", "dts",
                             "-i", path, "-c:a", "pcm_s32le", "-f", "s32le", "pipe:1")
            assert len(decoded) == 4096 * channels * 4
            if reference is None:
                reference = decoded
            assert decoded == reference, f"{name} PCMR {bits} changed reference level"
        (ROOT / f"pcmr_{name}_ffmpeg.s32").write_bytes(reference)
