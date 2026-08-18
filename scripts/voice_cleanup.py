#!/usr/bin/env python3
"""
voice_cleanup.py — VO 语音修复：诊断 + 处理 + 验收（齿音/削波/底噪/喷麦/口水音）

用法:
  诊断:  python voice_cleanup.py diagnose <vo.wav>
  处理:  python voice_cleanup.py process <vo.wav> <out.wav>
            [--declip] [--declick] [--hpf|--no-hpf]
            [--denoise NR NF] | [--arnndn /path/model.rnnn]
            [--deess I] [--target-lufs X] [--fade MS]
  自动:  python voice_cleanup.py auto <vo.wav> <out.wav> [--deess I] [--target-lufs X] [--fade MS]
  验收:  python voice_cleanup.py verify <raw.wav> <clean.wav>
  文件夹验收扫描:  python voice_cleanup.py report <folder> [--csv out.csv]
  批量自动修复:    python voice_cleanup.py batch <src_dir> <dst_dir> [flags...]

原则（见 references/voice_cleanup.md）:
  - 先诊断后处理, 最小干预 (auto 只修"机器可判定"的问题, 齿音留给人工)
  - 顺序: adeclip -> adeclick -> highpass -> afftdn/arnndn -> deesser -> (loudness)
  - VO 跟随源声道数, 不写 -ac
  - 禁 dynaudnorm / speechnorm
  - 大面积平顶削波(FlatFactor>0.9)自动跳过 adeclip 防 OOM, 提示重录
  - diagnose 新增 5–10kHz 齿音频谱提示(仅提示, 不自动修)
"""
import json
import re
import subprocess
import sys
import tempfile
import os
import array
import math

FFMPEG = "ffmpeg"


# ---------- 测量 ----------

def run_ffmpeg_stderr(args):
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostdin"] + args,
                       capture_output=True, text=True)
    return p.stderr


def get_duration(path):
    err = run_ffmpeg_stderr(["-i", path, "-f", "null", "-"])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", err)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def measure_loudnorm(path):
    err = run_ffmpeg_stderr(["-i", path, "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        raise RuntimeError("loudnorm 输出解析失败")
    d = json.loads(m.group(0))
    return float(d["input_i"]), float(d["input_tp"]), float(d["input_lra"])


def measure_astats(path):
    err = run_ffmpeg_stderr(["-i", path, "-af", "astats", "-f", "null", "-"])
    def grab(key):
        vals = re.findall(rf"{key}:\s*(-?[\d.]+|inf|-inf)", err)
        return [float(v) for v in vals if v not in ("inf", "-inf")]
    return {
        "peak_level": max(grab("Peak level dB") or [-999]),
        "flat_factor": max(grab("Flat factor") or [0]),
        "peak_count": max(grab("Peak count") or [0]),
        "rms_trough": min(grab("RMS trough dB") or [-999]),
    }


def _silence_floor(path):
    """静默段法: silencedetect 找最长静默段测 RMS。准确但有静默段才有效。"""
    err = run_ffmpeg_stderr(["-i", path, "-af", "silencedetect=noise=-45dB:d=0.3", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", err)]
    segs = [(s, e) for s, e in zip(starts, ends) if e - s >= 0.3]
    if not segs:
        return None
    s, e = max(segs, key=lambda x: x[1] - x[0])
    err2 = run_ffmpeg_stderr(["-ss", str(s), "-to", str(e), "-i", path, "-af", "astats", "-f", "null", "-"])
    lv = re.findall(r"RMS level dB:\s*(-?[\d.]+)", err2)
    return float(lv[-1]) if lv else None


def _percentile_floor(path):
    """兜底法: 读原始样本, 分帧算 RMS 取低百分位近似噪声地板。
    用于连续语音/重噪无静默段。ffmpeg 直出 f32le 单声道, 规避 astats metadata 不可靠与 24bit 读取问题。"""
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-t", "60", "-i", path,
                        "-af", "aresample=48000,aformat=channel_layouts=mono",
                        "-f", "f32le", "-"], capture_output=True)
    if p.returncode != 0 or not p.stdout:
        return None
    a = array.array("f")
    a.frombytes(p.stdout)
    if len(a) < 4800:
        return None
    win = 4800  # 100ms @ 48k
    rms = []
    for i in range(0, len(a) - win, win):
        s = a[i:i + win]
        e = math.fsum(x * x for x in s) / len(s)
        if e > 0:
            rms.append(10 * math.log10(e))
    if len(rms) < 5:
        return None
    rms.sort()
    p10 = rms[int(len(rms) * 0.10)]
    p50 = rms[int(len(rms) * 0.50)]
    # 只有"内容电平明显高于静默段(差>15dB)"时, p10 才是真噪声地板;
    # 否则是连续素材(纯音/连续语音)本身, 无可分噪声地板 -> 返回 None 不误杀
    if (p50 - p10) < 15:
        return None
    return round(p10, 1)  # 10th percentile 近似噪声地板


def measure_noise_floor(path):
    """返回 (value, method)。method: 'silence'|'approx'|None。"""
    nf = _silence_floor(path)
    if nf is not None:
        return nf, "silence"
    p = _percentile_floor(path)
    if p is not None:
        return p, "approx"
    return None, None


def measure_all(path):
    lufs, tp, lra = measure_loudnorm(path)
    st = measure_astats(path)
    nf, nf_method = measure_noise_floor(path)
    return {
        "lufs": lufs, "tp": tp, "lra": lra,
        "peak": st["peak_level"], "flat": st["flat_factor"], "peak_count": st["peak_count"],
        "noise_floor": nf, "noise_method": nf_method,
    }


def _rms_from_f32le(stdout):
    a = array.array("f")
    a.frombytes(stdout)
    if len(a) < 1:
        return 0.0
    return math.sqrt(math.fsum(x * x for x in a) / len(a))


def _sibilance_db(path):
    """5–10kHz 频段能量相对全频的 dB 比。越高越"齿音重"。失败返回 None。机器仅提示, 不自动修。"""
    try:
        overall = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", path,
                                  "-af", "aformat=channel_layouts=mono", "-f", "f32le", "-"],
                                 capture_output=True)
        band = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", path,
                               "-af", "aformat=channel_layouts=mono,bandpass=f=7500:width_type=h:width=2500",
                               "-f", "f32le", "-"], capture_output=True)
        o = _rms_from_f32le(overall.stdout)
        b = _rms_from_f32le(band.stdout)
        if o <= 0:
            return None
        return 20.0 * math.log10(b / o)
    except Exception:
        return None


