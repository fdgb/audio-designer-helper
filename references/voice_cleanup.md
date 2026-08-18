# VO Cleanup — 语音处理工作流（齿音 / 削波 / 底噪）

> 适用对象：配音 (VO)、对白、口播稿录音、游戏语音包。
> 与主工作流的关系：这是独立的「语音修复」模块，可单独触发；修完的干净语音也可回到主工作流按
> 「语音 = 音乐基准 -2dB」进混音。

## 一、诊断先行（先测再修，绝不盲修）

对每个语音文件先跑三项客观测量，决定要修什么，不要一上来全链路怼上去：

```bash
# 1) 响度 + 真峰
ffmpeg -hide_banner -nostdin -i "<vo.wav>" -af "loudnorm=print_format=json" -f null -
# 取 input_i (LUFS) / input_tp (dBTP)

# 2) 削波与统计（Peak level / Flat factor / Peak count）
ffmpeg -hide_banner -nostdin -i "<vo.wav>" -af "astats=metadata=1:measure_perchannel=Peak_level+Flat_factor+Peak_count:measure_overall=none" -f null -
# 简化版：直接 astats 全量输出，看 Flat factor 与 Peak count

# 3) 底噪水平：取无语音段落测 RMS（先用 silencedetect 找静默段）
ffmpeg -hide_banner -nostdin -i "<vo.wav>" -af "silencedetect=noise=-45dB:d=0.3" -f null -
# 对检测到的静默段单独 astats，得到噪声地板 (noise floor) dBFS
```

**判定标准**：

| 问题 | 判定 | 修复手段 |
|------|------|----------|
| 削波 | `input_tp >= 0 dBTP` 或 astats `Flat factor > 0` / `Peak count` 明显偏高 | `adeclip` |
| 喷麦/爆破音/口水音 | 试听 b/p/t/k 句首有"啪"；astats `Peak count` 高、`Flat factor` 近 0 但瞬态尖 | `adeclick`（脉冲修复，highpass 去不掉）|
| 底噪 | 噪声地板 > **-60 dBFS**（游戏语音包一般要求 ≤ -60，广播级 ≤ -65） | `afftdn`（首选）/ `arnndn`（重噪）|
| 齿音 | 试听 s/z/ts 刺耳；频谱图 5–10kHz 有明显尖峰能量条 | `deesser`（**机器不可判定，需人工**）|
| 低频轰鸣/近讲 | 频谱图 <100Hz 有能量团；试听有闷响 | `highpass=f=80` |

> 底噪测量有两档:**静默段法**（有静默段时准确）/ **分帧兜底法**（连续语音无静默段时取 RMS 低百分位近似，标 `approx`）。重噪无静默段也能测出，自动链路不会漏降噪。
> 频谱图可用 songsee skill 生成，处理前后各出一张做 A/B 对照，比纯耳听更有说服力。

## 二、处理顺序（顺序错了会互相污染，必须遵守）

```
1. adeclip   修削波        ← 最先。削波失真是"源头污染"，不先修会被后级当成齿音/噪声处理
2. adeclick  修喷麦/口水音  ← 去除 b/p/t/k 爆破音、弹舌咔哒等脉冲瞬态；与 adeclip 同属脉冲修复家族
3. highpass  去低频        ← f=80，清掉轰鸣/桌面震动/近讲低频堆积
4. afftdn    降底噪        ← 在任何动态处理之前。若后面要提增益/压缩，底噪会被一起放大
   (重噪可换 arnndn=m=模型.rnnn，见 §3.3)
5. deesser   去齿音        ← 在降噪之后。噪声里的高频嘶声会干扰 deesser 的侦测
   ⚠️ 齿音机器不可判定, auto 模式默认不修, 需 --deess 显式开启或人工试听
6. (可选) acompressor  轻压控制动态（语音 LRA 目标 4–8dB）
7. (可选) 响度对齐     两段式 loudnorm 或迭代 volume 命中目标 LUFS
8. (建议) afade 起止 5ms  ← auto 默认加, 消除处理链引入的起止咔哒/削波瞬态
```

## 三、各滤波器参数与调法

