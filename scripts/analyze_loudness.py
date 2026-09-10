#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measure integrated LUFS, true peak (dBTP), RMS and spectral centroid of an
audio file. Companion to gen_pinknoise.py: verify a mix sits at the calibrated
anchor and inside delivery ceilings (e.g. true peak <= -1.0 dBTP).
Usage:
  python scripts/analyze_loudness.py path/to/mix.wav
Requires: numpy, soundfile, pyloudnorm.
"""
import sys, argparse
import numpy as np
import soundfile as sf
import pyloudnorm as pyln


def true_peak_dbfs(x_mono):
    n = len(x_mono)
    t = np.arange(n)
    up = np.interp(np.arange(n * 4) / 4.0, t, x_mono)
    return 20 * np.log10(np.max(np.abs(up)) + 1e-12)


def spectral_centroid(x_mono, sr):
    N = 2048
    win = np.hanning(N)
    cents = []
    freqs = np.fft.rfftfreq(N, 1 / sr)
    for s in range(0, len(x_mono) - N, N // 2):
        seg = x_mono[s:s + N] * win
        mag = np.abs(np.fft.rfft(seg)) ** 2
        tot = mag.sum()
        if tot > 0:
            cents.append((freqs * mag).sum() / tot)
    return float(np.mean(cents)) if cents else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--sr", type=int, default=48000)
    args = ap.parse_args()

    data, sr = sf.read(args.path, dtype="float32", always_2d=True)
    if sr != args.sr:
        try:
            import resampy
            data = resampy.resample(data.T, sr, args.sr).T
            sr = args.sr
        except Exception:
            pass
    mono = data.mean(axis=1) if data.shape[1] == 2 else data[:, 0]
    meter = pyln.Meter(sr)
    lu = meter.integrated_loudness(mono)
    tp = true_peak_dbfs(mono)
    peak = 20 * np.log10(np.max(np.abs(mono)) + 1e-12)
    rms = 20 * np.log10(np.sqrt(np.mean(mono ** 2)) + 1e-12)
    cent = spectral_centroid(mono, sr)
    print(f"file        : {args.path}")
    print(f"LUFS        : {lu:.2f}")
    print(f"true peak   : {tp:.2f} dBTP  {'OK (<= -1.0)' if tp <= -1.0 else 'WARN > -1.0'}")
    print(f"sample peak : {peak:.2f} dBFS")
    print(f"RMS         : {rms:.2f} dB")
    print(f"centroid    : {cent:.0f} Hz")


if __name__ == "__main__":
    main()