def _severe_clipping(m):
    """大面积平顶/近方波(FlatFactor 极高) -> adeclip 易 OOM(137), 应跳过并提示重录。"""
    return (m["flat"] > 0.9) and (m["peak"] > -10.0)


def diagnose(path):
    m = measure_all(path)
    clipping = m["tp"] >= 0.0 or m["flat"] > 0.5
    # 分帧兜底法已用 gap 检查(内容电平明显高于静默段才认作噪声地板), 故同样用 -60 标准
    noise = m["noise_floor"] is not None and m["noise_floor"] > -60.0
    issues = {"clipping": clipping, "noise": noise, "noise_floor": m["noise_floor"]}
    print(f"== 诊断: {path}")
    print(f"  LUFS={m['lufs']:.2f}  TP={m['tp']:.2f} dBTP  LRA={m['lra']:.2f}")
    print(f"  Peak={m['peak']:.2f} dB  FlatFactor={m['flat']:.3f}  PeakCount={m['peak_count']:.0f}")
    nf_s = f"{m['noise_floor']:.1f} dBFS" if m["noise_floor"] is not None else "未检出"
    method_s = {"silence": "(静默段法)", "approx": "(分帧兜底·近似)", None: ""}.get(m["noise_method"], "")
    print(f"  噪声地板={nf_s} {method_s}")
    print(f"  -> 削波: {'是, 需 adeclip' if clipping else '否'}")
    print(f"  -> 底噪: {'超标, 需降噪' if noise else '达标或未测出'}")
    print(f"  -> 齿音: 机器不代听, 需人工试听 / songsee 频谱 5-10kHz")
    sib = _sibilance_db(path)
    if sib is not None:
        tag = "偏高, 建议试 --deess 0.10~0.15" if sib > -12 else "正常"
        print(f"  -> 齿音频谱提示: 5–10kHz 占比 {sib:+.1f}dB ({tag}, 仅提示不自动修)")
    print(f"  -> 喷麦/口水音: 看 Peak/Flat 与试听爆破瞬间, 可加 --declick(adeclick)")
    return issues, m


