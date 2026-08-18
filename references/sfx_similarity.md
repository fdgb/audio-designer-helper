# 音效频段相似检索模块（SFX Band-Similarity）

> 配套脚本：`scripts/sfx_library.py` 的 `bandsim` 子命令
> 适用：想找"听感频段接近"的音效，而不是直接按文件名搜（如"找个和这个低频轰鸣类似的""要一段明亮有冲击的"）。
> 场景触发词：相似音效、按频段找、频段检索、sound-alike、类比查找、听感接近

---

## 0. 设计原则（红线）

1. **复用索引**：直接读 `~/.workbuddy/sfx_library/index.db` 的 `files` 表，不重复扫库。
2. **频段缓存**：每个文件的 8 段频段向量算一次就存进 `bandprof` 表（随 `files` 同源 DB），下次秒回；不重复算。
3. **按范围即时算 / 预预热皆可**：日常检索用 `--library` / `--subcat` 限定（数千文件级，可接受），首次命中即时算并写缓存；若想让全库任意检索都秒回，可用 `prewarm` 子命令一次性预热（见 §5，外部 USB 盘 I/O 密集，是一次长跑，可断点续跑）。
4. **只读**：除写 `bandprof` 缓存列外，不碰音效库任何文件。
5. **频段相似 ≠ 语义相似**：它能找"频谱分布接近"的音效，但**不懂语义**（不知道两个都是"开门声"）。语义还得靠文件名/子目录。

---

## 1. 频段模型

每条音效被压成一个 **8 段归一化能量向量**（各段占比之和=1）：

| 段 | 频率范围 | 听感 |
|----|----------|------|
| sub   | 20–60 Hz   | 超低潜 |
| low   | 60–250     | 低频体 |
| lomid | 250–500    | 温暖中低 |
| mid   | 500–1000   | 中频主体 |
| himid | 1000–2000  | 中高 |
| humid | 2000–4000  | 临场 |
| pres  | 4000–8000  | 明亮 |
| bril  | 8000–20000 | 空气感 |

相似度 = 两个向量的**余弦相似**（0–100%）。另存 `centroid`(频谱质心/明亮度) 与 `flux`(瞬态密度) 供参考。

> **频段指纹取前 ~6 秒（leading window）**：为兼顾长环境音 / 多声道大文件（单文件可达 100 MB+）的解码速度，每段只取音频开头 6 秒做 FFT 统计（`_band_profile(lead=6)`）。音色指纹在开头即稳定，对"相似频段"检索足够；但若某音效首尾音色差异极大（如长渐变 pad），指纹只代表开头段。此为速度/精度权衡，已知边界。

---

## 2. 两种查询方式

### (a) 参考音效 → 找相似
```bash
python scripts/sfx_library.py bandsim \
  --query "/Volumes/Backup Plus/SFX/HOK/.../某参考.wav" \
  --library HOK --subcat Impacts --topk 10 --verbose
```
- `--query` 是要"像它"的参考音效路径（任意位置皆可，不必在库内）。
- 候选来自 `files` 表（默认全库音频；用 `--library`/`--subcat` 缩小范围更快）。
- **`--like` 路径子串过滤**（逗号分隔多个 pattern，`LOWER(path) LIKE`，OR 关系）：当 `--subcat` 粒度太粗（索引只记到浅层子目录）时，用它按文件名/路径关键词精准圈定候选池，例如 `--like "%bed%,%amb%,%atmo%"` 只搜铺底/氛围家族。这是日常"找某参考的同类"最高效的入口（见下方示例）。
- **`--notlike` 排除过滤**（逗号分隔多个 pattern，`LOWER(path) LIKE`，OR 关系，**命中任一个即剔除**）：频段相似不懂语义，`%bed%` 会把"bed spring(床簧)/wooden bed(木床)"等家具 Foley 也圈进来。用 `--notlike` 做语义纠偏，例如排除家具 Foley：`--notlike "%spring%,%creak%,%wooden%,%squeak%,%bedroom%,%mattress%,%bounce%,%foley%,%hoe%"`。这是"只要氛围 bed、不要家具 Foley"的关键开关。
- 结果即时算并写缓存到 `bandprof` 表；**每 200 条提交一次**，中断可断点续跑（重跑只算未缓存项）。
- `--verbose` 打印每个候选的频段条，方便肉眼比对。

