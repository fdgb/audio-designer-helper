# 场景判定树与完整命令示例

开始一个新混音任务时，先确定属于哪种场景，再选对应做法。

## 判定流程
```
用户给了一个素材组文件夹，里面有子文件夹(通常是不同版本)
│
├─ 参考文件夹有 [独立 MUS] + [独立 SFX] 两个文件?
│   │
│   ├─ 是 → 场景 A：标准逐轨匹配  (最常见, batch11/12/13)
│   │
│   └─ 否 → 参考文件夹只有 [1 个合并文件]?
│       │
│       ├─ 是 → 场景 B：合并参考  (示例：单个合并参考文件)
│       │
│       └─ 否 → 看用户具体描述
│
├─ 用户只说"让 Xxx_Idle_SFX 匹配参考 SFX，再和参考 MUS 合" → 场景 C：Idle 音效匹配 (动态混)
│
└─ 用户说"音乐只作氛围，主要表现音效" → 场景 D：音乐作氛围 (示例：技能音效)
```

---

## 场景 A：标准逐轨匹配（最常用）
参考有独立 MUS + SFX，目标也有独立 MUS + SFX。
- Step1：目标 MUS → 参考 MUS LUFS；目标 SFX → 参考 SFX LUFS（各自 <0.10 偏差）
- Step2：MUS `-1.5dB`，SFX `HPF80 + 增益`，amix，预览 loudnorm -16
- Step3：各自按增益导出
见 `scripts/standard_workflow.py`

## 场景 B：合并参考（单个文件）
参考只有 1 个已混好的 wav（如 23.5s / -15.28 LUFS），目标有独立 MUS+SFX。
无法逐轨匹配 → 把目标 MUS+SFX mix 后，整体响度对齐参考 I / TP。

```bash
# 1) 先混(不做整体归一化)
ffmpeg -y -i MUS.wav -i SFX.wav \
  -filter_complex "[0:a]volume=0dB[mus];[1:a]highpass=f=80:p=2,volume=2.5dB[sfx];\
[mus][sfx]amix=inputs=2:duration=longest:normalize=0[mix]" \
  -acodec pcm_s24le -ar 48000 -ac 2 premix.wav
# 2) 测 premix
ffmpeg -i premix.wav -af "loudnorm=print_format=json" -f null -   # 记 input_i/tp/lra/thresh
# 3) 两段式对齐参考 I/TP (默认动态模式, 同时命中 I 与 TP)
ffmpeg -y -i premix.wav -af "loudnorm=I=-15.28:TP=-0.95:LRA=11:\
measured_I=<mi>:measured_TP=<mtp>:measured_LRA=<mlra>:measured_thresh=<mth>:offset=0" \
  -acodec pcm_s24le -ar 48000 -ac 2 out.wav
# ⚠️ 不加 linear=true: linear 模式绕过真峰限制, TP 对齐会失效
```
⚠️ `-af` 不要写 `[out]` 标签。音乐"太小"时：MUS 从 0→+3.5dB，SFX +2.5→+1.0dB。

## 场景 C：Idle 音效匹配（动态混）
音效在播放中位置不固定，需先匹配响度再与参考音乐合。
- Step1：Idle SFX 迭代匹配到参考 SFX 的 LUFS（加 `alimiter=limit=0dB` 防削波，因源/参考常 >0dBTP）
- Step2：音乐不变，匹配后音效 `HPF80`，amix，检查频段不被音乐覆盖也不压过音乐

```bash
# Idle SFX 匹配参考 SFX 命令(单条, G 为迭代求得的总增益)
ffmpeg -y -i Idle_SFX.wav -af "volume=<G>dB,alimiter=limit=0dB:level=false" \
  -acodec pcm_s24le -ar 48000 -ac 2 Idle_SFX_matched.wav
# 混音
ffmpeg -y -i Ref_MUS.wav -i Idle_SFX_matched.wav \
  -filter_complex "[0:a]volume=0dB[m];[1:a]highpass=f=80:p=2[s];\
[m][s]amix=inputs=2:duration=longest:normalize=0" \
  -acodec pcm_s24le -ar 48000 -ac 2 preview.wav
```

## 场景 D：音乐作氛围（音效为主）
音乐不能太背音效盖住，但音效为主。
- MUS：`-5~-8dB` + `highpass=f=60` + 中频轻 scoop（如 1kHz -2dB 防打架）
- SFX：`-1dB` + `HPF80` 保持主体清晰
- 反馈"音乐再大些"：MUS 从 -8dB 提到 -5dB，保留中频 scoop

```bash
ffmpeg -y -i MUS.wav -i SFX.wav \
  -filter_complex "[0:a]highpass=f=60:p=2,equalizer=f=1000:type=peak:width=1.0:gain=-2.0,volume=-5dB[m];\
[1:a]highpass=f=80:p=2,volume=-1dB[s];\
[m][s]amix=inputs=2:duration=longest:normalize=0" \
  -acodec pcm_s24le -ar 48000 -ac 2 preview.wav
```

---

## 通用测量 / 校验命令
```bash
# 测 LUFS + 真峰
ffmpeg -hide_banner -nostdin -i file.wav -af "loudnorm=print_format=json" -f null -
# 验声道/采样率/位深/时长
ffprobe -hide_banner -show_entries format=duration -show_entries stream=channels,sample_rate,bit_depth -of default=noprint_wrappers=1 file.wav
```
