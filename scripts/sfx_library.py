#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sfx_library.py — 音效库管理模块 (audio-designer-helper skill)

设计目标: 对任意规模的音效库做一次"通篇扫描"建索引, 之后用 SQLite 做
快速检索 / 格式审计 / 重复文件发现 / 规格画像, 而不必每次都遍历整个硬盘。

子命令:
  scan   建/重建索引 (os.walk 单遍, 写 SQLite)
  search  按关键词/库/格式/大小 检索文件路径
  report  库健康报告 (总量/格式/各库计数/垃圾文件/非音频混杂)
  dupes   找重复文件 (size 快速分组 / hash 精确比对)
  specs   抽样或全量 ffprobe 音频规格 (采样率/位深/声道/时长/LUFS)

DB 默认存到 ~/.workbuddy/sfx_library/index.db, 绝不写入音效库目录本身。
所有操作为只读 (除生成 DB 文件外), 不会修改/删除你的音效文件。
"""
import argparse
import hashlib
import json
import multiprocessing
import os
import re
import random
import shutil
import sqlite3
import struct
import subprocess
import sys
import zipfile

try:
    import numpy as np
except ImportError:
    np = None

AUDIO_EXTS = {
    "wav", "aif", "aiff", "mp3", "flac", "ogg", "oga", "opus",
    "ape", "m4a", "aac", "wv", "mp2", "caf", "raw", "au",
}
JUNK_NAMES = (".ds_store",)

DEFAULT_DB = os.path.expanduser("~/.workbuddy/sfx_library/index.db")


# ----------------------------------------------------------------------------
# 索引构建
# ----------------------------------------------------------------------------
def _classify(name):
    low = name.lower()
    if low.startswith("._") or low in JUNK_NAMES or low.startswith("."):
        return "junk"
    ext = low.rsplit(".", 1)[-1] if "." in low else ""
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


def build_index(root, db):
    root = os.path.abspath(root)
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")   # 读写不互锁, 支持 scan/specs 与 dupes 并发
    con.execute("DROP TABLE IF EXISTS files")
    con.execute(
        """CREATE TABLE files(
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE,
            library TEXT,
            subcat TEXT,
            name TEXT,
            ext TEXT,
            size INTEGER,
            mtime REAL,
            kind TEXT,
            audio INTEGER
        )"""
    )
    con.execute("CREATE INDEX ix_lib ON files(library)")
    con.execute("CREATE INDEX ix_name ON files(name)")

    rows = []
    total = 0
    n_audio = n_junk = n_other = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # 跳过库内 .workbuddy 自建目录, 避免把索引工具自身卷进去
        dirnames[:] = [d for d in dirnames if d != ".workbuddy"]
        rel = os.path.relpath(dirpath, root)
        parts = [] if rel == "." else rel.split(os.sep)
        library = parts[0] if len(parts) >= 1 else "(root)"
        subcat = parts[1] if len(parts) >= 2 else ""
        for fn in filenames:
            try:
                st = os.stat(os.path.join(dirpath, fn))
            except OSError:
                continue
            ext = fn.lower().rsplit(".", 1)[-1] if "." in fn else ""
            kind = _classify(fn)
            audio = 1 if kind == "audio" else 0
            rows.append((os.path.join(dirpath, fn), library, subcat, fn,
                         ext, int(st.st_size), float(st.st_mtime), kind, audio))
            total += 1
            if kind == "audio":
                n_audio += 1
            elif kind == "junk":
                n_junk += 1
            else:
                n_other += 1
            if len(rows) >= 20000:
                con.executemany(
                    "INSERT OR IGNORE INTO files(path,library,subcat,name,ext,size,mtime,kind,audio) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", rows)
                rows.clear()
    if rows:
        con.executemany(
            "INSERT OR IGNORE INTO files(path,library,subcat,name,ext,size,mtime,kind,audio) "
            "VALUES(?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    print(f"索引完成: 共 {total} 文件 (音频 {n_audio}, 垃圾 {n_junk}, 其他 {n_other})")
    print(f"DB: {db}")
    con.close()


# ----------------------------------------------------------------------------
# 检索
# ----------------------------------------------------------------------------
def search(db, kw=None, library=None, ext=None, audio_only=False,
           min_size=None, max_size=None, limit=50, include_junk=False):
    con = sqlite3.connect(db, timeout=30)
    wheres, params = [], []
    if not include_junk:
        wheres.append("kind<>'junk'")
    if kw:
        wheres.append("name LIKE ?")
        params.append(f"%{kw}%")
    if library:
        wheres.append("library = ?")
        params.append(library)
    if ext:
        wheres.append("ext = ?")
        params.append(ext.lower())
    if audio_only:
        wheres.append("audio = 1")
    if min_size is not None:
        wheres.append("size >= ?")
        params.append(min_size)
    if max_size is not None:
        wheres.append("size <= ?")
        params.append(max_size)
    sql = "SELECT path FROM files"
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    sql += f" ORDER BY path LIMIT {int(limit)}"
    cur = con.execute(sql, params)
    out = cur.fetchall()
    con.close()
    if not out:
        print("(无匹配)")
        return
    for (p,) in out:
        print(p)
    print(f"\n--- 命中 {len(out)} 条 (上限 {limit}) ---")


# ----------------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------------
def report(db):
    con = sqlite3.connect(db, timeout=30)
    total = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    n_audio = con.execute("SELECT COUNT(*) FROM files WHERE audio=1").fetchone()[0]
    n_junk = con.execute("SELECT COUNT(*) FROM files WHERE kind='junk'").fetchone()[0]
    n_other = total - n_audio - n_junk
    print("========== 音效库健康报告 ==========")
    print(f"总文件数      : {total}")
    print(f"  音频文件    : {n_audio} ({100*n_audio/total:.1f}%)")
    print(f"  非音频      : {n_other}")
    print(f"  垃圾文件    : {n_junk} (._* / .DS_Store)")
    print("\n--- 各顶层库文件数 (Top 20) ---")
    for lib, c in con.execute(
        "SELECT library, COUNT(*) c FROM files GROUP BY library ORDER BY c DESC LIMIT 20"):
        print(f"  {c:>8}  {lib}")
    print("\n--- 格式分布 (Top 15, 不含垃圾文件) ---")
    for ext, c in con.execute(
        "SELECT COALESCE(ext,'(none)') e, COUNT(*) c FROM files "
        "WHERE kind<>'junk' GROUP BY e ORDER BY c DESC LIMIT 15"):
        print(f"  {c:>8}  .{ext}")
    # 非音频混在音频库顶层目录下的提示
    print("\n--- 非音频文件Top目录 (可能混杂视频/文档) ---")
    for lib, c in con.execute(
        "SELECT library, COUNT(*) c FROM files WHERE audio=0 AND kind<>'junk' "
        "GROUP BY library ORDER BY c DESC LIMIT 10"):
        print(f"  {c:>8}  {lib}")
    con.close()


# ----------------------------------------------------------------------------
# 重复文件
# ----------------------------------------------------------------------------
def _paths_for_size(con, size):
    """取某 size 碰撞组内的真实路径列表(避免 GROUP_CONCAT+split(',') 在含逗号路径上出错)。"""
    return [r[0] for r in con.execute(
        "SELECT path FROM files WHERE kind<>'junk' AND size=? ORDER BY path", (size,))]


def dupes(db, mode="size", limit=50):
    con = sqlite3.connect(db, timeout=30)
    print(f"========== 重复文件扫描 (mode={mode}) ==========")
    if mode == "size":
        groups = con.execute(
            "SELECT size, COUNT(*) c FROM files "
            "WHERE kind<>'junk' GROUP BY size HAVING c > 1 ORDER BY c DESC, size DESC").fetchall()
        shown = 0
        for size, c in groups:
            paths = _paths_for_size(con, size)
            print(f"\n[{c} 个, 各 {size} 字节]")
            for p in paths[:5]:
                print(f"   {p}")
            if len(paths) > 5:
                print(f"   ... 其余 {len(paths)-5} 个")
            shown += 1
            if shown >= limit:
                break
        print(f"\n--- 共 {len(groups)} 个size碰撞组 (显示前 {limit}) ---")
    else:  # hash
        # 先按 size 分组, 仅对 size 碰撞组做哈希, 控制成本
        size_groups = con.execute(
            "SELECT size, COUNT(*) c FROM files WHERE kind<>'junk' "
            "GROUP BY size HAVING COUNT(*)>1 ORDER BY c DESC").fetchall()
        exact = 0
        shown = 0
        for size, c in size_groups:
            paths = _paths_for_size(con, size)
            if len(paths) < 2:
                continue
            by_hash = {}
            for p in paths:
                h = _file_hash(p)
                by_hash.setdefault(h, []).append(p)
            for h, members in by_hash.items():
                if len(members) > 1:
                    exact += 1
                    if shown < limit:
                        print(f"\n[精确重复 {len(members)} 个, 各 {size} 字节, md5={h[:10]}]")
                        for m in members[:5]:
                            print(f"   {m}")
                        shown += 1
        print(f"\n--- 精确重复组: {exact} (显示前 {limit}) ---")
    con.close()


def _file_hash(path, blk=1 << 20):
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(blk), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


# ----------------------------------------------------------------------------
# 规格画像 (ffprobe)
# ----------------------------------------------------------------------------
def _extended80_to_double(b):
    """AIFF 采样率存在 80-bit IEEE extended float 里, 转 double。"""
    if len(b) < 10:
        return 0.0
    sign = (b[0] >> 7) & 1
    exp = ((b[0] & 0x7F) << 8) | b[1]
    mant = int.from_bytes(b[2:10], "big")
    if exp == 0:
        return 0.0
    mant |= 1 << 63
    val = mant / float(1 << 63)
    val *= 2.0 ** (exp - 16383)
    return -val if sign else val


def _parse_wave(f):
    """WAV(PCM) 头 -> (sr,bits,ch,dur)。非 PCM(压缩 wav) 返回 None 交给 ffprobe。"""
    sr = ch = bits = 0
    byte_rate = 0
    fmt_found = False
    data_size = None
    while True:
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        cid = hdr[:4]
        size = struct.unpack("<I", hdr[4:8])[0]
        if cid == b"fmt ":
            body = f.read(size)
            if len(body) < 16:
                return None
            audio_fmt = struct.unpack("<H", body[0:2])[0]
            ch = struct.unpack("<H", body[2:4])[0]
            sr = struct.unpack("<I", body[4:8])[0]
            byte_rate = struct.unpack("<I", body[8:12])[0]
            bits = struct.unpack("<H", body[14:16])[0]
            if audio_fmt != 1:      # 压缩 wav(如 WAV/mp3) -> 让 ffprobe 处理
                return None
            fmt_found = True
        elif cid == b"data":
            data_size = size
            break                  # fmt 必在 data 前, 已拿到所需
        else:
            f.seek(size + (size & 1), 1)   # 跳过整个 chunk 体(含奇数字节补位), 相对当前位置
    if not fmt_found:
        return None
    dur = data_size / byte_rate if (data_size is not None and byte_rate > 0) else 0.0
    return (sr, bits, ch, dur)


def _parse_aiff(f, form):
    """AIFF / AIFC(PCM) 头 -> (sr,bits,ch,dur)。压缩 AIFC 返回 None 交给 ffprobe。"""
    ch = bits = 0
    frames = 0
    sr_f = 0.0
    comm_found = False
    while True:
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        cid = hdr[:4]
        size = struct.unpack(">I", hdr[4:8])[0]
        if cid == b"COMM":
            body = f.read(size)
            if len(body) < 18:
                return None
            ch = struct.unpack(">H", body[0:2])[0]
            frames = struct.unpack(">I", body[2:6])[0]
            bits = struct.unpack(">H", body[6:8])[0]
            sr_f = _extended80_to_double(body[8:18])
            if form == b"AIFC" and len(body) >= 22:
                codec = body[18:22]
                if codec not in (b"NONE", b"twos", b"sowt"):
                    return None      # 压缩 AIFC -> 让 ffprobe 处理
            comm_found = True
            break
        else:
            f.seek(size + (size & 1), 1)   # 跳过整个 chunk 体(含奇数字节补位), 相对当前位置
    if not comm_found:
        return None
    dur = frames / sr_f if sr_f > 0 else 0.0
    return (int(round(sr_f)), bits, ch, dur)


def _probe_header(path):
    """极速解析 wav/aiff 头(无需 ffprobe 子进程)。不支持/压缩格式返回 None。
    WAV=RIFF 容器(小端); AIFF/AIFC=FORM 容器(大端)。"""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12:
                return None
            container, form = head[:4], head[8:12]
            if container == b"RIFF" and form == b"WAVE":
                return _parse_wave(f)
            if container == b"FORM" and form in (b"AIFF", b"AIFC"):
                return _parse_aiff(f, form)
    except OSError:
        return None
    return None


def _ffprobe(path):
    """ffprobe 兜底(压缩/非常规格式)。返回 dict 或 None。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=sample_rate,bits_per_sample,channels:format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    import json
    try:
        d = json.loads(out)
    except Exception:
        return None
    st = next((s for s in d.get("streams", []) if s.get("sample_rate")), {})
    dur = d.get("format", {}).get("duration")
    return {
        "sr": int(st.get("sample_rate", 0)) if st.get("sample_rate") else 0,
        "bits": int(st.get("bits_per_sample", 0)) if st.get("bits_per_sample") else 0,
        "ch": int(st.get("channels", 0)) if st.get("channels") else 0,
        "dur": float(dur) if dur else 0.0,
    }


