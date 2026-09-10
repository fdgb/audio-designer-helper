#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Index a local SFX library into index.db — fully offline, no ML model.
Produces three tables used by hybrid_search.py:
  files   : path, library, subcat, name, ext, size, kind, audio
  specs   : path, sr, bits, ch, dur
  bandprof: path, prof(8-band csv), centroid, flux
Requires: ffmpeg + ffprobe on PATH, and numpy.
Usage:
  python scripts/build_index.py /path/to/your/SFX --db index.db
"""
import os, sys, json, sqlite3, subprocess, argparse
import numpy as np

BANDS = [20, 60, 250, 500, 1000, 2000, 4000, 8000, 20000]  # 9 edges -> 8 bands
AUDIO_EXT = {".wav", ".flac", ".aif", ".aiff", ".mp3", ".ogg", ".m4a", ".wma", ".aac", ".mp2"}


def ffprobe_specs(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", path],
            capture_output=True, text=True, timeout=60)
        d = json.loads(out.stdout)
        stream = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
        if not stream:
            return None
        sr = int(stream.get("sample_rate", 0) or 0)
        bits = int(stream.get("bits_per_raw_sample") or stream.get("sample_bit_depth") or 0)
        ch = int(stream.get("channels", 0) or 0)
        dur = float(d.get("format", {}).get("duration", 0) or 0)
        return (sr, bits, ch, dur)
    except Exception:
        return None


def band_profile(path, sr=44100):
    """Return (8-band normalized profile, centroid Hz, flux) or None."""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
         "-af", f"aresample={sr}", "-f", "f32le", "-ac", "1", "-"],
        capture_output=True)
    x = np.frombuffer(p.stdout, dtype=np.float32)
    if x.size == 0:
        return None
    x = x.astype(np.float64)
    x -= x.mean()
    N, H = 2048, 1024
    win = np.hanning(N)
    nb = len(BANDS) - 1
    acc = np.zeros(nb)
    cents, flux, nframes = [], 0.0, 0
    freqs = np.fft.rfftfreq(N, 1 / sr)
    idx = np.digitize(freqs, BANDS) - 1
    prev = None
    for s in range(0, len(x) - N, H):
        seg = x[s:s + N] * win
        mag = np.abs(np.fft.rfft(seg)) ** 2
        for b in range(nb):
            acc[b] += mag[idx == b].sum()
        tot = mag.sum()
        if tot > 0:
            cents.append((freqs * mag).sum() / tot)
        if prev is not None:
            flux += np.sqrt(((np.sqrt(mag) - np.sqrt(prev)) ** 2).sum())
        prev = mag
        nframes += 1
    if nframes == 0:
        return None
    return acc / acc.sum(), float(np.mean(cents)), flux / nframes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="SFX library root directory")
    ap.add_argument("--db", default="index.db", help="output sqlite path")
    ap.add_argument("--sr", type=int, default=44100)
    ap.add_argument("--limit", type=int, default=0, help="0 = all files")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS files(
      id INTEGER PRIMARY KEY, path TEXT UNIQUE, library TEXT, subcat TEXT,
      name TEXT, ext TEXT, size INTEGER, kind TEXT, audio INTEGER);
    CREATE TABLE IF NOT EXISTS specs(
      path TEXT PRIMARY KEY, sr INTEGER, bits INTEGER, ch INTEGER, dur REAL);
    CREATE TABLE IF NOT EXISTS bandprof(
      path TEXT PRIMARY KEY, prof TEXT, centroid REAL, flux REAL);
    """)
    con.commit()

    files = []
    for dirpath, _, names in os.walk(args.root):
        for n in names:
            if os.path.splitext(n)[1].lower() in AUDIO_EXT:
                files.append(os.path.join(dirpath, n))
    if args.limit:
        files = files[:args.limit]
    print(f"found {len(files)} audio files")

    done = 0
    for path in files:
        try:
            specs = ffprobe_specs(path)
            prof = band_profile(path, args.sr)
        except Exception as e:
            print("skip", path, e); continue
        if prof is None:
            continue
        prof_arr, cent, flux = prof
        library = os.path.basename(os.path.dirname(path))
        subcat = os.path.basename(os.path.dirname(os.path.dirname(path)))
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower().lstrip(".")
        size = os.path.getsize(path)
        cur.execute("INSERT OR REPLACE INTO files(path,library,subcat,name,ext,size,kind,audio)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (path, library, subcat, name, ext, size, "audio", 1))
        if specs:
            cur.execute("INSERT OR REPLACE INTO specs(path,sr,bits,ch,dur) VALUES(?,?,?,?,?)",
                        (path, *specs))
        cur.execute("INSERT OR REPLACE INTO bandprof(path,prof,centroid,flux) VALUES(?,?,?,?)",
                    (path, ",".join(f"{v:.6f}" for v in prof_arr), cent, flux))
        done += 1
        if done % 500 == 0:
            con.commit(); print(f"  indexed {done}/{len(files)}")
    con.commit()
    cur.execute("SELECT COUNT(*) FROM bandprof")
    print(f"done. bandprof rows = {cur.fetchone()[0]}")
    con.close()


if __name__ == "__main__":
    main()
