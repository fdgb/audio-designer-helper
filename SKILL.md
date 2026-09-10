---
name: audio-designer-helper
description: >-
  音乐(MUS)+音效(SFX) 响度匹配与融合混音标准工作流。当用户提供含多版本子文件夹的素材目录，
  要求把某一版(Set_X)的音乐音效响度完全匹配到参考版(Set_Y)，再把音乐和音效融合混音
  (双方清晰可辨、杜绝 ducking/sidechain)，最后导出 mix 过后的音乐音效分轨时使用。
  覆盖标准逐轨匹配、合并参考、Idle音效匹配、音乐作氛围 四种场景，含 ffmpeg 滤波器链、
  两段式 loudnorm 精确匹配、stereo 保护、峰值限制等全部经验与踩坑点。
  另含音效库管理模块：对大规模音效库(数十万文件)做一遍索引后，支持按关键词/库/格式检索、
  库健康审计(格式/垃圾文件/非音频混杂)、重复文件发现(size 候选 + hash 精确)、ffprobe 规格画像
  (采样率/位深/声道/时长)，全部只读、索引外置。
  另含音效频段相似检索模块：给定参考音效路径或文字频段描述，在已索引音效库里按 8 段频段
  能量分布的余弦相似找"听感接近"的音效（按库/子类即时算 + bandprof 缓存，需 numpy）。
  另含音效材质/合成设计相似检索模块：给定参考音效，先解析其"声音材质构成 + 合成设计"（谐波纯度/
  明亮度/持续度/尾音长度/调制深度/事件密度/起音时间/瞬态锐度 8 维指纹），再按指纹余弦相似在全库
  找"声音是怎么做出来的"相近的音效（compsim 子命令，matprof 缓存，需 numpy）。这是 bandsim 频段
  相似之外的另一维度：即使频段分布不同，只要合成手法/材质相近也会被找出来；bandsim 最佳相似度
  低于阈值(默认 0.55)时会自动改用此维度兜底。
  另含 CG/视频音乐情绪分析模块：自动抽音轨并按窗口提取声学描述子，映射成 valence–arousal
  情绪时间线 + 近似 tempo，用于审外包 CG 音乐情绪弧（需 numpy，无训练模型，标签为粗粒度启发式）。
  可配合 MixForge 桌面工具做参考作品分析；导出分轨可直接进 Wwise/FMOD 引擎。
description_zh: 音频设计师助手（Audio Designer Helper）：音乐音效响度匹配、融合混音、音效库管理、频段相似检索与音乐情绪分析
description_en: Audio Designer Helper — Music + SFX loudness match & mix, SFX library mgmt, band-similarity search, CG music emotion analysis
disable: false
agent_created: true
---

# Audio Designer Helper — 音频设计师助手（音乐音效响度匹配与融合混音工作流）