```bash
# 按路径关键词圈定候选池（推荐：索引 subcat 粒度粗时，用 --like 最精准）
python scripts/sfx_library.py bandsim \
  --query "/Volumes/Backup Plus/SFX/音效库/BEST SFX/AMB/PREL_BED CRYSTAL_PO01.09.wav" \
  --library "音效库" --like "%bed%,%amb%,%atmo%" --topk 30 --verbose
```

### (b) 文字描述 → 找相似
```bash
python scripts/sfx_library.py bandsim \
  --describe "低频轰鸣 + 明亮瞬态" \
  --library HOK --topk 10
```
启发式关键词映射（粗）：低频/轰鸣/rumble/sub/bass→boost sub+low；中低/温暖/warm→boost lomid；明亮/高频/bright/air/亮/脆→boost pres+bril；冲击/瞬态/impact/打击→boost himid+humid。归一化后当查询向量。

---

## 3. 输出

```
== 查询向量 (某参考.wav) ==
  sub      0.11%
  low     38.93%
  ...
=== Top 10 相似音效 ===
  92.3%  /Volumes/.../候选A.wav
  88.1%  /Volumes/.../候选B.wav
  ...
```
Top-K 按相似度降序，直接给库内**绝对路径**，可一键接回主工作流的响度匹配/融合混音。

---

## 5. 预预热 `prewarm`（全库频段缓存，一次性长跑）

把整个 `files` 表里所有音频的 `bandprof` **和** `matprof` 同时提前算好（一次解码、两个指纹一起存），之后任意 `bandsim` / `compsim` 检索都直接命中缓存、秒回。适合"我要随时在全库 36 万音效里找相似"的场景。

```bash
# 全库预热（并行 4 进程；WAV 直读免 ffmpeg + 向量化 FFT，实测约 27 文件/秒
# → 36 万约 3–4 小时，后台跑；外部 HDD 上并行 4 最优，取更高反而因寻道竞争变慢）
python scripts/sfx_library.py prewarm --parallel 4

# 只预热某个库 / 子类（快得多，按需）
python scripts/sfx_library.py prewarm --library HOK --subcat Impacts --parallel 4
python scripts/sfx_library.py prewarm --library "音效库" --limit 1000 --parallel 4   # 先试一小批
```

> **性能关键**：`--parallel N`（默认 0=自动取 CPU 核数）用多进程并行解码+双指纹。底层 WAV 直读（绕过 ffmpeg 子进程）+ 向量化 `rfft` 把单文件从 ~430ms 降到 ~0.1s。外部 HDD 顺序读是瓶颈，实测并行 4 最优、并行 8 因寻道竞争反而变慢。

- **可断点续跑**：`prewarm` 自动跳过 `bandprof` 与 `matprof` 都已存在的路径（`path NOT IN bandprof OR path NOT IN matprof`），中断后重跑接着算，不重复（也顺带补全任一指纹缺失的文件）。
- **进度**：写 stderr（每 200 条一行百分比）；中途想看进度可另开 `SELECT COUNT(*) FROM bandprof` 查行数。
- **提交粒度**：每 200 条 `COMMIT` 一次，异常退出最多丢最后 200 条（重跑补回）。
- **不开 `--limit` 即为不限量**；显式 `--limit N` 只跑前 N 个（调试用）。注意与 `search`/`dupes` 的 `--limit` 语义不同（那俩默认 50 上限）。

---

## 4. 已知坑 / 注意事项

