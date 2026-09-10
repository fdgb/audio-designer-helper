#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a pink-noise reference file at a target integrated LUFS.
Used as the single objective anchor for bus-level balancing (game audio
mixing): calibrate monitors once, then set every bus fader against it.

Usage:
  python scripts/gen_pinknoise.py --out pinknoise_-23LUFS.wav --lufs -23 --sr 48000 --dur 20
Requires: numpy, soundfile, pyloudnorm.
"""
import argparse
import numpy as np
import soundfile as sf
import pyloudnorm as pyln


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="pinknoise_-23LUFS.wav")
    ap.add_argument("--lufs", type=float, default=-23.0)
    ap.add_argument("--sr", type=int, default=48000)
    ap.add_argument("--dur", type=float, default=20.0)
    args = ap.parse_args()

    N = int(args.sr * args.dur)
    np.random.seed(42)
    white = np.random.randn(N)
    pink = np.zeros(N)
    b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
    for i in range(N):
        w = white[i]
        b0 = 0.99886 * b0 + w * 0.0555179
        b1 = 0.99332 * b1 + w * 0.0750759
        b2 = 0.96900 * b2 + w * 0.1538520
        b3 = 0.86650 * b3 + w * 0.3104856
        b4 = 0.55000 * b4 + w * 0.5329522
        b5 = -0.7616 * b5 - w * 0.0168980
        pink[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362
        b6 = w * 0.115926
    pink = pink - np.mean(pink)
    meter = pyln.Meter(args.sr)
    before = meter.integrated_loudness(pink)
    pink = pink * (10.0 ** ((args.lufs - before) / 20.0))
    after = meter.integrated_loudness(pink)
    stereo = np.column_stack([pink, pink]).astype(np.float32)
    sf.write(args.out, stereo, args.sr, subtype="PCM_24")
    print(f"before LUFS={before:.2f}  after LUFS={after:.2f}  target={args.lufs}")
    print(f"wrote {args.out}  ({args.dur}s, {args.sr}Hz, 24-bit stereo)")


if __name__ == "__main__":
    main()