## When to use
- 用户给出素材组文件夹（如 `C:\Users\<用户名>\Desktop\展示对比混音\素材名\`），内含至少两个子文件夹（通常是不同版本），每个含 MUS(音乐) 和 SFX(音效) 文件
- 用户要求：`让 版本_X 完全匹配 版本_Y 的响度` → `混音` → `导出 mix 版本分轨`
- 触发词：混音、响度匹配、MIX 版本、音乐音效分轨、版本_X 去匹配 版本_Y、融合、不打架、不能盖住
- 也适用于：提供的参考是单个已混好的合并文件、或只有音效需要匹配、或音乐只作氛围
- **音效库管理触发词**：音效库管理、盘库、找音效、搜音效、SFX library、音效库扫描、音效去重、规格审计、音效库报告 →
  走「音效库管理模块」（见下文），详细流程在 `references/sfx_library.md`；脚本 `scripts/sfx_library.py`
- **音效频段相似检索触发词**：相似音效、按频段找、频段检索、sound-alike、类比查找、听感接近、找类似的音效 →
  走「音效频段相似检索模块」（见下文），详细流程在 `references/sfx_similarity.md`；脚本 `scripts/sfx_library.py bandsim`
- **音效材质/合成设计相似检索触发词**：声音材质、合成设计、声音是怎么做的、shimmer/颗粒/调制相近、找材质类似的、频段找不到相似就换维度 →
  走「音效材质/合成设计相似检索模块」（见下文）；脚本 `scripts/sfx_library.py compsim`；
  `analyze` 现已带"材质/合成设计指纹"小节（见下文该模块说明）
- **CG/视频音乐情绪分析触发词**：音乐情绪、情绪分析、CG 音乐、视频音乐情绪、情绪时间线、mood analysis、审音乐情绪弧 →
  走「CG/视频音乐情绪分析模块」（见下文），详细流程在 `references/music_emotion.md`；脚本 `scripts/music_emotion.py`

## 核心宗旨（用户明确要求，务必遵守）
> **在不改变原本音乐音效听感的前提下尽量让两者融合。**
- 双方都必须清晰可辨，互不干扰
- **严禁** ducking / 大 sidechain / 自动闪避
- 干预最小化：不要为了"融合"去疯狂 boost / scoop 频段
- 音乐不要做音量渐大/渐小（用户多次反馈"每首音乐都需要在保持统一音量前提下去 mix"）

## 三步工作流（标准形态）
1. **Step 1 — 响度匹配**：被调整文件夹里的 MUS 精确匹配参考文件夹 MUS 的 LUFS，SFX 精确匹配参考 SFX 的 LUFS（偏差 < 0.10 LUFS，力争 < 0.03）。各自独立匹配。
2. **Step 2 — 混音预览**：把匹配后的 MUS + SFX 混合，给出一版 Mix Preview 让用户试听并反馈平衡。
3. **Step 3 — 导出分轨**：用户确认 Step 2 后，把"mix 处理过"的 MUS / SFX 各自导出为独立分轨（不做整体归一化，保留各自 mix 增益）。

> 每一步都要向用户确认再进下一步（用户是结构化分步确认制）。Step 3 必须在 Step 2 被用户明确说"没问题/可以"之后才做。

---

## 工具协同：MixForge 伴侣（可选，非依赖）

> **MixForge** 是一个独立的桌面自动混音工具（Electron 应用），擅长"参考作品分析 → 推导各轨道目标响度"和"多轨自动混音"。
> 它与本 skill 是**互补关系**：MixForge 负责"定目标 + 多轨自动平衡"，本 skill 负责"用 ffmpeg 精确命中目标 LUFS + 导出可进引擎的独立分轨"。
> **不装 MixForge 也能完整使用本 skill**（纯 ffmpeg 流程）；装了它，可在 Step 1 前先拿到参考画像，少走弯路。

### 协同管线（推荐）
1. 用 MixForge 打开参考作品 → 得到 `ReferenceProfile`（Integrated LUFS / LRA / True Peak / 7 子带能量 / 声像 / 瞬态）。
2. 用其自动混音推导的轨道目标偏移（见下方"主轨道基准机制"）作为 Step 2 混音平衡的**初始取向**。
3. 回到本 skill：Step 1 用 ffmpeg 两段式 loudnorm 精确把 MUS/SFX 命中目标 LUFS（偏差 < 0.10）；Step 2 按 MixForge 给的偏移做静态平衡并出预览；Step 3 导出分轨。
4. （可选）把分轨导入 Wwise（见文末"Wwise 运用"）——MixForge 的 `sync-references to Wwise` 仍是**规划中**能力，当前靠手动导入。

### MixForge 知识库原则（已融入本工作流）
这些原则来自 MixForge 的混音知识库，与本 skill 的 ffmpeg 实践互相印证：

**频段混音原则**
- 低频 20–250Hz：地基。底鼓 60–100Hz、贝斯 80–200Hz；人声/音效轨道 HPF 切 80Hz 以下。
- 中频 250–2000Hz：清晰度主体；250–500Hz "箱音" 适度衰减可增加清晰度。
- 中高频 2–5kHz：语音识别度关键，音效在此让路（语音保护频段）。
- 高频 5–20kHz：空气感/亮度，8–12kHz 增光泽，16kHz+ 微量即可。
- 本 skill 的 `highpass=f=80` 正是低频清理；"轻度 carving" 对应中频让路。

**LUFS 目标参考（行业基准，供自定目标用）**
| 场景 | 目标 LUFS |
|------|-----------|
| 流媒体发布 | -14 |
| 游戏音乐 | -16 ~ -18 |
| 游戏音效 | -18 ~ -22 |
| 游戏语音 | 音乐基准 -2dB |
| 影视混音 | -24（对话基准） |
- 本 skill Step 1 的 `target` 取自**用户指定的参考版**，不是这张表；此表用于"没有现成参考、需自定目标"时。

**主轨道基准机制（MixForge 自动混音用）**
- 以音乐轨道原始 LUFS 为基准（不统一标准化 → 避免音乐被压低）。
- 语音目标 = 基准 -2dB；音效目标 = 基准 -4dB。
- 本 skill Step 2 的 SFX 默认偏移（-1.5 ~ -4dB）与之同一思路；用 MixForge 推导值可替代经验值。

**频谱冲突解决优先级（通用）**
1. 声像分离（首选，零音质损失）
2. EQ 让路（次选，谨慎）
3. 动态侧链（最后手段）——**本 skill 默认禁 ducking/sidechain**，冲突优先靠声像 + 轻 EQ。

**动态范围（LRA）**
- 音乐 6–12dB、语音 4–8dB、音效 8–16dB；过度压缩丢失生命力。
- 故本 skill 用静态音量平衡 / `acompressor`，绝不用 `dynaudnorm`。

---

## Step 1 — 响度匹配（精确 LUFS）

### 测量 LUFS / 真峰 (TP)
```bash
ffmpeg -hide_banner -nostdin -i "<file.wav>" -af "loudnorm=print_format=json" -f null -
# 取 stderr 里 input_i (LUFS) 与 input_tp (true peak dBTP)
```

### 匹配算法（迭代逼近，最稳）
对**原始文件**迭代施加总增益 `volume=G dB`，每次重新测量，直到 `measured ≈ target`：
```
G = 0
for i in range(max_iter):
    tmp = apply volume=G to ORIGINAL
    measured = measure_lufs(tmp)
    diff = target - measured
    if abs(diff) < 0.03: break
    G += diff          # 线性逼近（LUFS≈线性于增益dB）