def _probe(path):
    """优先用头解析(秒级), 失败/压缩格式回退 ffprobe。"""
    h = _probe_header(path)
    if h is not None:
        return {"sr": h[0], "bits": h[1], "ch": h[2], "dur": h[3]}
    return _ffprobe(path)


def specs(db, library=None, sample=200, deep=False):
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")   # 读写不互锁: 允许 specs 写时 dupes 并发读
    con.execute("""CREATE TABLE IF NOT EXISTS specs(
        path TEXT PRIMARY KEY, sr INT, bits INT, ch INT, dur REAL)""")
    where = "audio=1"
    params = []
    if library:
        where += " AND library=?"
        params.append(library)
    if deep:
        rows = con.execute(f"SELECT path FROM files WHERE {where}", params).fetchall()
    else:
        q = f"SELECT path FROM files WHERE {where} ORDER BY RANDOM() LIMIT {int(sample)}"
        rows = con.execute(q, params).fetchall()
    print(f"== 规格画像: 取样 {len(rows)} 个音频文件 ==")
    agg = {}
    n = 0
    for (p,) in rows:
        s = _probe(p)
        if not s:
            continue
        con.execute("INSERT OR REPLACE INTO specs(path,sr,bits,ch,dur) VALUES(?,?,?,?,?)",
                    (p, s["sr"], s["bits"], s["ch"], s["dur"]))
        key = (s["sr"], s["bits"], s["ch"])
        a = agg.setdefault(key, [0, 0.0])
        a[0] += 1
        a[1] += s["dur"]
        n += 1
        if n % 1000 == 0:        # 周期提交, 释放写锁 + 控制 WAL 体积
            con.commit()
    con.commit()
    print("\n采样规格分布 (采样率/位深/声道 : 文件数 / 总时长):")
    for key, (c, dur) in sorted(agg.items(), key=lambda x: -x[1][0]):
        print(f"  {key[0]}Hz / {key[1]}bit / {key[2]}ch : {c} 个, 共 {dur/60:.1f} 分钟")
    con.close()


