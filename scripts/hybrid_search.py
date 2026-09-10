#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hybrid SFX retrieval: named-concept search = metadata keyword filter
   (files.name/subcat/library) + bandprof acoustic gate (broadband, low-mid
   body, centroid window, duration cap). Far more reliable than pure spectral
   similarity for NAMED concepts (door / impact / hit / thunder ...).
Usage:
  python scripts/hybrid_search.py --db index.db --keywords door,impact,slam,hit \
      --out candidates.txt --top 40
Acoustic gate (override with flags):
  --cent-lo 250 --cent-hi 4000 --min-body 0.12 --min-bands 3 --max-top-band 0.60 --max-dur 2.5
"""
import sqlite3, os, re, argparse


def search(db, keywords, top=40, cent_lo=250, cent_hi=4000, min_body=0.12,
           min_bands=3, max_top_band=0.60, max_dur=2.5, size_cap=2_500_000):
    con = sqlite3.connect(db); cur = con.cursor()
    kws = [k.strip() for k in keywords if k.strip()]
    conds = " OR ".join(
        [f"(name LIKE '%{k}%' OR subcat LIKE '%{k}%' OR library LIKE '%{k}%')" for k in kws])
    cur.execute(f"SELECT path, name, subcat, library, size FROM files WHERE {conds}")
    filerows = cur.fetchall()

    # word-boundary refine on the filename for the more specific keywords.
    # Treat underscore/digit as a boundary too (real SFX names use Foo_Bar.wav),
    # so we use a custom boundary that excludes '_' from the word class.
    specific = [k for k in kws if len(k) >= 3]
    refine = re.compile(r'(?<![a-z0-9])(?:' + '|'.join(re.escape(k) for k in specific) + r')(?![a-z0-9])', re.I)
    kept = [(p, n, s, l, sz) for (p, n, s, l, sz) in filerows if refine.search(n.lower())]

    cur.execute("SELECT path, prof, centroid, flux FROM bandprof")
    prof_map = {p: (prof, cent, flux) for p, prof, cent, flux in cur.fetchall()}
    cur.execute("SELECT path, dur FROM specs")
    dur_map = {p: d for p, d in cur.fetchall()}
    con.close()

    def gate(path, size):
        if path not in prof_map:
            return False
        try:
            b = [float(x) for x in prof_map[path][0].split(",")]
        except Exception:
            return False
        if len(b) != 8:
            return False
        if max(b) > max_top_band:
            return False  # narrowband (bird / pure tone)
        if sum(1 for x in b if x > 0.12) < min_bands:
            return False  # too narrow
        if b[2] + b[3] < min_body:
            return False  # no low-mid body
        c = prof_map[path][1]
        if c is None or c < cent_lo or c > cent_hi:
            return False
        d = dur_map.get(path)
        if d is not None and d > max_dur:
            return False
        if d is None and size and size > size_cap:
            return False
        return True

    passed = []
    for path, name, subcat, library, size in kept:
        if gate(path, size):
            b = [float(x) for x in prof_map[path][0].split(",")]
            score = (b[2] + b[3] + b[4]) - 0.3 * (b[0] + b[1]) - 0.2 * b[7]
            passed.append((path, score))
    passed.sort(key=lambda x: -x[1])
    return passed[:top]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--keywords", required=True, help="comma-separated, e.g. door,impact,slam")
    ap.add_argument("--out", default="candidates.txt")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--cent-lo", type=float, default=250)
    ap.add_argument("--cent-hi", type=float, default=4000)
    ap.add_argument("--min-body", type=float, default=0.12)
    ap.add_argument("--min-bands", type=int, default=3)
    ap.add_argument("--max-top-band", type=float, default=0.60)
    ap.add_argument("--max-dur", type=float, default=2.5)
    args = ap.parse_args()

    res = search(args.db, args.keywords.split(","), args.top,
                 args.cent_lo, args.cent_hi, args.min_body, args.min_bands,
                 args.max_top_band, args.max_dur)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(f"hybrid search: {args.keywords}\n")
        for i, (path, score) in enumerate(res):
            f.write(f"{i:02d}  score={score:.3f}  {path}\n")
    print(f"wrote {len(res)} candidates -> {args.out}")


if __name__ == "__main__":
    main()