```
- 把**最佳 G 对应的临时文件**复制为最终输出，再测一次确认偏差 < 0.10
- 不要对上一次结果再叠加 gain（会累积误差/limiter 干扰），始终对**原始文件**施加总 gain

### 峰值处理（关键）
- 若**源文件或参考文件真峰 > 0 dBTP**（实测常出现 +0.3~+1.7），任何增益都会削波，污染 LUFS 测量 → 匹配时加 `alimiter=limit=0dB:level=false` 把峰顶锁在 0 dBFS（数字不过载；比参考本身已削波更干净）
- `alimiter` 的 `limit` **不能 > 0 dBFS**；若想"匹配参考真峰"，上限只能取到 0
- ⚠️ **迭代替增益收敛时不要用 alimiter 做"压峰阻止响度收敛"的常驻限制器**——它会吃掉增益导致 ~0.45 LUFS 偏差，使匹配无法收敛。仅在最终输出需要防削波时短接一次

### ffmpeg 匹配单条命令模板
```bash
ffmpeg -hide_banner -y -i "<src.wav>" \
  -af "volume=<G>dB,alimiter=limit=0dB:level=false" \
  -acodec pcm_s24le -ar 48000 -ac 2 "<out.wav>"
```

---

## Step 2 — 混音预览

### 标准平衡（默认取向：音效略靠前、双方清晰）
> 若已用 MixForge 得到参考作品的轨道目标偏移（音效基准 ≈ 基准-4dB、语音 ≈ 基准-2dB），可直接套用其推导值作为初始取向；否则按下述经验取值。
```
MUS : volume -1.5dB                        (给 SFX 留空间)
SFX : highpass=f=80:p=2, volume <g>dB      (HPF 去低频轰鸣；g 视平衡而定)
amix=inputs=2:duration=longest:normalize=0
loudnorm=I=-16:TP=-1.0:LRA=11              (仅预览归一化，方便试听)
```
- **SFX gain 取值经验**：
  - 参考里音效本就比音乐响 → 同幅下调（如 MUS -1.5 / SFX -1.5），保留参考平衡
  - 参考里音效比音乐低 → SFX 拉 +2~+3dB，让音效略靠前
  - 用户说"音乐太小" → MUS 推 +3~+4dB、SFX 收到 +1dB（音乐反超音效 2~3dB）

### 滤波器链要点
- ffmpeg 滤波器名：**`lowshelf`/`highshelf`**（不是 `low_shelf`/`high_shelf`）、`equalizer`、`highpass`、`volume`、`amix`、`loudnorm`
- 轻度 carving（仅在频段打架时）：MUS 在冲突频段 `equalizer=f=1000:type=peak:width=1.0:gain=-2.0`，SFX 加 `highshelf` boost + HPF 80Hz
- **绝不用 `dynaudnorm`** —— 它会自动推增益，遇大动态段落把音频压成静音/后半段丢失（用户反馈"后半段音乐全没了"就是它导致的）
- 若确需压动态范围，用 `acompressor`（安全，不改变结构、不丢段落）

### 合并参考场景（参考是单个已混好的文件）
参考文件夹只有 **1 个合并文件**（如 `Merged_Reference.wav`），没有独立 MUS/SFX → 无法逐轨匹配。做法：
1. 先 mix（MUS 原样 / SFX HPF+增益），`amix normalize=0`
2. 两段式 `loudnorm` 把**整首混合结果**精确对齐参考的 I（如 -15.28）与 TP（如 -0.95）
```bash
# 第一段测 premix 的 input_i/tp/lra/thresh
# 第二段(默认动态模式, 同时命中 I 与 TP):
ffmpeg -i premix.wav -af "loudnorm=I=<ref_I>:TP=<ref_TP>:LRA=11:\
measured_I=<mi>:measured_TP=<mtp>:measured_LRA=<mlra>:measured_thresh=<mth>:offset=0" \
-acodec pcm_s24le -ar 48000 -ac 2 out.wav
- ⚠️ **不要**加 `linear=true`：loudnorm 的 linear 模式走固定增益、会绕过真峰限制，TP 对齐将悄然失效；要守 TP 必须用默认动态模式。仅当你只对齐 I、不在乎 TP 时，才可用 `linear=true`。
```
- ⚠️ 用 `-af` 时不要写 `[out]` 输出标签（会报 "Output with label 'out' does not exist"）；如需标签用 `-filter_complex` + `-map '[out]'`

### Idle 音效场景（音效需配合音乐，且音效在播放中位置不固定）
用户：让 `Xxx_Idle_SFX` 完全匹配参考 `Xxx_Show_SFX` 响度 → 再和参考 `Xxx_Show_MUS` 合在一起检查频段不被覆盖、也不压过音乐。
- 先按 Step 1 把 Idle SFX 匹配到参考 SFX 的 LUFS（同算法，加 0dBTP limiter 防削波）
- 再 mix：音乐不变，匹配后音效 + HPF 80Hz，双方叠加不互压

### 音乐作氛围场景（音效为主，音乐不能太背音效盖住）
- MUS：低音量（如 -5 ~ -8dB）+ `highpass=f=60` 去超低频 + 中频轻 scoop 避免打架
- SFX：保持主体清晰（如 -1dB + HPF 80）
- 反馈"音乐可以再大一些" → MUS 从 -8dB 提到 -5dB，加中频 scoop 防打架

---

## Step 3 — 导出 mix 版本分轨

用户确认 Step 2 后才做。导出**各自独立**的 MUS / SFX 分轨，处理与试听版完全一致：
```bash
# MUS 分轨
ffmpeg -hide_banner -y -i "<matched_MUS.wav>" -af "volume=<mus_gain>dB" \
  -acodec pcm_s24le -ar 48000 -ac 2 "<MUS_stem.wav>"