# ----------------------------------------------------------------------------
# 频段相似检索 (bandsim) — 离线频段能量分布 + 余弦相似 (需 numpy)
# ----------------------------------------------------------------------------
BAND_EDGES = [20, 60, 250, 500, 1000, 2000, 4000, 8000, 20000]
BAND_LABELS = ["sub", "low", "lomid", "mid", "himid", "humid", "pres", "bril"]
BAND_SHORT = ["su", "lo", "lm", "mi", "hm", "hu", "pr", "br"]


def _ensure_bandprof(db):
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("""CREATE TABLE IF NOT EXISTS bandprof(
        path TEXT PRIMARY KEY, prof TEXT, centroid REAL, flux REAL)""")
    con.commit()
    return con


def _band_profile_from(x, sr=44100):
    """从已解码单声道信号 x(float64) 算 8 段频段能量 + 明亮度(质心) + 瞬态密度(flux)。
    向量化(批量 rfft + stride 取帧), 比逐帧 Python 循环快很多。
    返回 (prof_list, centroid, flux) 或 None(信号过短/全静音)。"""
    if x is None or x.size < 2048:
        return None
    x = x - x.mean()
    N, H = 2048, 1024
    nb = len(BAND_EDGES) - 1
    freqs = np.fft.rfftfreq(N, 1 / sr)
    idx = np.digitize(freqs, BAND_EDGES) - 1
    nframe = (len(x) - N) // H + 1
    if nframe < 1:
        return None
    stride = x.strides[0]
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(nframe, N), strides=(H * stride, stride)) * np.hanning(N)
    MAG = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    acc = MAG.sum(axis=0)
    s = acc.sum()
    if s <= 0:
        return None
    band = np.zeros(nb)
    for b in range(nb):
        band[b] = acc[idx == b].sum()
    tot = MAG.sum(axis=1)
    cents = (freqs * MAG).sum(axis=1) / (tot + 1e-12)
    flux = np.sqrt(((np.sqrt(MAG[1:]) - np.sqrt(MAG[:-1])) ** 2).sum(axis=1)).sum() / nframe
    return ((band / band.sum()).tolist(), float(np.mean(cents)), float(flux))


def _band_profile(path, sr=44100, lead=6):
    """离线算 8 段频段能量分布 + 明亮度 + 瞬态密度。先试 WAV 直读(免 ffmpeg, 快),
    失败/非 WAV 回退 ffmpeg。lead: 只取前 N 秒做指纹(音色在开头即稳定; 长环境音/
    多声道文件也能秒级算完, 避免把一个 100MB ambisonic 全部解码)。"""
    if np is None:
        print("bandsim 需要 numpy, 请先: pip install numpy", file=sys.stderr)
        sys.exit(1)
    x = _decode_fast(path, sr, lead)
    return _band_profile_from(x, sr)


def _describe_to_profile(text):
    """文字描述 -> 8 段权重向量 (归一化)。粗粒度启发式, 仅作检索入口。"""
    if np is None:
        print("bandsim 需要 numpy, 请先: pip install numpy", file=sys.stderr)
        sys.exit(1)
    t = (text or "").lower()
    w = np.ones(len(BAND_LABELS))
    if any(k in t for k in ["低频", "轰鸣", "rumble", "sub", "bass", "low", "重"]):
        w[0] += 2.0; w[1] += 1.0
    if any(k in t for k in ["中低", "温暖", "warm", "body", "lomid", "厚"]):
        w[2] += 2.0
    if any(k in t for k in ["中频", "mid", "饱满"]):
        w[3] += 1.5
    if any(k in t for k in ["明亮", "高频", "bright", "air", "high", "treble", "亮", "脆"]):
        w[5] += 1.0; w[6] += 2.0; w[7] += 2.0
    if any(k in t for k in ["冲击", "瞬态", "impact", "transient", "attack", "snap", "打击", "击"]):
        w[4] += 1.0; w[5] += 1.0
    return (w / w.sum()).tolist()


def _cosine(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _exec_retry(con, sql, params=()):
    """执行写 SQL; 遇 'database is locked'(多进程/后台并发写同一 DB) 自动重试。"""
    import time
    last = None
    for _ in range(10):
        try:
            con.execute(sql, params)
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                last = e
                time.sleep(0.4)
                continue
            raise
    raise last


def _commit_retry(con):
    """commit; 遇 'database is locked' 自动重试(并发写同一 DB 时可能出现)。"""
    import time
    last = None
    for _ in range(10):
        try:
            con.commit()
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                last = e
                time.sleep(0.4)
                continue
            raise
    raise last


# ----------------------------------------------------------------------------
def _decode(path, sr=44100, maxsec=None):
    """解码成单声道 float64 (numpy)。maxsec=None 全解码; 否则只取前 N 秒。失败 None。"""
    if np is None:
        print("分析需要 numpy: pip install numpy", file=sys.stderr)
        sys.exit(1)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path]
    if maxsec:
        cmd += ["-t", str(maxsec)]
    cmd += ["-af", f"aresample={sr}", "-f", "f32le", "-ac", "1", "-"]
    p = subprocess.run(cmd, capture_output=True)
    x = np.frombuffer(p.stdout, dtype=np.float32)
    if x.size == 0:
        return None
    return x.astype(np.float64)


