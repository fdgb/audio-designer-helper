#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""music_emotion.py — CG / 视频里的音乐情绪分析 (离线, numpy+ffmpeg, 无训练模型)。

设计: 把音轨按固定窗口切片, 每窗提取声学描述子 (能量/RMS, 明亮度/频谱质心,
张力/频谱平坦度, 调式明暗/大小调近似, 瞬态密度), 再映射成 valence(愉悦度)–arousal(激
昂度) 二维 + 离散情绪标签, 输出一张"情绪时间线"。全局再估一个 tempo(BPM)。

诚实边界:
- 分析的是**音乐自身的声学情绪**, 不是画面叙事情绪; 镜头悲喜它不知道。
- 若视频里人声/音效与音乐混在一起, 会污染特征 → 最好喂纯音乐轨或音乐占比高的段。
- 没有训练好的"情绪分类模型" (那要从 zenodo 下权重, 本环境被限速), 所以标签是粗粒度
  的启发式, 用作"找情绪错位/核对情绪弧"足够, 不是精确心理学量表。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

try:
    import numpy as np
except ImportError:
    np = None

SR = 22050
BANDS = [20, 60, 250, 500, 1000, 2000, 4000, 8000, 20000]
# 大调/小调模板 (12 半音, 相对根音的 1/3/5)
MAJOR = [1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0]
MINOR = [1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0]


def _read_mono(path, sr=SR):
    """读成单声道 float(22050Hz)。失败返回 None。
    统一用单条 ffmpeg 直接输出 f32le, 避免先写 temp wav 再读两遍子进程。
    """
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
         "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True)
    if p.returncode != 0:
        return None
    x = np.frombuffer(p.stdout, dtype=np.float32)
    if x.size == 0:
        return None
    return x.astype(np.float64)


def _window_features(x, sr, t0, dur):
    s = int(t0 * sr)
    e = int((t0 + dur) * sr)
    seg = x[s:e]
    if seg.size < 512:
        return None
    rms = float(np.sqrt(np.mean(seg ** 2)))
    N = min(4096, len(seg))
    if N < 256:
        return None
    mag = np.abs(np.fft.rfft(seg[:N] * np.hanning(N))) ** 2
    freqs = np.fft.rfftfreq(N, 1 / sr)
    idx = np.digitize(freqs, BANDS) - 1
    nb = len(BANDS) - 1
    bands = np.array([mag[idx == b].sum() for b in range(nb)], dtype=float)
    tot = bands.sum()
    if tot > 0:
        bands = bands / tot
    centroid = float((freqs * mag).sum() / tot) if tot > 0 else 0.0
    flat = float(np.exp(np.mean(np.log(mag + 1e-12))) / (mag.mean() + 1e-12))
    # 粗 chroma: 频率→半音
    midi = 69 + 12 * np.log2(freqs[1:] / 440.0)
    chroma = np.zeros(12)
    for f, m in zip(midi, mag[1:]):
        c = int(round(f)) % 12
        chroma[c] += m
    cs = chroma.sum()
    if cs > 0:
        chroma = chroma / cs
    best_maj, best_min = -1.0, -1.0
    for k in range(12):
        roll = np.roll(chroma, k)
        ma = float(np.dot(roll, np.array(MAJOR)))
        mi = float(np.dot(roll, np.array(MINOR)))
        best_maj = max(best_maj, ma)
        best_min = max(best_min, mi)
    mode = "Major" if best_maj >= best_min else "Minor"
    return dict(rms=rms, bands=bands.tolist(), centroid=centroid,
                flatness=flat, mode=mode)


def _tempo(x, sr):
    """全局 BPM 近似: 包络自相关。粗, 仅作参考。"""
    hop = int(sr / 100.0)  # 100Hz 包络
    if len(x) < hop * 4:
        return 0.0
    env = np.abs(x[::hop])
    env = env - np.mean(env)
    min_lag = int(0.3 * 100)   # 0.3s -> 200 BPM
    max_lag = int(2.0 * 100)   # 2.0s -> 30 BPM
    if max_lag >= len(env):
        return 0.0
    # 一次性计算完整自相关（避免逐 lag 分配大数组导致内存/耗时爆炸）
    full = np.correlate(env, env, mode="full")
    mid = len(env) - 1
    best_lag, best_ac = min_lag, -1.0
    for lag in range(min_lag, max_lag):
        if lag >= len(env):
            break
        val = float(full[mid + lag]) / (len(env) - lag)
        if val > best_ac:
            best_ac, best_lag = val, lag
    bpm = 60.0 / (best_lag / 100.0)
    return round(bpm, 1)


def _tag(v, a, tension):
    if a >= 0.6 and v >= 0.6:
        return "激昂/史诗"
    if a >= 0.6 and v < 0.4:
        return "紧张/压迫"
    if a < 0.4 and v >= 0.6:
        return "宁静/温暖"
    if a < 0.4 and v < 0.4:
        return "悲伤/低沉"
    if v >= 0.5:
        return "欢快"
    return "神秘/中性"


def analyze(path, win_dur=8.0, verbose=False):
    if np is None:
        print("music_emotion 需要 numpy: pip install numpy", file=sys.stderr)
        sys.exit(1)
    x = _read_mono(path)
    if x is None:
        print(f"无法读取音轨: {path}", file=sys.stderr)
        sys.exit(1)
    dur = len(x) / SR
    bpm = _tempo(x, SR)
    print(f"== 音乐情绪分析: {os.path.basename(path)} ==")
    print(f"时长 {dur:.1f}s | 估测 tempo ≈ {bpm} BPM | 窗口 {win_dur}s")
    print("-- 情绪时间线 --")
    rows = []
    t = 0.0
    while t < dur:
        f = _window_features(x, SR, t, win_dur)
        if f:
            energy = f["rms"]
            bright = f["centroid"]
            tension = f["flatness"]
            valence = 0.5 + (0.3 if f["mode"] == "Major" else -0.3) + (bright - 1500) / 4000.0
            valence = max(0.0, min(1.0, valence))
            arousal = max(0.0, min(1.0, energy * 6.0 + bright / 4000.0))
            tag = _tag(valence, arousal, tension)
            rows.append((t, f, valence, arousal, tag))
            print(f"  {t:5.1f}s  {f['mode']:5} cent={bright:6.0f}Hz "
                  f"tense={tension:.2f} V={valence:.2f} A={arousal:.2f} -> {tag}")
            if verbose:
                bl = " ".join(f"{v*100:.0f}" for v in f["bands"])
                print(f"          bands[sub,low,lomid,mid,himid,humid,pres,bril]={bl}")
        t += win_dur
    if rows:
        av = float(np.mean([r[2] for r in rows]))
        aa = float(np.mean([r[3] for r in rows]))
        print(f"\n== 总体: valence≈{av:.2f} arousal≈{aa:.2f} -> "
              f"{_tag(av, aa, 0.5)} ==")
    return dict(duration=dur, bpm=bpm, timeline=[
        dict(t=round(t, 1), mode=f["mode"], centroid=round(f["centroid"], 0),
             tension=round(f["flatness"], 2), valence=round(v, 2),
             arousal=round(a, 2), tag=tag)
        for (t, f, v, a, tag) in rows])


def main():
    ap = argparse.ArgumentParser(description="CG/视频音乐情绪分析")
    ap.add_argument("path", help="视频或音频文件")
    ap.add_argument("--win", type=float, default=8.0, help="窗口秒数 (默认 8)")
    ap.add_argument("--verbose", action="store_true", help="显示每窗频段条")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非文本")
    args = ap.parse_args()
    res = analyze(args.path, args.win, args.verbose)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