### 1. adeclip — 削波修复
```bash
ffmpeg -hide_banner -y -i in.wav -af "adeclip" -acodec pcm_s24le out.wav
```
- 默认参数对轻中度削波已足够；重度削波（大面积平顶）可加 `adeclip=arorder=12:threshold=8`。
- 修复后必须再测一次 TP：修复展开的波形峰值可能 > 0，需接 `alimiter=limit=0dB:level=false` 或整体 -1~-2dB 留 headroom。
- 只在确认削波时使用，无削波文件跑它纯属浪费且有微小音染风险。

### 2. adeclick — 喷麦 / 爆破音 / 口水音（脉冲瞬态）
```bash
ffmpeg -hide_banner -y -i in.wav -af "adeclick" -acodec pcm_s24le out.wav
# 重脉冲(连续喷麦)可加: adeclick=window=80:overlap=90:threshold=4
```
- 专修 **b/p/t/k 爆破音、弹舌咔哒、录音接口瞬态噪声**——这些是宽带脉冲，highpass 去不掉（高通只管低频），必须靠 adeclick 的脉冲侦测。
- 默认参数（window=55, threshold=2）对偶发喷麦足够；连续重喷麦把 `threshold` 提到 4、`window` 提到 80。
- **轻量安全**：adeclick 只动"像脉冲"的瞬态，对正常语音几乎无音染，可放心放进自动链路（auto 已默认开启 `--declick` 仅当显式 `--declick`，诊断阶段靠 Peak/Flat 与试听判断）。
- 与 adeclip 区别：adeclip 救"平顶削波"，adeclick 救"尖刺脉冲"，两者互补，顺序是先 clip 后 click。

### 3. afftdn — FFT 降噪（首选，温和）
```bash
# 自动追踪噪声地板（适合噪声平稳的房间底噪/电流声）
ffmpeg -hide_banner -y -i in.wav -af "afftdn=nr=12:nf=-40:tn=1" -acodec pcm_s24le out.wav
```
- `nr`（降噪量 dB）：**从 10–12 起步，绝不上来就 20+**。过猛 → 水声/金属声 artifact、咬字尾音被啃。
- `nf`（噪声地板估计）：填诊断阶段实测的噪声地板值附近（如实测 -52 就填 -50 ~ -55）。
- `tn=1` 开噪声追踪，适合底噪缓变的素材。
- 修完听三处：句尾气声、s 音、静默段——这三处最容易暴露降噪 artifact。

### 4. arnndn — RNN 降噪（需模型文件，已内置 `models/cb.rnnn`）
```bash
# 技能已自带通用模型 models/cb.rnnn，直接用 --arnndn 传路径即可：
python scripts/voice_cleanup.py process in.wav out.wav --arnndn models/cb.rnnn --deess 0.15
# 或裸 ffmpeg：
ffmpeg -hide_banner -y -i in.wav -af "arnndn=m=models/cb.rnnn" -acodec pcm_s24le out.wav
```
- 模型来源：GregorR/rnnoise-models（本 skill 内置 `cb.rnnn`=conjoined-burgers 通用型；如需广播/对话场景可换 `bd.rnnn`）。若缺失，从 `https://raw.githubusercontent.com/GregorR/rnndn-models/master/conjoined-burgers-2018-08-28/cb.rnnn` 下载（需联网，沙箱外）。
- 比 afftdn 强得多，专为语音训练；但对**气声/呼吸类非稳态噪声无效**（它正确判断"这不是可减的稳态底噪"就压 0 dB）。仅对空调/风扇/街道等稳态底噪有效。
- 实测：一条干净录音（稳态底噪已 ~−79 dBFS）上 arnndn 几乎零作用，版本差异主要来自 LUFS 对齐增益。