def _decode_fast(path, sr=44100, maxsec=6):
    """快速解码成单声道 float64。对 WAV 直接读 PCM(免 ffmpeg 子进程, 这是性能关键:
    逐文件 spawn ffmpeg 在 34 万文件规模下会慢 ~30 倍)。非 WAV 或读失败回退 ffmpeg。
    只取前 maxsec 秒; 若原生采样率≠sr 用线性插值重采样(粗粒度频谱特征足够)。"""
    if np is None:
        print("分析需要 numpy: pip install numpy", file=sys.stderr)
        sys.exit(1)
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext == "wav":
        try:
            import wave
            with wave.open(path, "rb") as w:
                nch = w.getnchannels()
                sw = w.getsampwidth()
                rate = w.getframerate()
                nframes = w.getnframes()
                take = min(nframes, int(maxsec * rate))
                raw = w.readframes(take)
            if not raw:
                return None
            if sw == 2:
                data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
            elif sw == 1:
                data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
            elif sw == 3:
                a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int64)
                iv = a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)
                iv = np.where(iv >= (1 << 23), iv - (1 << 24), iv)
                data = iv.astype(np.float64) / 8388608.0
            elif sw == 4:
                data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
            else:
                return _decode(path, sr, maxsec)
            if nch > 1:
                data = data.reshape(-1, nch).mean(axis=1)
            if rate != sr and data.size > 1:
                n = data.size
                xi = np.linspace(0, n - 1, int(round(n * sr / rate)))
                data = np.interp(xi, np.arange(n), data).astype(np.float64)
            return data
        except Exception:
            return _decode(path, sr, maxsec)
    return _decode(path, sr, maxsec)


# ----------------------------------------------------------------------------
# 材质 / 合成设计指纹 (material fingerprint)
# 与 bandsim 的"频段能量分布"不同, 这一组特征刻画声音的"材质构成 + 合成手法":
#   tonal   谐波占比(振荡器/采样 vs 噪声发生)
#   bright  明亮度(滤波/振荡器设计)
#   sustain 持续度(铺底 Pad vs 脉冲/断奏)
#   tail    尾音长度(混响/延迟/反馈设计)
#   mod     调制深度(LFO/合唱/颤音/镶边设计)
#   density 事件密度(颗粒/纹理/连击设计)
#   attack  起音时间(拨弦/渐入 swell 设计)
#   trans   瞬态锐度/波峰(打击乐设计)
# 全部归一化到 0..1, 用余弦相似衡量"合成设计是否相近" —— 即使频段分布不同,
# 只要"声音是怎么做出来的"相近, 也会被找出来。
MAT_FEATS = ["tonal", "bright", "sustain", "tail", "mod", "density", "attack", "trans"]
MAT_DESC = {
    "tonal": "谐波纯度",
    "bright": "明亮度",
    "sustain": "持续度",
    "tail": "尾音长度",
    "mod": "调制深度",
    "density": "事件密度",
    "attack": "起音时间",
    "trans": "瞬态锐度",
}


def _material_features(x, sr=44100):
    """从已解码单声道信号 x(float64) 提取 8 维材质/合成指纹, 返回 list[float] 0..1。"""
    if x is None or x.size < 2048:
        return None
    x = x - x.mean()
    n = x.size
    H = 512
    # RMS 包络(帧长1024, 步长512)
    env = np.array([np.sqrt(np.mean(x[i:i + 1024] ** 2))
                    for i in range(0, n - 1024, H)], dtype=float)
    if env.size < 4:
        return None
    emax = env.max()
    if emax <= 1e-9:
        return None
    env = env / emax
    # 起音: 从 10%->90% 峰值的时间
    lo = int(np.argmax(env > 0.1))
    hi = int(np.argmax(env >= 0.9)) if (env >= 0.9).any() else env.size - 1
    attack_t = max(0.0, (hi - lo) * H / sr)
    attack = float(np.clip(attack_t / 2.0, 0, 1))      # 0=瞬起, 1=缓入>2s
    # 持续度: 超过 0.5 峰值的帧占比
    sustain = float(np.clip((env > 0.5).mean() * 1.6, 0, 1))
    # 尾音: 峰值之后做 log 线性衰减拟合, 斜率即衰减速率
    pk = int(np.argmax(env))
    tail = env[pk:]
    if tail.size > 4:
        t = np.arange(tail.size)
        le = np.log(tail + 1e-6)
        A = np.vstack([t, np.ones_like(t)]).T
        slope, _ = np.linalg.lstsq(A, le, rcond=None)[0]
        tail_feat = float(np.clip(-slope * H * 4.0, 0, 1))   # 衰减越慢→越长
    else:
        tail_feat = 0.0
    # 调制(AM/LFO): 包络去慢趋势后残差相对均值
    win_slow = max(3, int(0.5 * sr / H))
    if win_slow > len(env):
        win_slow = len(env)   # 极短文件: 慢窗不能超过包络长度, 否则 convolve 越界
    kernel = np.ones(win_slow) / win_slow
    base = np.convolve(env, kernel, mode="same")
    resid = env - base
    mod = resid.std() / (env.mean() + 1e-9)
    mod_feat = float(np.clip(mod * 2.5, 0, 1))
    # 事件密度: 包络正向增量 > 均值+2σ 的帧数 / 时长
    d_env = np.clip(np.diff(env), 0, None)
    if d_env.size:
        thr = d_env.mean() + 2.0 * d_env.std()
        events = int((d_env > thr).sum())
        dens = events / (n / sr)
    else:
        dens = 0.0
    density = float(np.clip(dens / 6.0, 0, 1))
    # 频谱: 平坦度→谐波纯度, 质心→明亮度 (向量化批量 rfft)
    N, Hf = 2048, 1024
    win = np.hanning(N)
    freqs = np.fft.rfftfreq(N, 1 / sr)
    nframe = (n - N) // Hf + 1
    if nframe < 1:
        return None
    stride = x.strides[0]
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(nframe, N), strides=(Hf * stride, stride)) * win
    MAG = np.abs(np.fft.rfft(frames, axis=1)) + 1e-12
    tot = MAG.sum(axis=1)
    flats = np.exp(np.mean(np.log(MAG), axis=1)) / MAG.mean(axis=1)
    cents = (freqs * MAG).sum(axis=1) / tot
    flat = float(np.mean(flats))
    cent = float(np.mean(cents))
    tonal = float(np.clip(1.0 / (1.0 + flat * 8.0), 0, 1))
    bright = float(np.clip(cent / 8000.0, 0, 1))
    # 瞬态锐度: 波峰因子(peak/rms)
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x ** 2)))
    crest = peak / (rms + 1e-9)
    trans = float(np.clip((crest - 3.0) / 15.0, 0, 1))
    return [tonal, bright, sustain, tail_feat, mod_feat, density, attack, trans]


def _material_tags(feats):
    """根据材质指纹推断"合成设计/声音材质"标签(启发式, 仅供参考)。"""
    if not feats:
        return []
    tonal, bright, sustain, tail, mod, density, attack, trans = feats
    tags = []
    if sustain > 0.6 or tail > 0.55:
        tags.append("长铺底/长尾(近似 Pad·Reverb·Delay)")
    if density > 0.5:
        tags.append("高事件密度(近似 Granular·Arp·多连击)")
    if mod > 0.5:
        tags.append("明显调制(近似 LFO·Chorus·Vibrato·Phaser)")
    if attack < 0.25 and trans > 0.5:
        tags.append("打击/瞬态(近似 Percussive·Hit)")
    tags.append("谐波音源(振荡器/采样)" if tonal > 0.55 else "噪声/纹理源")
    tags.append("明亮滤波/高通" if bright > 0.55 else "暗色滤波/低通")
    if tail <= 0.2 and sustain < 0.3:
        tags.append("干声/短衰减(近似 Dry·Staccato)")
    return tags