# SFX 分轨
ffmpeg -hide_banner -y -i "<matched_SFX.wav>" -af "highpass=f=80:p=2,volume=<sfx_gain>dB" \
  -acodec pcm_s24le -ar 48000 -ac 2 "<SFX_stem.wav>"
```
- **不要**对分轨再做整体 loudnorm（那是混合预览用的）；分轨保留各自 mix 增益，方便在 Wwise 里再叠加
- 输出目录建议：`最终分轨/<batch>/<素材名>/`

---


## 音效库管理模块（检索 / 审计 / 去重 / 规格画像）

> 独立模块，可单独触发；找原料 → 主工作流加工 → 规格审计，构成完整闭环。
> 完整命令、工作流、坑见 `references/sfx_library.md`；脚本 `scripts/sfx_library.py`。

### 设计原则（红线）
1. **只读优先**：除生成索引 DB 外不修改/不删除库内任何文件；清理/去重/转码均"先出报告 → 你确认 → 再执行"。
2. **索引外置**：SQLite 默认存 `~/.workbuddy/sfx_library/index.db`，绝不写入音效库目录。
3. **一遍扫描多次查询**：几十万文件扫一次约 7 分钟，之后检索/报告毫秒级。

### 快速命令
```bash
# 建索引（已对 /Volumes/Backup Plus/SFX 跑过，DB 在 ~/.workbuddy/sfx_library/index.db）
python scripts/sfx_library.py scan --root "/Volumes/Backup Plus/SFX" --db ~/.workbuddy/sfx_library/index.db
# 按关键词检索（默认排除垃圾文件）
python scripts/sfx_library.py search --kw whoosh --audio-only
python scripts/sfx_library.py search --library HOK --kw UI --limit 20
# 库健康报告
python scripts/sfx_library.py report
# 找重复：size=快速候选(误报多) / hash=精确(慢,后台)
python scripts/sfx_library.py dupes --mode size --limit 20
# 规格画像（ffprobe 抽样）
python scripts/sfx_library.py specs --library HOK --sample 100
```

### 要点
- **检索**是最高频能力：模糊匹配文件名，可组合 `--library`/`--ext`/`--audio-only`/`--min-size`/`--max-size`。
- **去重坑**：体积碰撞 ≠ 重复。5.1 环绕 stem（各通道同字节）、等长 loop/hit 都会体积碰撞但不是重复——size 只缩小范围，真重复走 `hash` 且**人工确认同源**再处理。
- **规格画像**：确认交付库是否统一 48k/24bit/stereo；`bits=0` 即 mp3/ogg/aac 等压缩格式（正常）；发现 44.1k 遗留、5.1 多声道等"异类"决定是否规整。
- **实测**：本库 359,800 文件（音频 348,223 / 垃圾 22 / 其他 11,555），DB=`~/.workbuddy/sfx_library/index.db`。HOK 交付标准化在 48k/24/stereo；全库高度异构（含 96k/192k、mono/stereo/5.1、压缩格式）。剩余 22 个垃圾为 Finder 重生的 .ds_store + 临时文件，极小可后续清（见 `report`）。

---

## Wwise 运用：把导出分轨接进引擎

本 skill 导出的 `MUS_stem.wav` / `SFX_stem.wav` 就是为进 Wwise 这类中间件准备的（48kHz / 24bit / stereo，与 Wwise 工程默认兼容）。

### 导入与结构
- 用 **Audio File Importer** 批量导入分轨；位深选 24-bit、采样率 48kHz（与本 skill 一致，无需重采样）。
- 建议层级：
  ```
  Actor-Mixer Hierarchy
  ├─ Music  (Actor-Mixer)  → 挂 MUS_stem
  └─ SFX    (Actor-Mixer)  → 挂 SFX_stem
  ```
  二者各自挂到独立 **Audio Bus**（如 `Music_Bus` / `SFX_Bus`），方便整体调平衡与加总线处理。

### 还原 mix 平衡（关键）
- 分轨**已带各自 mix 增益**（Step 3 的 `mus_gain` / `sfx_gain`），故在 Wwise 里**不要开 Normalize**（Audio File Importer 的 Normalize 选项保持关闭），否则会把分轨重新拉平、破坏 mix 平衡。
- 还原试听版平衡：在 Actor-Mixer 上设 `Volume`，让 MUS / SFX 的相对关系与 Step 2 预览一致（音乐略后、音效略前，或按你的参考取向）。
- 如需核对整段响度，用 Wwise 的 **Loudness Meter / Meter** 验证最终 Integrated LUFS，而不是重新归一。

### 变体与空间
- 多版本 / 随机：用 **Random Container / Switch Container** 挂多个分轨变体；**Blend Container** 做交叉淡入淡出。
- 3D 空间：把 SFX Actor-Mixer 接到 **Position / Attenuation** 做距离衰减；音乐通常设为 2D（不计距离）。
- RTPC：可用 RTPC 驱动音乐/音效的相对音量（如战斗强度推音乐），但**保持 Step 2 定的基准平衡**为 0 点。

### 避坑
- ⚠️ 不要在 Wwise 里对分轨再做一次整体响度归一——那等于重做 Step 3，且会抹掉 mix 平衡。
- ⚠️ 不要对音乐轨开自动闪避 / ducking（与核心宗旨冲突）；双方清晰靠的是 Step 2 的平衡 + 轻 EQ。
- 若需进 FMOD：思路一致（Event + Mixer Bus + 手动 gain），分轨可直接复用。

---

## 通用硬性规则
- **永远是 stereo（2 声道）**：任何 ffmpeg 命令都带 `-ac 2`，绝不加 `-ac 1`（用户明确拒绝 mono："为什么是mono呢，应该都是stereo啊"）
- 统一格式：`pcm_s24le -ar 48000 -ac 2`
- **路径写法**：
  - Windows（含 Git Bash）：`C:\Users\...`（反斜杠）或直接 `/c/Users/...` 都行——Git Bash 在执行前会把 `/c/...` 翻译成 `C:\...`，两种 ffmpeg 都能识别。**真正的坑是含空格/中文的路径必须加引号**，不是斜杠形式。
  - macOS / Linux：用 `/Users/...` 或 `~/...`，同样注意空格加引号。
- **Python 受管 venv**：
  - Windows：`C:/Users/<用户名>/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
  - macOS / Linux：`/Users/<用户名>/.workbuddy/binaries/python/envs/default/bin/python3`（或直接用系统 `python3`）