| 现象 | 原因 | 处理 |
|------|------|------|
| `需要 numpy` | 环境没装 numpy | `pip install numpy`（仅 bandsim 需要，其他子命令不依赖） |
| 首次很慢 | 候选范围内文件首次算频段要读外部盘 | 正常；结果进 `bandprof` 缓存，重跑秒回 |
| 全库跑不动 | 36 万文件逐个读外部 USB 盘（I/O 瓶颈） | 加 `--parallel 4` 多进程提速；或用 `--library`/`--subcat` 限定子集 |
| 找到的音效"同名不同源"语义不对 | 频段相似不懂语义 | 结合 `--kw` 关键词在结果里二次筛选，或人工确认 |
| 压缩格式(mp3/ogg)也能算 | ffmpeg 先解码再分析 | 正常，无需预处理 |
| 候选算不出 / 结果很少 | 外置盘未挂载（路径 404）或文件损坏 | 先 `diskutil mount disk2s2` 挂盘；损坏文件会被安全跳过不缓存 |
| 参考音效报错退出 | 参考文件本身不可读 | 换一个能解码的参考；候选不可读会跳过，只有参考不可读才致命 |
| `bandsim` 跑一半断了 | 会话结束 / 手动中断 | 重跑同命令即可：已缓存的跳过，只补未算的（每 200 提交） |
| 同时跑两个全库扫描(bandsim+compsim)报 `database is locked` | SQLite 同库只允许一个写者, 两个后台任务并发写 `index.db` 互相锁 | 已加 `_exec_retry`/`_commit_retry` 自动重试兜底; 但**不要并发跑两个全库扫描**, 串行(先 bandsim 完再 compsim)更快更稳 |

---

## 6. 导出打包 `--export` 与 无匹配自动分析 `analyze`

### (a) 每次搜索自动在桌面生成"相似音效"文件夹（默认开）
`bandsim` 搜索完，**默认自动**把 Top-K 相似音效复制到 `~/Desktop/sfx_similar/<参考名>/` 并打包同名 `.zip`。**只复制、不动原音效库任何文件**（非破坏，原文件安全）。无需加 `--export` 就会落盘——搜完即有一份桌面素材包。

```bash
python scripts/sfx_library.py bandsim \
  --query "/某参考.wav" --library "音效库" --like "%bed%" \
  --topk 30
# 默认 -> 复制 30 个到 ~/Desktop/sfx_similar/<参考名>/
#       -> 打包成 ~/Desktop/sfx_similar/<参考名>.zip
```
- 想自定义位置：`--export "$HOME/Desktop/sfx_similar/我的结果"`（覆盖默认桌面路径）。
- 想关掉自动落盘（只打印排名）：`--no-export`。
- 文件夹名取参考文件名（去扩展名），同名参考重跑会覆盖该文件夹；不同参考各自独立文件夹。
- 文件名冲突（不同目录同名）自动加 `001_` 前缀，不覆盖。
- "全部" 指本次检索结果里的 Top-K；若候选池很大（如全库 36 万）不想一次导几百个，用 `--topk` 控制数量，避免打包体积爆炸。

### (b) 本地找不到时，自动对参考音效做全面分析
若 `bandsim` 在本地库**一个相似候选都读不出来**（候选全部缺失/损坏），它不再只报"无候选"，而是**自动对参考音效跑一次全面声学分析**并打出报告（含"材质/合成设计指纹"小节），至少给你一份详尽的参考画像。也可单独调用：

```bash
python scripts/sfx_library.py analyze --query "/某参考.wav"          # 文本报告(含材质指纹)
python scripts/sfx_library.py analyze --query "/某参考.wav" --json    # 程序可消费的 JSON
```
分析维度（离线 numpy+ffmpeg，无训练模型）：
- 基础规格：时长 / 采样率 / 声道数（ffprobe）
- 动态：RMS / Peak / Crest（波峰因子）
- 频谱：8 段能量分布、**频谱质心**、85% 能量截止频率、平坦度（越低越有音色/越纯）、瞬态密度
- 节奏：估测 BPM（包络自相关，无 beat 则 ≈0）
- 主导频段
- **材质/合成设计指纹（新增）**：谐波纯度 / 明亮度 / 持续度 / 尾音长度 / 调制深度 / 事件密度 / 起音时间 / 瞬态锐度 8 维（0–1），并给出"推断合成设计"标签（如 长铺底/长尾、高事件密度(近似 Granular·Arp)、明显调制(近似 LFO·Chorus·Vibrato)、谐波音源、暗色滤波/低通 等，启发式仅供参考）