def _material_profile(path, sr=44100, lead=6):
    """解码+提取材质指纹。失败/静音返回 None。"""
    if np is None:
        print("材质分析需要 numpy: pip install numpy", file=sys.stderr)
        sys.exit(1)
    x = _decode_fast(path, sr, lead)
    return _material_features(x, sr)


def _profile_full_worker(path):
    """并行预计算 worker: 解码一次, 同时算 bandprof + matprof(避免重复解码)。
    返回 (path, prof_list, centroid, flux, mat_list) 或 None。供 multiprocessing 调用。"""
    x = _decode_fast(path, 44100, 6)
    if x is None:
        return None
    bp = _band_profile_from(x, 44100)
    if bp is None:
        return None
    mat = _material_features(x, 44100)
    if mat is None:
        return None
    return (path, bp[0], bp[1], bp[2], mat)


def _ensure_matprof(db):
    con = sqlite3.connect(db, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("""CREATE TABLE IF NOT EXISTS matprof(
        path TEXT PRIMARY KEY, feats TEXT)""")
    con.commit()
    return con


def _analyze_reference(path, maxsec=30):
    """对单个参考音效做全面声学分析 (离线, numpy+ffmpeg, 无训练模型)。
    返回 dict: 基础规格 + 动态 + 8段频段 + 明亮度/截止/平坦度/瞬态 + BPM + 主导频段。"""
    if not os.path.exists(path):
        return None
    # 基础规格 (ffprobe)
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=sample_rate,channels",
             "-of", "json", path], capture_output=True, text=True)
        info = json.loads(probe.stdout)
    except Exception:
        info = {}
    dur = float((info.get("format") or {}).get("duration") or 0)
    sr = None
    ch = None
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            sr = int(s.get("sample_rate") or 44100)
            ch = int(s.get("channels") or 1)
            break
    x = _decode_fast(path, 44100, maxsec)
    if x is None:
        return None
    x = x - x.mean()
    rms = float(np.sqrt(np.mean(x ** 2)))
    peak = float(np.max(np.abs(x)))
    crest = float(peak / (rms + 1e-9))
    # 逐窗 FFT 统计
    N, H = 2048, 1024
    win = np.hanning(N)
    freqs = np.fft.rfftfreq(N, 1 / 44100)
    idx = np.digitize(freqs, BAND_EDGES) - 1
    nb = len(BAND_EDGES) - 1
    acc = np.zeros(nb)
    cents, flats, fluxes, roffs = [], [], [], []
    prev = None
    nframes = 0
    for s in range(0, len(x) - N, H):
        seg = x[s:s + N] * win
        mag = np.abs(np.fft.rfft(seg)) ** 2
        b = np.array([mag[idx == i].sum() for i in range(nb)], dtype=float)
        if b.sum() > 0:
            acc += b
        tot = mag.sum()
        cents.append(float((freqs * mag).sum() / (tot + 1e-9)))
        csum = np.cumsum(mag)
        roffs.append(float(freqs[np.searchsorted(csum, 0.85 * tot)]) if tot > 0 else 0.0)
        flats.append(float(np.exp(np.mean(np.log(mag + 1e-12))) / (mag.mean() + 1e-12)))
        env = np.abs(seg)
        if prev is not None:
            fluxes.append(float(np.mean(np.abs(env - prev))))
        prev = env
        nframes += 1
    if acc.sum() > 0:
        prof = (acc / acc.sum()).tolist()
    else:
        prof = [0.0] * nb
    # BPM 近似 (复用 music_emotion 的包络自相关)
    bpm = 0.0
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import music_emotion
        bpm = music_emotion._tempo(x, 44100)
    except Exception:
        pass
    # 材质 / 合成设计指纹 (复用已解码的 x, 不重复解码)
    mat = _material_features(x, 44100)
    mat_tags = _material_tags(mat) if mat else []
    return dict(
        duration=dur, sample_rate=sr or 44100, channels=ch or 1,
        rms=round(rms, 5), peak=round(peak, 5), crest=round(crest, 2),
        bands=[round(v, 4) for v in prof],
        centroid=round(float(np.mean(cents)), 1) if cents else 0.0,
        rolloff=round(float(np.mean(roffs)), 1) if roffs else 0.0,
        flatness=round(float(np.mean(flats)), 3) if flats else 0.0,
        flux=round(float(np.mean(fluxes)), 4) if fluxes else 0.0,
        bpm=bpm,
        dominant=BAND_LABELS[int(np.argmax(prof))] if acc.sum() > 0 else "-",
        mat=[round(v, 3) for v in mat] if mat else None,
        mat_tags=mat_tags,
    )


def _print_analysis(ar):
    """打印 _analyze_reference 的结果为人类可读报告。"""
    if not ar:
        print("(无法分析该文件)")
        return
    print(f"\n== 参考音效全面分析 ==")
    print(f"  规格: {ar['duration']:.1f}s | {ar['sample_rate']}Hz | {ar['channels']}ch")
    print(f"  动态: RMS={ar['rms']:.4f}  Peak={ar['peak']:.4f}  Crest(波峰因子)={ar['crest']:.1f}x")
    print(f"  频谱: 质心={ar['centroid']:.0f}Hz  85%能量截止={ar['rolloff']:.0f}Hz  "
          f"平坦度={ar['flatness']:.2f}(越低越有音色/越纯)  瞬态密度={ar['flux']:.3f}")
    print(f"  节奏: 估测 BPM≈{ar['bpm']}  (包络自相关, 无beat则≈0)")
    print(f"  主导频段: {ar['dominant']}")
    bar = " ".join(f"{c}:{int(v * 100)}" for c, v in zip(BAND_SHORT, ar['bands']))
    print(f"  8段能量: [{bar}]")
    if ar.get("mat"):
        print("\n-- 声音材质 / 合成设计指纹 --")
        for k, v in zip(MAT_FEATS, ar["mat"]):
            block = "\u2588" * int(round(v * 20))
            print(f"  {MAT_DESC[k]:<8} {v:.2f} [{block}]")
        if ar.get("mat_tags"):
            print("  推断合成设计: " + " · ".join(ar["mat_tags"]))


def analyze_reference_cmd(query, as_json=False, maxsec=30):
    """`analyze` 子命令: 对单个参考文件做全面分析。"""
    if not query:
        print("analyze 需要 --query <参考音效路径>", file=sys.stderr)
        sys.exit(1)
    ar = _analyze_reference(query, maxsec)
    if as_json:
        print(json.dumps(ar, ensure_ascii=False, indent=2))
    else:
        _print_analysis(ar)