## Pitfalls（踩坑全集）
| 现象 | 原因 | 解决 |
|------|------|------|
| 输出是 mono | 命令里带了 `-ac 1` | 删除，改 `-ac 2` |
| 频段全乱、听感崩 | EQ boost/scoop 过猛 | 最小干预：只 HPF + 轻 scoop(-1~-2dB) |
| 后半段音乐没了 | 用了 `dynaudnorm` | 改用静态音量平衡或 `acompressor` |
| filter not found | 写了 `low_shelf`/`high_shelf` | 改用 `lowshelf`/`highshelf` |
| 数据错位 / NaN | 假设 WAV 头固定 44 字节；EQ 参数过激 | 用 ffmpeg/wave 模块读；滤波器参数放温和 |
| 管道丢数据 | ffmpeg stdout pipe | 改用临时文件 |
| Git Bash 中文路径失败 | 含空格/中文路径**未加引号**（两种斜杠形式 Git Bash 都能翻译） | 路径加引号：`"C:\Users\..."` 或 `"/c/Users/..."` |
| 匹配不收敛 / 偏差大 | 迭代替增益时常驻 alimiter 吃增益 | 收敛阶段不加 limiter，仅在最终防削波时接一次 |
| 增益后削波 | 源/参考真峰 > 0 | 加 `alimiter=limit=0dB:level=false` |
| loudnorm 匹配不精确 | 单遍 loudnorm 不保证 I | 两段式：先测再带 measured_* 参数二遍 |
| Label 'out' not exist | `-af` 里写了 `[out]` 标签 | 去掉标签；或改用 `-filter_complex`+`-map` |

## Verification（每次必做）
1. `ffprobe` 确认分轨 `channels=2`、`sample_rate=48000`、`bit_depth` 正常、时长与源一致
2. 对匹配后文件重新 `loudnorm` 测量，确认与参考偏差 < 0.10 LUFS
3. `present_files` 把 Mix Preview / 分轨交给用户试听确认