# ---------- 处理 ----------

def build_chain(declip=False, declick=False, hpf=True, denoise=None,
                deess=None, arnndn=None, limiter=True):
    """denoise: (nr, nf) | arnndn: 模型路径 | deess: intensity。顺序固定。"""
    chain = []
    if declip:
        chain.append("adeclip")
    if declick:
        chain.append("adeclick")
    if hpf:
        chain.append("highpass=f=80:p=2")
    if arnndn:
        chain.append(f"arnndn=m={arnndn}")
    elif denoise:
        nr, nf = denoise
        chain.append(f"afftdn=nr={nr}:nf={nf}:tn=1")
    if deess is not None:
        chain.append(f"deesser=i={deess}:m=0.5:f=0.5")
    if limiter:
        chain.append("alimiter=limit=0dB:level=false")
    return ",".join(chain)


def process(src, dst, chain, gain_db=None, fade_ms=0):
    af = (f"volume={gain_db}dB," if gain_db is not None else "") + chain
    if fade_ms > 0:
        d = get_duration(src)
        f = fade_ms / 1000.0
        fd = max(0.001, d - f)
        af += f",afade=t=in:d={f:.3f},afade=t=out:st={fd:.3f}:d={f:.3f}"
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", src, "-af", af,
                        "-acodec", "pcm_s24le", "-ar", "48000", dst],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败 (code {p.returncode}):\n{p.stderr[-800:]}")


def match_lufs(src, dst, chain, target, max_iter=6, tol=0.03, fade_ms=0):
    """修复链只跑一次得中间文件, 再对中间文件迭代纯 volume 命中目标 LUFS。
    (修复滤波器不可重复跑: 慢且增益变化会改变其侦测行为;
     迭代阶段不挂 alimiter 以免吃增益阻碍收敛, 仅最终输出接一次防削波)"""
    g = 0.0
    best_g = 0.0
    best_diff = None
    with tempfile.TemporaryDirectory() as td:
        mid = os.path.join(td, "repaired.wav")
        repair = ",".join(f for f in chain.split(",") if not f.startswith("alimiter"))
        process(src, mid, repair or "anull")
        for i in range(max_iter):
            tmp = os.path.join(td, "it.wav")
            process(mid, tmp, "anull", gain_db=round(g, 3))
            m, _, _ = measure_loudnorm(tmp)
            diff = target - m
            print(f"  iter{i}: G={g:+.2f}dB -> {m:.2f} LUFS (diff {diff:+.2f})")
            if best_diff is None or abs(diff) < abs(best_diff):
                best_diff, best_g = diff, g
            if abs(diff) < tol:
                break
            g += diff
        process(mid, dst, "alimiter=limit=0dB:level=false", gain_db=round(best_g, 3), fade_ms=fade_ms)
    m, tp, _ = measure_loudnorm(dst)
    print(f"  最终: {m:.2f} LUFS (目标 {target}, 偏差 {m-target:+.3f})  TP={tp:.2f}")


def verify(src, dst):
    m0 = measure_all(src)
    m1 = measure_all(dst)
    print("== 验收对比 (处理前 → 后)")
    print(f"  LUFS:      {m0['lufs']:.2f} → {m1['lufs']:.2f}  ({m1['lufs']-m0['lufs']:+.2f})")
    print(f"  TP:        {m0['tp']:.2f} → {m1['tp']:.2f} dBTP  (应 ≤ 0)")
    nf0 = f"{m0['noise_floor']:.1f}" if m0['noise_floor'] is not None else "n/a"
    nf1 = f"{m1['noise_floor']:.1f}" if m1['noise_floor'] is not None else "n/a"
    print(f"  噪声地板:  {nf0} → {nf1} dBFS  (应下降)")
    print(f"  FlatFactor:{m0['flat']:.3f} → {m1['flat']:.3f}  (削波应趋 0)")
    ok = (m1["tp"] <= 0.0) and (m0["noise_floor"] is None or m1["noise_floor"] <= m0["noise_floor"] + 0.5)
    print(f"  结论: {'PASS ✅' if ok else '⚠️ 需复查 (TP 仍>0 或底噪未降)'}")
    return ok