def _export_topk(items, export):
    """items: list of (sim, path, vec)。复制 Top-K 到 export 文件夹并打包 zip(只复制、不动原库)。
    返回 (copied, zip_path)。"""
    export = os.path.expanduser(export)
    os.makedirs(export, exist_ok=True)
    copied = 0
    seen = {}
    for sim, p, vec in items:
        if not os.path.exists(p):
            continue
        base = os.path.basename(p)
        if base in seen:
            seen[base] += 1
            base = f"{seen[base]:03d}_{base}"
        else:
            seen[base] = 0
        try:
            shutil.copy2(p, os.path.join(export, base))
            copied += 1
        except Exception as e:
            print(f"  复制失败 {base}: {e}", file=sys.stderr)
    zip_path = export.rstrip(os.sep) + ".zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(export):
            for fn in files:
                fp = os.path.join(root, fn)
                z.write(fp, os.path.relpath(fp, os.path.dirname(export)))
    return copied, zip_path


def _like_wrap(pat):
    """--like / --notlike 的"子串过滤"语义: 默认把输入当字面子串(下划线/百分号转义), 前后包 %。
    若用户已显式写 % 通配, 则原样保留(走原生 LIKE 高级用法)。"""
    p = pat.strip()
    if "%" in p:
        return p.lower()
    esc = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + esc.lower() + "%"


def bandsim(db, query=None, describe=None, library=None, subcat=None,
            like=None, notlike=None, topk=20, verbose=False, export=None,
            no_export=False, threshold=0.55, parallel=1, min_sim=None):
    """按相似频段在音效库里找音效。query=参考音效路径; describe=频段文字描述。
    候选范围按 library/subcat/like 限定 (即时算 + 缓存到 bandprof 表, 每200提交可断点续跑)。"""
    if not query and not describe:
        print("bandsim 需要 --query <参考音效路径> 或 --describe <频段描述>",
              file=sys.stderr)
        sys.exit(1)
    if query:
        qp = _band_profile(query)
        if qp is None:
            print(f"无法读取参考音效: {query}", file=sys.stderr)
            sys.exit(1)
        qprof, qcent, qflux = qp
        qname = os.path.basename(query)
    else:
        qprof = _describe_to_profile(describe)
        qcent = qflux = 0.0
        qname = f"描述[{describe}]"
    print(f"== 查询向量 ({qname}) ==")
    for l, v in zip(BAND_LABELS, qprof):
        print(f"  {l:6} {v * 100:6.2f}%")
    con = _ensure_bandprof(db)
    wheres = ["audio=1"]
    params = []
    if library:
        wheres.append("library=?")
        params.append(library)
    if subcat:
        wheres.append("subcat=?")
        params.append(subcat)
    if like:
        patterns = [p.strip() for p in like.split(",") if p.strip()]
        ors = " OR ".join(r"LOWER(path) LIKE ? ESCAPE '\'" for _ in patterns)
        wheres.append(f"({ors})")
        params.extend(_like_wrap(pat) for pat in patterns)
    if notlike:
        # 排除语义不符的候选(如把 Foley 家具音排除, 只留环境氛围 bed)
        npats = [p.strip() for p in notlike.split(",") if p.strip()]
        nors = " OR ".join(r"LOWER(path) LIKE ? ESCAPE '\'" for _ in npats)
        wheres.append(f"NOT ({nors})")
        params.extend(_like_wrap(pat) for pat in npats)
    if query:
        wheres.append("path <> ?")   # 排除参考音效自身(避免 100% 自匹配)
        params.append(query)
    cands = con.execute(
        f"SELECT path FROM files WHERE {' AND '.join(wheres)}", params).fetchall()
    print(f"-- 候选范围: {len(cands)} 个音频 (即时算, 结果缓存 bandprof, 每200提交) --")
    scored = []
    done = 0
    uncached = []
    for (p,) in cands:
        row = con.execute(
            "SELECT prof,centroid,flux FROM bandprof WHERE path=?", (p,)).fetchone()
        if row and row[0]:
            prof = [float(x) for x in row[0].split(",")]
            scored.append((_cosine(qprof, prof), p, prof))
        else:
            uncached.append(p)
    use_pool = bool(parallel) and len(uncached) > 100
    if use_pool:
        nproc = min(int(parallel), os.cpu_count() or 4)
        with multiprocessing.Pool(nproc) as pool:
            for res in pool.imap_unordered(_profile_full_worker, uncached, chunksize=32):
                if not res:
                    continue
                p, prof, cent, flux, mat = res
                _exec_retry(con,
                    "INSERT OR REPLACE INTO bandprof(path,prof,centroid,flux) VALUES(?,?,?,?)",
                    (p, ",".join(f"{x:.6f}" for x in prof), cent, flux))
                _exec_retry(con,
                    "INSERT OR REPLACE INTO matprof(path,feats) VALUES(?,?)",
                    (p, ",".join(f"{x:.4f}" for x in mat)))
                scored.append((_cosine(qprof, prof), p, prof))
                done += 1
                if done % 200 == 0:
                    _commit_retry(con)
                    sys.stderr.write(f"\r  已算 {done}/{len(uncached)}"); sys.stderr.flush()
    else:
        for p in uncached:
            res = _profile_full_worker(p)
            if not res:
                continue
            p, prof, cent, flux, mat = res
            _exec_retry(con,
                "INSERT OR REPLACE INTO bandprof(path,prof,centroid,flux) VALUES(?,?,?,?)",
                (p, ",".join(f"{x:.6f}" for x in prof), cent, flux))
            _exec_retry(con,
                "INSERT OR REPLACE INTO matprof(path,feats) VALUES(?,?)",
                (p, ",".join(f"{x:.4f}" for x in mat)))
            scored.append((_cosine(qprof, prof), p, prof))
            done += 1
            if done % 200 == 0:
                _commit_retry(con)
    _commit_retry(con)
    con.close()
    scored.sort(key=lambda r: -r[0])
    if min_sim is not None:
        keep = [r for r in scored if r[0] >= min_sim]
        print(f"\n=== 所有相似(≥{min_sim * 100:.0f}%) 音效: {len(keep)} 个 ===")
    else:
        keep = scored[:topk]
        print(f"\n=== Top {min(topk, len(scored))} 相似音效 ===")
    for sim, p, prof in keep:
        print(f"  {sim * 100:5.1f}%  {p}")
        if verbose:
            bar = " ".join(f"{c}:{int(v * 100)}" for c, v in zip(BAND_SHORT, prof))
            print(f"          [{bar}]")
    if not scored:
        print("(本地库未找到可读取的相似候选)")
        # 优化2: 本地找不到时, 自动对参考音效做全面分析, 至少给你一份详尽的参考画像
        if query and os.path.exists(query):
            print("-- 已自动对参考音效做全面分析 --")
            _print_analysis(_analyze_reference(query))
        return
    # 自动导出(默认开): 未显式 --export 且未 --no-export 时, 默认把结果落到
    # ~/Desktop/sfx_similar/<参考名>/ 并打包 zip。只复制、不动原音效库。
    # 用 --export <路径> 覆盖目标, 用 --no-export 关闭自动导出。
    if export is None and not no_export:
        refname = os.path.basename(query if query else describe or "result")
        refname = os.path.splitext(refname)[0]
        refname = re.sub(r'[^A-Za-z0-9 _\-\u4e00-\u9fff]', '_', refname)
        export = os.path.join(os.path.expanduser("~/Desktop/sfx_similar"), refname)
    if export:
        copied, zip_path = _export_topk(keep, export)
        print(f"\n-- 已导出 {copied} 个相似音效 -> {export}")
        print(f"-- 已打包 -> {zip_path}")
    # 材质/合成设计兜底: 频段相似度不足时, 自动改用"材质/合成设计"维度检索
    # (即解析参考音效组成 + 全库搜相近材质/合成设计的声音)。
    if scored and threshold and scored[0][0] < threshold and query and os.path.exists(query):
        print(f"\n!! 最佳频段相似度仅 {scored[0][0] * 100:.1f}% (< 阈值 {threshold * 100:.0f}%), "
              f"频段维度找不到足够相似的音效。")
        print(">> 自动改用【材质/合成设计】维度: 分析参考音效组成 + 全库搜相近材质/合成...")
        compsim(db, query, library, subcat, like, notlike, topk, verbose, None, no_export, min_sim)