> 诚实边界：`analyze` 是**声学特征画像**，不是"这段音乐表达了什么情绪"的语义理解；BPM/调式/合成设计标签均为粗粒度启发式，作参考用。若要做 CG/视频的"音乐情绪弧"，另有 `scripts/music_emotion.py`。

---

## 7. 材质 / 合成设计相似检索 `compsim`（频段相似之外的另一维度）

`bandsim` 按"频谱分布"找相似，但有两个盲区：**(1)** 频段分布不同、可"声音是这么做成的"其实很像（如两段不同的 shimmer pad，一个偏亮一个偏暗，频段向量差很多，但都是"高调制 + 高颗粒密度 + 缓入 + 谐波源"做出来的）；**(2)** 频段维度根本找不到足够相似的音效。

`compsim` 就是为这两类场景而生：先把参考音效拆成 **8 维"声音材质 / 合成设计"指纹**，再按指纹余弦相似去全库找"合成手法/材质相近"的音效。这正是"找不到相似音效时，分析参考音效组成 → 全库搜相近材质/合成设计"的能力。

### 指纹模型（8 维，均归一化 0–1）
| 维度 | 合成含义 | 高值≈ |
|------|----------|-------|
| tonal    | 谐波纯度（振荡器/采样 vs 噪声发生） | 谐波音源 |
| bright   | 明亮度（滤波/振荡器设计） | 明亮滤波/高通 |
| sustain  | 持续度（铺底 Pad vs 脉冲/断奏） | 长铺底 |
| tail     | 尾音长度（混响/延迟/反馈设计） | 长尾/混响 |
| mod      | 调制深度（LFO/合唱/颤音/镶边设计） | 明显调制 |
| density  | 事件密度（颗粒/纹理/连击设计） | 颗粒/多连击 |
| attack   | 起音时间（拨弦/渐入 swell 设计） | 缓入 swell |
| trans    | 瞬态锐度（打击乐设计） | 打击/瞬态 |

相似度 = 指纹向量的**余弦相似**（0–100%）。指纹来自前 6 秒 leading window，写进 **`matprof`** 表缓存（与 `bandprof` 同库、断点续跑）。全库扫描同样支持 `--parallel N`（并行 4 最优，见 §5 性能说明），compsim 与 bandsim 共用同一并行解码+双指纹管线，跑通一次后两维检索都秒回。

### 快速命令
```bash
# 参考音效 → 按"材质/合成设计"找相似
python scripts/sfx_library.py compsim \
  --query "/Volumes/Backup Plus/SFX/音效库/氛围铺底音效/ambiences/shimmer_amb_05冒险_迷幻.wav" \
  --library "音效库" --subcat 氛围铺底音效 --topk 15 --verbose

# 用 --like 圈定候选池(与 bandsim 同语义), 自动导出到 ~/Desktop/sfx_similar/<参考名>_mat/
python scripts/sfx_library.py compsim \
  --query "/某参考.wav" --library "音效库" --like "%shimmer%,%amb%,%pad%" --topk 20

# analyze 现已含材质指纹小节
python scripts/sfx_library.py analyze --query "/某参考.wav"
```

### 与 bandsim 的关系 & 兜底
- **互补**：频段分布差异大、但合成手法相似的，用 `compsim`；两者都跑、对照看最稳。
- **兜底触发**：`bandsim` 的**最佳相似度 < `--threshold`(默认 0.55)** 时，会自动调用 `compsim` 再搜一次，并把材质相近结果导出到 `<参考名>_mat` 文件夹。`--threshold 0` 可关闭兜底；调高阈值（如 0.7）让兜底更易触发。
- **材质相似 ≠ 语义相似**：仍不懂"两个都是开门声"这类语义；语义靠文件名/子目录或 `--like` 圈定。
- 已知坑、导出打包、只读/缓存、`--like/--notlike/--export/--no-export` 语义，与 `bandsim` 完全一致（见 §6 与上方各节）。