def _parse_flags(args):
    """提取通用 flag, 返回 (dict, errors)。"""
    o = {"declip": False, "declick": False, "hpf": True, "denoise": None,
         "deess": None, "arnndn": None, "target": None, "fade": 0}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--declip":
            o["declip"] = True
        elif a == "--declick":
            o["declick"] = True
        elif a == "--hpf":
            o["hpf"] = True
        elif a == "--no-hpf":
            o["hpf"] = False
        elif a == "--denoise":
            o["denoise"] = (float(args[i + 1]), float(args[i + 2])); i += 2
        elif a == "--arnndn":
            o["arnndn"] = args[i + 1]; i += 1
        elif a == "--deess":
            o["deess"] = float(args[i + 1]); i += 1
        elif a == "--target-lufs":
            o["target"] = float(args[i + 1]); i += 1
        elif a == "--fade":
            o["fade"] = int(args[i + 1]); i += 1
        i += 1
    return o


def auto_process(src, dst, opts):
    """最小干预自动修复: 只修机器可判定项(削波/底噪); 齿音不自动修。返回所用 chain。"""
    m = measure_all(src)
    clipping = m["tp"] >= 0.0 or m["flat"] > 0.5
    noise = m["noise_floor"] is not None and m["noise_floor"] > -60.0
    declip = clipping
    if declip and _severe_clipping(m):
        print(f"  ⚠️ 大面积平顶削波 (FlatFactor={m['flat']:.3f}) -> adeclip 跳过(防OOM, 建议重录)")
        declip = False
    denoise = None
    if noise:
        nf_meas = m["noise_floor"] if m["noise_floor"] is not None else -50
        nf_param = round(min(max(nf_meas, -80), -20))   # afftdn nf 合法区间 [-80,-20]
        nr = 12 if nf_meas <= -45 else 15               # 重噪加码
        denoise = (nr, nf_param)
    chain = build_chain(declip=declip, declick=opts["declick"], hpf=opts["hpf"],
                        denoise=denoise, deess=opts["deess"],
                        arnndn=opts["arnndn"], limiter=True)
    print(f"== 自动链路(最小干预): {chain}")
    fade_ms = opts["fade"] or 5   # auto 默认起止 5ms fade, 防处理链引入瞬态
    if opts["target"] is not None:
        match_lufs(src, dst, chain, opts["target"], fade_ms=fade_ms)
    else:
        process(src, dst, chain, fade_ms=fade_ms)
    return chain


_VO_EXTS = (".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".ape", ".m4a")


def report(folder, csv_path=None):
    """文件夹验收扫描: 逐文件客观测量 + 标记(削波/底噪), 可选导出 CSV。"""
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(_VO_EXTS))
    if not files:
        print(f"(无音频文件: {folder})")
        return
    print(f"== VO 验收扫描: {folder}  ({len(files)} 个文件) ==")
    print(f"{'文件':42} {'LUFS':>8} {'TP':>8} {'噪声地板':>10} {'标记':>14}")
    rows = []
    for f in files:
        p = os.path.join(folder, f)
        try:
            m = measure_all(p)
        except Exception as e:
            print(f"{f[:42]:42} {'ERR':>8} {'':>8} {'':>10} {str(e)[:14]:>14}")
            continue
        clip = m["tp"] >= 0.0 or m["flat"] > 0.5
        noise = m["noise_floor"] is not None and m["noise_floor"] > -60.0
        flags = []
        if clip:
            flags.append("削波")
        if noise:
            flags.append("底噪")
        tag = " ".join(flags) if flags else "OK"
        nf = f"{m['noise_floor']:.1f}" if m["noise_floor"] is not None else "n/a"
        lufs_s = "n/a" if m["lufs"] in (float("inf"), float("-inf")) else f"{m['lufs']:.1f}"
        print(f"{f[:42]:42} {lufs_s:>8} {m['tp']:>8.2f} {nf:>10} {tag:>14}")
        rows.append((f, lufs_s, f"{m['tp']:.2f}", nf, tag))
    if csv_path:
        import csv
        with open(csv_path, "w", newline="") as cf:
            w = csv.writer(cf)
            w.writerow(["file", "lufs", "tp_db", "noise_floor_db", "flag"])
            for r in rows:
                w.writerow(r)
        print(f"\nCSV -> {csv_path}")