def compsim(db, query=None, library=None, subcat=None, like=None, notlike=None,
            topk=20, verbose=False, export=None, no_export=False, parallel=1,
            min_sim=None):
    """材质/合成设计相似检索: 分析参考音效的"声音材质构成 + 合成设计", 再全库搜相近材质。
    与 bandsim(频段能量分布)是不同维度 —— 即使频段分布不同, 只要"声音是怎么做出来的"相近也会命中。
    这正是"找不到相似音效时"的兜底能力: 解析参考组成 → 按材质指纹全库匹配。"""
    if not query:
        print("compsim 需要 --query <参考音效路径>", file=sys.stderr)
        sys.exit(1)
    qmat = _material_profile(query)
    if qmat is None:
        print(f"无法读取参考音效: {query}", file=sys.stderr)
        sys.exit(1)
    print(f"== 参考材质指纹 ({os.path.basename(query)}) ==")
    for k, v in zip(MAT_FEATS, qmat):
        print(f"  {MAT_DESC[k]:<8} {v:.2f}")
    tags = _material_tags(qmat)
    if tags:
        print("  推断合成设计: " + " · ".join(tags))
    con = _ensure_matprof(db)
    wheres = ["audio=1"]
    params = []
    if library:
        wheres.append("library=?")
        params.append(library)
    if subcat:
        wheres.append("subcat=?")
        params.append(subcat)
    if like:
        patterns = [p.strip() for p in like.split(",") if p.strip()]
        ors = " OR ".join(r"LOWER(path) LIKE ? ESCAPE '\'" for _ in patterns)
        wheres.append(f"({ors})")
        params.extend(_like_wrap(pat) for pat in patterns)
    if notlike:
        npats = [p.strip() for p in notlike.split(",") if p.strip()]
        nors = " OR ".join(r"LOWER(path) LIKE ? ESCAPE '\'" for _ in npats)
        wheres.append(f"NOT ({nors})")
        params.extend(_like_wrap(pat) for pat in npats)
    if query:
        wheres.append("path <> ?")   # 排除参考音效自身
        params.append(query)
    cands = con.execute(
        f"SELECT path FROM files WHERE {' AND '.join(wheres)}", params).fetchall()
    print(f"-- 候选范围: {len(cands)} 个音频 (即时算, 结果缓存 matprof, 每200提交) --")
    scored = []
    done = 0
    uncached = []
    for (p,) in cands:
        row = con.execute("SELECT feats FROM matprof WHERE path=?", (p,)).fetchone()
        if row and row[0]:
            feats = [float(x) for x in row[0].split(",")]
            scored.append((_cosine(qmat, feats), p, feats))
        else:
            uncached.append(p)
    use_pool = bool(parallel) and len(uncached) > 100
    if use_pool:
        nproc = min(int(parallel), os.cpu_count() or 4)
        with multiprocessing.Pool(nproc) as pool:
            for res in pool.imap_unordered(_profile_full_worker, uncached, chunksize=32):
                if not res:
                    continue
                p, prof, cent, flux, mat = res
                _exec_retry(con,
                    "INSERT OR REPLACE INTO bandprof(path,prof,centroid,flux) VALUES(?,?,?,?)",
                    (p, ",".join(f"{x:.6f}" for x in prof), cent, flux))
                _exec_retry(con,
                    "INSERT OR REPLACE INTO matprof(path,feats) VALUES(?,?)",
                    (p, ",".join(f"{x:.4f}" for x in mat)))
                scored.append((_cosine(qmat, mat), p, mat))
                done += 1
                if done % 200 == 0:
                    _commit_retry(con)
                    sys.stderr.write(f"\r  已算 {done}/{len(uncached)}"); sys.stderr.flush()
    else:
        for p in uncached:
            res = _profile_full_worker(p)
            if not res:
                continue
            p, prof, cent, flux, mat = res
            _exec_retry(con,
                "INSERT OR REPLACE INTO bandprof(path,prof,centroid,flux) VALUES(?,?,?,?)",
                (p, ",".join(f"{x:.6f}" for x in prof), cent, flux))
            _exec_retry(con,
                "INSERT OR REPLACE INTO matprof(path,feats) VALUES(?,?)",
                (p, ",".join(f"{x:.4f}" for x in mat)))
            scored.append((_cosine(qmat, mat), p, mat))
            done += 1
            if done % 200 == 0:
                _commit_retry(con)
    _commit_retry(con)
    con.close()
    scored.sort(key=lambda r: -r[0])
    if min_sim is not None:
        keep = [r for r in scored if r[0] >= min_sim]
        print(f"\n=== 所有材质相近(≥{min_sim * 100:.0f}%): {len(keep)} 个 ===")
    else:
        keep = scored[:topk]
        print(f"\n=== Top {min(topk, len(scored))} 材质/合成设计相近音效 ===")
    for sim, p, feats in keep:
        print(f"  {sim * 100:5.1f}%  {p}")
        if verbose:
            bar = " ".join(f"{k}:{v:.2f}" for k, v in zip(MAT_FEATS, feats))
            print(f"          [{bar}]")
    if not scored:
        print("(本地库未找到可读取的材质候选)")
        return
    # 自动导出(默认开): 与 bandsim 同机制, 默认落到 ~/Desktop/sfx_similar/<参考名>_mat/
    if export is None and not no_export:
        refname = os.path.basename(query)
        refname = os.path.splitext(refname)[0]
        refname = re.sub(r'[^A-Za-z0-9 _\-\u4e00-\u9fff]', '_', refname)
        export = os.path.join(os.path.expanduser("~/Desktop/sfx_similar"), refname + "_mat")
    if export:
        copied, zip_path = _export_topk(keep, export)
        print(f"\n-- 已导出 {copied} 个材质相近音效 -> {export}")
        print(f"-- 已打包 -> {zip_path}")