## 音效频段相似检索模块（按相似频段找音效）

> 详细流程：`references/sfx_similarity.md`；脚本 `scripts/sfx_library.py bandsim`

### 适用
想找"听感频段接近"的音效，而不是直接按文件名搜。例如"找个和这个低频轰鸣类似的""要一段明亮有冲击的"。

### 设计原则（红线）
- 复用 `index.db` 的 `files` 表，不重复扫库。
- 每个文件的 8 段频段向量算一次存进 `bandprof` 表，下次秒回（缓存）。
- 候选范围用 `--library` / `--subcat` 限定（数千文件级可接受），首次命中即时算并写缓存；若要让全库任意检索都秒回，用 `prewarm` 子命令一次性预热（见下；WAV 直读+向量化+多进程 `--parallel 4`，外部 USB 盘 I/O 密集，约 3–4h 长跑，可断点续跑）。
- **频段相似 ≠ 语义相似**：它找频谱分布接近的音效，但**不懂语义**（不知道两个都是"开门声"）。语义还得靠文件名/子目录。

### 快速命令
```bash
# (a) 参考音效 → 找相似 (候选限定 HOK/Impacts 子集, 更快)
python scripts/sfx_library.py bandsim \
  --query "/某参考.wav" --library HOK --subcat Impacts --topk 10 --verbose

# (a') 按路径关键词圈定候选池（索引 subcat 粒度粗时最精准）
python scripts/sfx_library.py bandsim \
  --query "/某参考.wav" --library "音效库" --like "%bed%,%amb%,%atmo%" --topk 30 --verbose

# (b) 文字描述 → 找相似
python scripts/sfx_library.py bandsim \
  --describe "低频轰鸣 + 明亮瞬态" --library HOK --topk 10

# (c) 搜索完自动在桌面生成"相似音效"文件夹(zip) —— 默认就开, 不用加 --export
#     默认落到 ~/Desktop/sfx_similar/<参考名>/ 并打包 .zip (只复制不破坏原库)
python scripts/sfx_library.py bandsim \
  --query "/某参考.wav" --library "音效库" --like "%bed%" \
  --notlike "%spring%,%creak%,%wooden%,%squeak%,%bedroom%,%mattress%,%bounce%,%foley%,%hoe%" \
  --topk 30
#     想自定义桌面位置: --export "$HOME/Desktop/sfx_similar/我的结果"
#     想关掉自动导出(只打印不落盘): --no-export

# (d) 单独对参考音效做全面声学分析(也可在 bandsim 找不到时自动触发)
python scripts/sfx_library.py analyze --query "/某参考.wav"
python scripts/sfx_library.py analyze --query "/某参考.wav" --json
```
- 频段模型：`sub 20-60 / low 60-250 / lomid 250-500 / mid 500-1k / himid 1-2k / humid 2-4k / pres 4-8k / bril 8-20k`（各段占比之和=1，指纹取自前 6 秒 leading window）。
- 相似度 = 余弦相似（0–100%）。Top-K 给库内绝对路径，可接回主工作流。
- **自动导出（默认开）**：每次 `bandsim` 搜索完，自动把 Top-K 相似音效复制到 `~/Desktop/sfx_similar/<参考名>/` 并打包同名 `.zip`（**只复制、不动原音效库**；文件夹名取参考文件名，同名重跑会覆盖）。这是"搜完即交付一个桌面文件夹"的默认行为。
- `--export <文件夹>`：覆盖默认桌面位置，把结果放到指定文件夹并打包 `.zip`。
- `--no-export`：关闭自动导出（只打印排名、不落盘）。
- `--notlike`：排除语义不符的候选（如把家具 Foley 排除、只留氛围 bed）。频段相似不懂语义，`--like "%bed%"` 会误收"bed spring/wooden bed"等家具音，用 `--notlike` 纠偏。
- **本地找不到时 / 相似度过低时**：`bandsim` 在(1)一个候选都读不出时，会自动对参考音效跑 `analyze` 全面分析兜底；(2)若**最佳相似度低于阈值(默认 0.55)**，会自动改用 `compsim`（材质/合成设计维度）再搜一次——即"解析参考音效组成 + 全库搜相近材质/合成设计"的兜底能力（见下一模块）。`analyze` 子命令也可单独用（规格/RMS/Peak/Crest/8段/质心/截止/平坦度/瞬态/BPM/主导频段 **+ 材质/合成设计指纹**）。
- **前置**：`pip install numpy`（仅 bandsim/prewarm/analyze/compsim 需要）。