def batch(src_dir, dst_dir, opts):
    """批量自动修复: 文件夹内每首 VO 跑 auto_process, 输出到 dst_dir, 并写 _batch_report.csv。"""
    os.makedirs(dst_dir, exist_ok=True)
    files = sorted(f for f in os.listdir(src_dir) if f.lower().endswith(_VO_EXTS))
    if not files:
        print(f"(无音频文件: {src_dir})")
        return
    print(f"== 批量自动修复: {src_dir} -> {dst_dir}  ({len(files)} 个) ==")
    import csv
    csv_path = os.path.join(dst_dir, "_batch_report.csv")
    with open(csv_path, "w", newline="") as cf:
        w = csv.writer(cf)
        w.writerow(["source", "output", "chain", "lufs_out", "tp_out"])
        for f in files:
            sp = os.path.join(src_dir, f)
            dp = os.path.join(dst_dir, os.path.splitext(f)[0] + ".wav")
            try:
                chain = auto_process(sp, dp, opts)
                lufs, tp, _ = measure_loudnorm(dp)
                w.writerow([f, os.path.basename(dp), chain, f"{lufs:.2f}", f"{tp:.2f}"])
                print(f"  ✓ {f} -> {os.path.basename(dp)}  ({lufs:.2f} LUFS, {tp:.2f} dBTP)")
            except Exception as e:
                w.writerow([f, os.path.basename(dp), f"ERROR: {e}", "", ""])
                print(f"  ✗ {f}: {e}")
    print(f"\n批量报告 -> {csv_path}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]

    if cmd == "diagnose":
        if len(sys.argv) < 3:
            print("diagnose 需要 <vo.wav>"); sys.exit(1)
        diagnose(sys.argv[2])
        return

    if cmd == "verify":
        if len(sys.argv) < 4:
            print("verify 需要 <raw.wav> <clean.wav>"); sys.exit(1)
        verify(sys.argv[2], sys.argv[3])
        return

    if cmd == "report":
        if len(sys.argv) < 3:
            print("report 需要 <folder> [--csv out.csv]"); sys.exit(1)
        folder = sys.argv[2]
        csvp = None
        if "--csv" in sys.argv[3:]:
            i = sys.argv.index("--csv")
            csvp = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        report(folder, csvp)
        return

    if cmd == "batch":
        if len(sys.argv) < 4:
            print("batch 需要 <src_dir> <dst_dir> [flags...]"); sys.exit(1)
        src_dir, dst_dir = sys.argv[2], sys.argv[3]
        opts = _parse_flags(sys.argv[4:])
        batch(src_dir, dst_dir, opts)
        return

    # auto / process 需要 src + dst
    if len(sys.argv) < 4:
        print(__doc__); sys.exit(1)
    src, dst = sys.argv[2], sys.argv[3]
    opts = _parse_flags(sys.argv[4:])

    if cmd == "auto":
        auto_process(src, dst, opts)
    elif cmd == "process":
        declip = opts["declip"]
        if declip:
            m0 = measure_all(src)
            if _severe_clipping(m0):
                print(f"  ⚠️ 大面积平顶削波 (FlatFactor={m0['flat']:.3f}), adeclip 易 OOM, 已跳过 adeclip(建议重录)")
                declip = False
        chain = build_chain(declip=declip, declick=opts["declick"], hpf=opts["hpf"],
                            denoise=opts["denoise"], deess=opts["deess"],
                            arnndn=opts["arnndn"], limiter=True)
        print(f"== 链路: {chain}")
        if opts["target"] is not None:
            match_lufs(src, dst, chain, opts["target"], fade_ms=opts["fade"])
        else:
            process(src, dst, chain, fade_ms=opts["fade"])
    else:
        print(__doc__); sys.exit(1)

    lufs, tp, lra = measure_loudnorm(dst)
    print(f"== 输出: {dst}\n  LUFS={lufs:.2f}  TP={tp:.2f}  LRA={lra:.2f}")
    print("  请务必试听三处高危点: 句尾气声 / 密集s音 / 静默段; 喷麦句首爆破音")


if __name__ == "__main__":
    main()