def prewarm(db, library=None, subcat=None, limit=None, parallel=1):
    """并行预计算 bandprof + matprof 缓存(跳过已缓存, 可断点续跑)。
    跑完后 bandsim / compsim 任意检索秒回。parallel=进程数(0/负=自动=CPU核数)。"""
    if np is None:
        print("prewarm 需要 numpy: pip install numpy", file=sys.stderr)
        sys.exit(1)
    con = _ensure_bandprof(db)
    con.execute("CREATE TABLE IF NOT EXISTS matprof(path TEXT PRIMARY KEY, feats TEXT)")
    con.commit()
    wheres = ["audio=1",
              "(path NOT IN (SELECT path FROM bandprof) "
              " OR path NOT IN (SELECT path FROM matprof))"]
    params = []
    if library:
        wheres.append("library=?"); params.append(library)
    if subcat:
        wheres.append("subcat=?"); params.append(subcat)
    rows = con.execute(
        f"SELECT path FROM files WHERE {' AND '.join(wheres)}", params).fetchall()
    if limit:
        rows = rows[:int(limit)]
    uncached = [r[0] for r in rows]
    total = len(uncached)
    print(f"== prewarm: 待算 {total} 个 (已缓存跳过), 进度写 stderr ==")
    done = 0
    use_pool = bool(parallel) and total > 100
    if use_pool:
        nproc = min(int(parallel), os.cpu_count() or 4)
        with multiprocessing.Pool(nproc) as pool:
            for res in pool.imap_unordered(_profile_full_worker, uncached, chunksize=32):
                if not res:
                    continue
                p, prof, cent, flux, mat = res
                _exec_retry(con,
                    "INSERT OR REPLACE INTO bandprof(path,prof,centroid,flux) VALUES(?,?,?,?)",
                    (p, ",".join(f"{x:.6f}" for x in prof), cent, flux))
                _exec_retry(con,
                    "INSERT OR REPLACE INTO matprof(path,feats) VALUES(?,?)",
                    (p, ",".join(f"{x:.4f}" for x in mat)))
                done += 1
                if done % 200 == 0:
                    _commit_retry(con)
                    sys.stderr.write(f"\r  prewarm {done}/{total} ({done * 100 // total}%)")
                    sys.stderr.flush()
    else:
        for p in uncached:
            res = _profile_full_worker(p)
            if not res:
                continue
            p, prof, cent, flux, mat = res
            _exec_retry(con,
                "INSERT OR REPLACE INTO bandprof(path,prof,centroid,flux) VALUES(?,?,?,?)",
                (p, ",".join(f"{x:.6f}" for x in prof), cent, flux))
            _exec_retry(con,
                "INSERT OR REPLACE INTO matprof(path,feats) VALUES(?,?)",
                (p, ",".join(f"{x:.4f}" for x in mat)))
            done += 1
            if done % 200 == 0:
                _commit_retry(con)
                sys.stderr.write(f"\r  prewarm {done}/{total} ({done * 100 // total}%)")
                sys.stderr.flush()
    con.commit()
    con.close()
    print(f"\nprewarm 完成: 新增 {done} 条 (bandprof+matprof)")


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="音效库管理")
    ap.add_argument("cmd", choices=["scan", "search", "report", "dupes", "specs", "bandsim", "prewarm", "analyze", "compsim"])
    ap.add_argument("--root", help="音效库根目录 (scan 用)")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"索引DB路径 (默认 {DEFAULT_DB})")
    ap.add_argument("--kw", help="关键词 (search, 模糊匹配文件名)")
    ap.add_argument("--library", help="顶层库名过滤")
    ap.add_argument("--ext", help="扩展名过滤 (不含点)")
    ap.add_argument("--audio-only", action="store_true")
    ap.add_argument("--min-size", type=int)
    ap.add_argument("--max-size", type=int)
    ap.add_argument("--include-junk", action="store_true", help="检索含 ._* 垃圾文件")
    # default=None: 仅当用户显式 --limit 才生效; prewarm 中为 None=不限量。
    # search/dupes 在 dispatch 处回退到各自的 50 上限。
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--mode", default="size", choices=["size", "hash"])
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--query", help="参考音效路径 (bandsim, 按相似频段找)")
    ap.add_argument("--describe", help="频段文字描述 (bandsim, 如 '低频轰鸣+明亮瞬态')")
    ap.add_argument("--subcat", help="子类过滤 (bandsim 候选范围)")
    ap.add_argument("--like", help="路径子串过滤(逗号分隔多个, LOWER(path) LIKE, 缩小候选池)")
    ap.add_argument("--notlike", help="排除过滤(逗号分隔多个, LOWER(path) LIKE, 命中任一个即剔除; 用于剔除 Foley/家具等语义不符项)")
    ap.add_argument("--topk", type=int, default=20, help="相似音效返回数量下限(默认20, 保底)")
    ap.add_argument("--min-sim", type=float, default=None,
                    help="导出所有相似度≥该值(0-1)的候选, 替代 topk 上限(用于'导出所有相似音效')")
    ap.add_argument("--threshold", type=float, default=0.55,
                    help="bandsim: 最佳相似度低于该阈值时, 自动改用材质/合成设计维度检索 (默认0.55)")
    ap.add_argument("--verbose", action="store_true", help="bandsim 显示候选频段条")
    ap.add_argument("--export", help="bandsim: 把 Top-K 相似音效复制到该文件夹并打包 zip(非破坏, 原文件不动)。省略时默认落到 ~/Desktop/sfx_similar/<参考名>/")
    ap.add_argument("--no-export", action="store_true", help="bandsim: 关闭'每次搜索自动导出到桌面'的默认行为")
    ap.add_argument("--maxsec", type=int, default=30, help="analyze: 分析前 N 秒 (默认30)")
    ap.add_argument("--json", action="store_true", help="analyze: 输出 JSON")
    ap.add_argument("--parallel", type=int, default=0,
                    help="并行进程数(默认0=自动取CPU核数); >=2 时全库扫描用多进程, 大幅加速")
    args = ap.parse_args()
    par = args.parallel or (os.cpu_count() or 4)

    if args.cmd == "scan":
        if not args.root:
            print("scan 需要 --root", file=sys.stderr); sys.exit(1)
        build_index(args.root, args.db)
    elif args.cmd == "search":
        search(args.db, args.kw, args.library, args.ext, args.audio_only,
               args.min_size, args.max_size,
               args.limit if args.limit is not None else 50, args.include_junk)
    elif args.cmd == "report":
        report(args.db)
    elif args.cmd == "dupes":
        dupes(args.db, args.mode, args.limit if args.limit is not None else 50)
    elif args.cmd == "specs":
        specs(args.db, args.library, args.sample, args.deep)
    elif args.cmd == "bandsim":
        bandsim(args.db, args.query, args.describe, args.library, args.subcat,
                args.like, args.notlike, args.topk, args.verbose, args.export,
                args.no_export, args.threshold, par, args.min_sim)
    elif args.cmd == "analyze":
        analyze_reference_cmd(args.query, args.json, args.maxsec)
    elif args.cmd == "compsim":
        compsim(args.db, args.query, args.library, args.subcat,
                args.like, args.notlike, args.topk, args.verbose,
                args.export, args.no_export, par, args.min_sim)
    elif args.cmd == "prewarm":
        # limit=None => 不限量, 跑完整个 audio 集合; 显式 --limit 则只跑前 N 个
        prewarm(args.db, args.library, args.subcat, args.limit, par)


if __name__ == "__main__":
    main()