### 预预热 `prewarm`（全库双缓存 bandprof+matprof，可选长跑）
```bash
# 全库预热：之后 bandsim / compsim 任意检索秒回（WAV 直读+向量化 FFT+并行 4，
# 实测约 27 文件/秒 → 36 万约 3–4h，后台跑；外部 HDD 上并行 4 最优）
python scripts/sfx_library.py prewarm --parallel 4
# 只预热某个库/子类（快得多，按需）
python scripts/sfx_library.py prewarm --library HOK --subcat Impacts --parallel 4
```
- 自动跳过 bandprof 与 matprof 都已缓存的路径，可断点续跑；每 200 条提交一次；不写 `--limit` 即为不限量。

---

## 音效材质 / 合成设计相似检索模块（compsim）

> 与 `bandsim`（频段能量分布相似）互补的另一维度。脚本 `scripts/sfx_library.py compsim`
> 详细流程见 `references/sfx_similarity.md` §7。

### 适用
`bandsim` 按"频谱分布"找相似，但它有两个盲区：
1. **频段分布不同但"声音是这么做成的"其实很像**（比如两段不同的 shimmer pad，一个偏亮一个偏暗，频段向量差很多，但都是"高调制 + 高颗粒密度 + 缓入 + 谐波源"做出来的）；
2. **在频段维度根本找不到足够相似的音效**（最佳相似度过低）。

`compsim` 就是为这两类场景而生：它先把参考音效拆成 **8 维"声音材质 / 合成设计"指纹**，再按指纹余弦相似去全库找"合成手法/材质相近"的音效。这正是"找不到相似音效时，分析参考音效组成 → 全库搜相近材质/合成设计"的能力。

### 8 维材质 / 合成设计指纹（均已归一化 0–1）
| 维度 | 听感 / 合成含义 | 高值≈ |
|------|----------------|-------|
| tonal    | 谐波纯度（振荡器/采样 vs 噪声发生） | 谐波音源 |
| bright   | 明亮度（滤波/振荡器设计） | 明亮滤波/高通 |
| sustain  | 持续度（铺底 Pad vs 脉冲/断奏） | 长铺底 |
| tail     | 尾音长度（混响/延迟/反馈设计） | 长尾/混响 |
| mod      | 调制深度（LFO/合唱/颤音/镶边设计） | 明显调制 |
| density  | 事件密度（颗粒/纹理/连击设计） | 颗粒/多连击 |
| attack   | 起音时间（拨弦/渐入 swell 设计） | 缓入 swell |
| trans    | 瞬态锐度（打击乐设计） | 打击/瞬态 |

`analyze` 子命令现已打印这组指纹 + **推断合成设计标签**（启发式，如"长铺底/长尾""高事件密度(近似 Granular·Arp)""明显调制(近似 LFO·Chorus·Vibrato)""谐波音源""暗色滤波/低通"等）。

### 快速命令
```bash
# (a) 参考音效 → 按"材质/合成设计"找相似 (候选限定某库/子类, 更快)
python scripts/sfx_library.py compsim \
  --query "/某参考.wav" --library "音效库" --subcat 氛围铺底音效 --topk 15 --verbose

# (b) 也能用 --like / --notlike / --library / --subcat / --topk / --verbose / --export / --no-export
#     (语义与 bandsim 完全一致; 默认也自动把 Top-K 复制到 ~/Desktop/sfx_similar/<参考名>_mat/ 并打包 zip)
python scripts/sfx_library.py compsim \
  --query "/某参考.wav" --library "音效库" --like "%shimmer%,%amb%,%pad%" --topk 20

# (c) 先看参考音效的"组成 + 推断合成设计" (analyze 已含材质指纹小节)
python scripts/sfx_library.py analyze --query "/某参考.wav"
```

### 设计原则（红线）
- 复用 `index.db` 的 `files` 表；每个文件的 8 维指纹算一次存进 **`matprof`** 表，下次秒回（缓存，与 `bandprof` 同库、可断点续跑）。
- 指纹来自前 6 秒 leading window（与 bandsim 同款速度/精度权衡）。
- **材质相似 ≠ 语义相似**：它找"合成手法/材质"相近的音效，仍不懂"两个都是开门声"这类语义；语义靠文件名/子目录或 `--like` 圈定。
- 与 `bandsim` 互补而非替代：频段分布差异大但合成手法相似的，用 `compsim`；两者都跑、对照看最稳。
- **兜底触发**：`bandsim` 最佳相似度 < `--threshold`(默认 0.55) 时，自动调用 `compsim` 再搜一次并打印结果（也会自动导出 `<参考名>_mat` 文件夹）。可 `--threshold 0` 关闭兜底，或调高阈值让兜底更易触发。

---

## CG / 视频音乐情绪分析模块

> 详细流程：`references/music_emotion.md`；脚本 `scripts/music_emotion.py`

### 适用
审外包 CG / 过场动画的音乐情绪弧——"音乐的情绪转折点有没有卡上剪辑点""情绪对不对"。视频自动抽音轨。