### 5. deesser — 去齿音
```bash
# 第一步：监听模式，只听被削掉的齿音成分，确认没伤到正常辅音
ffmpeg -hide_banner -i in.wav -af "deesser=i=0.15:s=e" -f wav - | ffplay -autoexit -nodisp -i - 2>/dev/null
# 第二步：正式输出
ffmpeg -hide_banner -y -i in.wav -af "deesser=i=0.15:m=0.5:f=0.5" -acodec pcm_s24le out.wav
```
- `i`（强度 0–1）：**0.1–0.2 起步**，女声/明亮麦克风可到 0.25；超过 0.3 基本必然"大舌头"（咬字发闷、s 变 th）。
- `m`（最大削减量 0–1）：0.5 默认即可，防止单点削过头。
- `f`（作用频率系数 0–1）：默认 0.5 约对应 5–8kHz 齿音区；齿音偏高（女声 8–10kHz）可适当调高。
- `s=e` 是关键调参技巧：先单听"被删掉的东西"，里面应该只有嘶嘶声，若听到完整辅音说明 `i` 过大。

### 6. 动态与响度（遵守主工作流铁律）
- **禁 dynaudnorm；speechnorm 同样属于自动增益骑乘，默认不用**——与主工作流"绝不自动闪避/自动推增益"一脉相承。
- 需要控制动态：`acompressor=threshold=-18dB:ratio=2.5:attack=15:release=200:makeup=2`（轻压，语音 LRA 目标 4–8dB）。
- 需要命中目标响度：沿用主工作流的**迭代 volume 匹配算法**或两段式 loudnorm（见 SKILL.md Step 1），游戏语音目标 = 音乐基准 -2dB。

## 四、完整链路模板（全问题素材）

```bash
# 诊断确认：削波 + 喷麦 + 底噪 -50dBFS + 齿音刺耳（全问题素材）
ffmpeg -hide_banner -y -i "VO_raw.wav" \
  -af "adeclip,adeclick,highpass=f=80:p=2,afftdn=nr=12:nf=-48:tn=1,deesser=i=0.15:m=0.5:f=0.5,alimiter=limit=0dB:level=false,afade=t=in:d=0.005,afade=t=out:st=ST:d=0.005" \
  -acodec pcm_s24le -ar 48000 "VO_clean.wav"
# 注: ST = 时长-0.005 (秒); afade 仅消除链尾瞬态, 不改变时长
```
> 实际按诊断结果**裁剪链路**：没削波就去掉 adeclip，无喷麦去掉 adeclick，底噪达标就去掉 afftdn，齿音不确认就不加 deesser。最小干预原则同样适用于语音——**auto 模式正是这么做的**（齿音不机判故默认不加）。

## 五、声道规则（语音特例，与主工作流不同）

- **VO 跟随源声道数**：游戏语音源常见 mono，保持 mono 即可（引擎里做 2D/3D 定位），**不强制 -ac 2**。
- 主工作流的"永远 stereo"铁律只约束 MUS/SFX 分轨，不适用于 VO。
- 命令里**不写 -ac 参数**即保持源声道数。

## 六、验证（每次必做）

**脚本一键验收**（推荐，客观对比处理前后）：
```bash
python scripts/voice_cleanup.py verify VO_raw.wav VO_clean.wav
# 输出 LUFS/TP/噪声地板/FlatFactor 的 前→后 对照 + PASS/⚠️ 结论
```

1. 处理前后各测一次：LUFS / TP / 静默段噪声地板，三个数字写进交付说明。
2. `ffprobe` 确认声道数与源一致、48kHz、24bit、时长不变。
3. songsee 出处理前后频谱对比图（齿音区 5–10kHz、底噪地板肉眼可见差异）。
4. 试听三处高危点：句尾气声、密集 s 音句、静默段。
5. `present_files` 交付 clean 版 + 对比图给用户确认。

## 七、文件夹级批量命令（report / batch）

单次 `diagnose`/`auto` 针对单文件；交付前常用"整文件夹批量验收 + 批量修复"。

### report — 文件夹验收扫描
逐文件测 LUFS / TP / 噪声地板，标记 `削波`(TP≥0 或 Flat>0.5) / `底噪`(噪声地板>-60dBFS) / `OK`，可选导出 CSV：
```bash
python scripts/voice_cleanup.py report VO_folder/ --csv VO_report.csv
# 输出列: file, lufs, tp_db, noise_floor_db, flag
```
- 用途：交付前一眼看整批语音健康度，挑出需重录/重磨的条目；CSV 可直接进表格筛选（如筛 `flag≠OK`）。
- 噪声地板 > -60dBFS 判为底噪（游戏语音包一般要求 ≤ -60，广播级 ≤ -65）。

