#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音乐音效混音 - 标准逐轨工作流参考实现 (Step1 匹配 + Step2 混音 + Step3 分轨)
==================== 用法 ====================
1. 改下方 CONFIG / TASKS
2. 跑 Step1:  python standard_workflow.py step1
3. 跑 Step2:  python standard_workflow.py step2   -> 出 Mix Preview 给用户听
4. 用户确认后: python standard_workflow.py step3  -> 导出分轨
每条命令可单独跑；或 python standard_workflow.py all 一次跑完(仅在你已确认平衡时)

经验固化:
- 匹配: 对原始文件迭代 volume 总增益, 逼近参考 LUFS; 仅最终输出且真峰>0 时接 alimiter limit=0dB 防削波(收敛阶段不加, 否则吃增益)
- 混音: MUS -1.5dB, SFX HPF80 + 增益; amix normalize=0; 预览 loudnorm -16/-1
- 分轨: 各自按 mix 增益导出, 不做整体归一化
- 永远 -ac 2 (stereo), pcm_s24le 48k
"""
import os
import sys
import json
import subprocess

# ============================== CONFIG ==============================
SRC_BASE = r"C:\Users\<用户名>\Desktop\展示对比混音"      # 素材根目录
OUT_BASE = r"C:\Users\<用户名>\WorkBuddy\<任务目录>\输出"   # Step1 匹配结果
MIX_BASE = r"C:\Users\<用户名>\WorkBuddy\<任务目录>\混音输出" # Step2 预览
STEM_BASE = r"C:\Users\<用户名>\WorkBuddy\<任务目录>\最终分轨" # Step3 分轨
BATCH = "batchXX"

# 每个素材组: 被调整版本匹配到参考版本的 MUS / SFX
# mus_gain / sfx_gain 是 Step2 混音增益(相对匹配后文件), 按平衡反馈调
TASKS = [
    {
        "group": "素材名",
        "ref_mus": "RefVer/MUS_file.wav",
        "ref_sfx": "RefVer/SFX_file.wav",
        "tgt_mus": "TgtVer/MUS_file.wav",
        "tgt_sfx": "TgtVer/SFX_file.wav",
        "mus_gain": -1.5,
        "sfx_gain": -1.5,
    },
]


# ============================== 工具 ==============================
def measure_lufs(fp):
    cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-i', fp,
           '-af', 'loudnorm=print_format=json', '-f', 'null', '-']
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    out = r.stderr + r.stdout
    js = out.rfind('{'); je = out.rfind('}') + 1
    if js != -1 and je > js:
        try:
            d = json.loads(out[js:je])
            return float(d['input_i']), float(d.get('input_tp', 0.0))
        except Exception:
            pass
    return None, None


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True,
                      encoding='utf-8', errors='replace')
    if r.returncode != 0:
        print("  FFMPEG ERR:\n" + r.stderr[-1500:])
    return r.returncode == 0


# ============================== Step 1 ==============================
def match_file(src, ref_lufs, out, max_iter=20):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # 先测源真峰, 决定是否最终防削波(与 voice_cleanup.match_lufs 同思路)
    _, src_tp = measure_lufs(src)
    g = 0.0
    best_g, best_diff, best_tmp = 0.0, 999.0, None
    for i in range(max_iter):
        tmp = out + f".iter{i}.wav"
        # 收敛阶段只施加总增益 volume, 不加常驻 alimiter
        # (常驻 alimiter 会吃增益, 导致 ~0.45 LUFS 偏差, 匹配无法收敛)
        af = f"volume={g:+.3f}dB"
        if run(['ffmpeg', '-hide_banner', '-y', '-i', src, '-af', af,
                '-acodec', 'pcm_s24le', '-ar', '48000', '-ac', '2', tmp]):
            m, _ = measure_lufs(tmp)
            if m is None:
                break
            diff = ref_lufs - m
            if abs(diff) < abs(best_diff):
                best_diff, best_g, best_tmp = diff, g, tmp
            else:
                try: os.remove(tmp)
                except: pass
                break
            if abs(diff) < 0.03:
                break
            g += diff
    if best_tmp is None:
        return None
    # 清理其他 iter, 保留 best
    for f in os.listdir(os.path.dirname(out)):
        if f.startswith(os.path.basename(out) + ".iter") and \
           os.path.join(os.path.dirname(out), f) != best_tmp:
            try: os.remove(os.path.join(os.path.dirname(out), f))
            except: pass
    # 最终输出: 仅在源真峰>0 时接一次 alimiter 防削波, 收敛阶段不加
    if src_tp is not None and src_tp > 0:
        af = f"volume={best_g:+.3f}dB,alimiter=limit=0dB:level=false"
    else:
        af = f"volume={best_g:+.3f}dB"
    run(['ffmpeg', '-hide_banner', '-y', '-i', best_tmp, '-af', af,
         '-acodec', 'pcm_s24le', '-ar', '48000', '-ac', '2', out])
    try: os.remove(best_tmp)
    except: pass
    final, _ = measure_lufs(out)
    return final


def step1():
    print("=== Step 1: 响度匹配 ===")
    for t in TASKS:
        print(f"\n--- {t['group']} ---")
        rm, _ = measure_lufs(os.path.join(SRC_BASE, t['ref_mus']))
        rs, _ = measure_lufs(os.path.join(SRC_BASE, t['ref_sfx']))
        print(f"  参考 MUS={rm:.2f}  SFX={rs:.2f}")
        fm = match_file(os.path.join(SRC_BASE, t['tgt_mus']), rm,
                        os.path.join(OUT_BASE, BATCH, t['group'],
                                     os.path.basename(t['tgt_mus'])))
        fs = match_file(os.path.join(SRC_BASE, t['tgt_sfx']), rs,
                        os.path.join(OUT_BASE, BATCH, t['group'],
                                     os.path.basename(t['tgt_sfx'])))
        print(f"  结果 MUS={fm:.2f}(diff {fm-rm:+.2f})  SFX={fs:.2f}(diff {fs-rs:+.2f})")


# ============================== Step 2 ==============================
def step2():
    print("=== Step 2: 混音预览 ===")
    for t in TASKS:
        out = os.path.join(MIX_BASE, BATCH, t['group'], f"{t['group']}_Mix_Preview.wav")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        mus = os.path.join(OUT_BASE, BATCH, t['group'], os.path.basename(t['tgt_mus']))
        sfx = os.path.join(OUT_BASE, BATCH, t['group'], os.path.basename(t['tgt_sfx']))
        af = (f"[0:a]volume={t['mus_gain']:+.1f}dB[mus];"
              f"[1:a]highpass=f=80:p=2,volume={t['sfx_gain']:+.1f}dB[sfx];"
              f"[mus][sfx]amix=inputs=2:duration=longest:normalize=0[mix];"
              f"[mix]loudnorm=I=-16:TP=-1.0:LRA=11[out]")
        run(['ffmpeg', '-hide_banner', '-y', '-i', mus, '-i', sfx,
             '-filter_complex', af, '-map', '[out]',
             '-acodec', 'pcm_s24le', '-ar', '48000', '-ac', '2', out])
        print(f"  {out}")


# ============================== Step 3 ==============================
def step3():
    print("=== Step 3: 导出分轨 ===")
    for t in TASKS:
        d = os.path.join(STEM_BASE, BATCH, t['group'])
        os.makedirs(d, exist_ok=True)
        mus_in = os.path.join(OUT_BASE, BATCH, t['group'], os.path.basename(t['tgt_mus']))
        sfx_in = os.path.join(OUT_BASE, BATCH, t['group'], os.path.basename(t['tgt_sfx']))
        mus_out = os.path.join(d, os.path.basename(t['tgt_mus']))
        sfx_out = os.path.join(d, os.path.basename(t['tgt_sfx']))
        run(['ffmpeg', '-hide_banner', '-y', '-i', mus_in,
             '-af', f"volume={t['mus_gain']:+.1f}dB",
             '-acodec', 'pcm_s24le', '-ar', '48000', '-ac', '2', mus_out])
        run(['ffmpeg', '-hide_banner', '-y', '-i', sfx_in,
             '-af', f"highpass=f=80:p=2,volume={t['sfx_gain']:+.1f}dB",
             '-acodec', 'pcm_s24le', '-ar', '48000', '-ac', '2', sfx_out])
        print(f"  {mus_out}\n  {sfx_out}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    {"step1": step1, "step2": step2, "step3": step3,
     "all": lambda: (step1(), step2(), step3())}.get(arg, step1)()