### 设计原则（红线）
- 离线 numpy+ffmpeg，**无训练模型**（不下 zenodo 权重，避免本环境限速）。
- 输出**情绪时间线**（每窗 valence–arousal + 离散标签）+ 总体 + 近似 tempo(BPM)。
- **诚实边界**：分析的是**音乐自身的声学情绪**，不是画面叙事情绪；混有人声/音效会污染 → 最好喂纯音乐轨；标签是粗粒度启发式，不是精确量表。

### 快速命令
```bash
# 视频：自动抽音轨分析
python scripts/music_emotion.py "某CG.mp4" --win 8
# 音频 + 每窗频段条
python scripts/music_emotion.py "某BGM.wav" --verbose
# 机器可读（交给 agent 做 narration / 比对）
python scripts/music_emotion.py "某CG.mp4" --json
```
- 每窗描述子：大/小调近似、频谱质心(明亮度)、频谱平坦度(张力)、能量；映射 valence/arousal + 标签（激昂/史诗、紧张/压迫、宁静/温暖、悲伤/低沉、欢快、神秘/中性）。
- **前置**：`pip install numpy`。

---

## 参考脚本
- `scripts/standard_workflow.py`：标准逐轨匹配 + 混音 + 导出 的参考实现（参数化，可直接改 TASKS 复用）
- `scripts/sfx_library.py`：音效库管理（scan 建索引 / search 检索 / report 健康报告 / dupes 找重复[size|hash] / specs ffprobe 规格画像 / bandsim 频段相似检索[需 numpy, 支持 --like 缩范围 / --export 打包到桌面] / compsim 材质·合成设计相似检索[需 numpy, 与 bandsim 互补, 按材质指纹余弦相似, 支持 --like/--export/--threshold 兜底] / analyze 参考音效全面声学分析[含材质/合成设计指纹, 需 numpy] / prewarm 全库频段缓存预热[需 numpy]），索引默认存 `~/.workbuddy/sfx_library/index.db`，只读不改动库
- `scripts/music_emotion.py`：CG/视频音乐情绪分析（自动抽音轨 → 分窗 → valence–arousal 情绪时间线 + 近似 tempo，需 numpy，无训练模型）
- `references/edge_cases.md`：四种场景的判定树与完整命令示例
- `references/sfx_library.md`：音效库管理完整工作流（设计原则、命令速查、典型闭环、去重坑、规格审计、已知坑）
- `references/sfx_similarity.md`：音效频段相似检索完整工作流（频段模型、两种查询、缓存、已知坑）
- `references/music_emotion.md`：CG/视频音乐情绪分析完整工作流（描述子含义、用法、诚实边界、已知坑）


---

## 新增能力（2026-09 更新）

> 以下为本期新增、独立于上述模块的离线检索与校准能力。旧模块（响度匹配 / 音效库管理 / bandsim / compsim / 音乐情绪）保持不变。

### 具名概念混合检索（scripts/hybrid_search.py）
针对「门 / 撞击 / 雷 / 警报」等**具名概念**，纯频谱相似度误检率高（会把单次瞬态的敲门、对话误判为门）。改用**混合检索**：文件名关键词过滤（door / slam / impact / hit / knock / thud / bang / metal / wood / crate / hatch / shutter）叠加 8 段频谱声学门控（宽频能量 ≥3 段 + 单段 < 0.60 去窄带 + 中低频体 + 质心 250–4000Hz + 时长 < 2.5s）。实测在全库 34 万+ 文件里从 62319 关键词候选收敛到 962 个真门/撞击/敲击，Top40 全部真相关。
- 用法：`python scripts/hybrid_search.py --kw door --db <index.db> --top 40`
- 与 bandsim / compsim 的关系：bandsim / compsim 解决「找听感 / 合成手法相近」；hybrid_search 解决「按名字找确定概念」。具名概念优先 hybrid，模糊相似用 band / compsim。

### 粉红噪音校准（scripts/gen_pinknoise.py）
任何平衡 / 响度对齐问题，先以粉红噪音锚定监听。生成 Paul Kellet 粉红噪音并归一化到目标 LUFS（默认 -23 LUFS），作监听校准参考。
- 用法：`python scripts/gen_pinknoise.py --out pink.wav --lufs -23 --sr 48000 --dur 30`

### 响度分析（scripts/analyze_loudness.py）
对单条音频输出 LUFS / 真峰 dBTP / RMS / 频谱质心，用于交付前客观验收。
- 用法：`python scripts/analyze_loudness.py path/to/file.wav`

### 离线索引器（scripts/build_index.py）
为 hybrid_search 建库：扫描本地音效库，产出 `index.db`（files / specs / bandprof / matprof）。只读、不改动库内文件；`--db` 参数化、路径脱敏。

### 能力账本（CAPABILITY-LEDGER.json）
声明各能力实测状态与独立复验命令，供二次检验（capability-verifier）对账「声称能用 vs 实测能用」。

### 依赖
新增脚本依赖 `numpy` + `soundfile` + `pyloudnorm`（`ffmpeg` 仍用于响度测量）。详见 `requirements.txt`。