### batch — 文件夹批量自动修复
对 `src_dir` 内每首 VO 跑 `auto_process`（最小干预：只修机判项[削波/底噪]，齿音需显式 `--deess`，起止 fade 默认开），输出到 `dst_dir`，并写 `_batch_report.csv`：
```bash
python scripts/voice_cleanup.py batch VO_raw_folder/ VO_clean_folder/ --target-lufs -18
# 输出列: source, output, chain, lufs_out, tp_out
```
- `batch` 接受与 `auto` 相同的 flags：`--declip --declick --denoise --deess --arnndn --target-lufs --fade`。
- 每首独立裁剪链路；大面积平顶削波（FlatFactor>0.9 且 TP>-10dB）自动跳过 adeclip 防 OOM（见 Pitfalls）。
- `_batch_report.csv` 记录每首实际应用的滤波器链 + 输出 LUFS/TP，便于留痕与复验。

### 齿音频谱提示（diagnose 新增）
`diagnose` 额外打印 `5–10kHz 能量占比`（bandpass RMS vs 全频 RMS，dB）：
- `> -12dB` 标"偏高, 建议试 --deess 0.10~0.15"；否则"正常"。
- 这是**提示性**的——机器无法判定齿音，故 `auto`/`batch` 默认不去齿音，需 `--deess` 显式开启 + `s=e` 监听校准。

## Pitfalls（语音专属坑）

| 现象 | 原因 | 解决 |
|------|------|------|
| 人声发闷、s 变 th | deesser `i` 过大 | 降到 0.1–0.2，用 `s=e` 监听校准 |
| 水声/金属声 artifact | afftdn `nr` 过猛 | 降到 10–12；仍不行换 arnndn |
| 句尾气声被啃掉 | 降噪 + gate 叠加过度 | 去掉 agate 或 release 放长到 400ms+ |
| 修完削波反而爆音 | adeclip 展开波形峰值 > 0 | 后接 alimiter 或整体 -1~-2dB |
| 齿音越修越明显 | 顺序错误：先 deess 后降噪 | 严格按 declip→HPF→denoise→deess 顺序 |
| arnndn 报错找不到模型 | .rnnn 模型未下载 | 先确认模型文件存在，或退回 afftdn |
| 语音变成 stereo 双声道 | 误套主工作流 -ac 2 铁律 | VO 跟随源声道数，不写 -ac |
| adeclip 进程被系统杀掉(137/OOM) | 素材大面积平顶（近方波/整段削波），adeclip 试图重建全部样本致内存爆炸 | 大面积削波不是 adeclip 的适用场景——那是需要重录的素材；adeclip 只救局部/间歇性削波 |
| 响度迭代又慢又不收敛 | 每次迭代重跑整条修复链（增益变化会改变 deesser/afftdn 侦测行为） | 修复链只跑一次得中间文件，迭代阶段只对中间文件加纯 volume（见 voice_cleanup.py match_lufs） |
| 句首"啪"的喷麦去不掉 | 只用 highpass（高通只管低频，喷麦是宽带脉冲） | 加 `adeclick`（脉冲修复）；连续重喷麦提 threshold 到 4、window 到 80 |
| 起止有咔哒/瞬态爆点 | adeclip/削波展开在首尾产生瞬态 | 链路尾接 `afade=t=in:d=0.005,afade=t=out:st=ST:d=0.005`（auto 默认加 5ms）|
| auto 修完反而"发闷/齿音被压" | 旧版 auto 强制 deesser=0.15，但齿音机器不可判定，盲修伤辅音 | 新版 auto **默认不去齿音**；确需则 `--deess 0.15` 显式开启，配 `s=e` 监听校准 |
| 重噪素材 auto 没降噪 | 连续语音无静默段，旧版 silencedetect 测不到噪声地板 | 新版加**分帧兜底法**（RMS 低百分位近似），重噪也能测出并降噪 |
