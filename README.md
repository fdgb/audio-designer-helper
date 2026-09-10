# 音频设计师助手 · Audio Designer Helper

本地音频处理工具箱，面向音效设计、配音、音乐集成工作流。纯 ffmpeg + numpy 实现，离线可用，不调用任何云端模型。

## 功能

- **音乐 / 音效响度匹配与融合混音**：把素材组的 MUS、SFX 精确匹配到参考版的 LUFS（偏差 < 0.10），融合混音后导出各自独立分轨。适配标准平衡、Idle 音效、音乐作氛围三种场景。双方保持清晰可辨，不使用 ducking / 自动闪避。
- **VO 语音修复**：诊断（齿音 / 削波 / 底噪频谱提示）、最小干预自动修复、文件夹批量修复与验收扫描、处理前后客观对照（LUFS / 真峰 / 噪声地板）。
- **音效库管理**：对本地音效库建 SQLite 索引（只读，不改动库内文件），支持文件名模糊检索、健康报告、重复查找、规格画像。实测可索引 35 万+ 文件。
- **音效相似检索**：
  - 频段相似（bandsim）——按 8 段频段能量分布余弦相似，找「听感接近」的音效，搜完自动导出桌面文件夹 + zip；
  - 材质 / 合成设计相似（compsim）——频段找不到时，按 8 维声音材质指纹找「合成手法相近」的音效。
- **CG / 视频音乐情绪分析**：视频自动抽音轨，输出分窗 valence–arousal 情绪时间线与近似 BPM，用于审外包 CG 的音乐情绪弧。

每个模块都有对应的 `references/*.md` 完整工作流文档，主流程见 `SKILL.md`。

## 使用前提

- `ffmpeg`（需在 PATH，用于响度测量、处理、抽音轨）
- `numpy`（bandsim / compsim / analyze / prewarm / music_emotion 需要）：`pip install numpy`
- Python 3.10+

## 快速示例

```bash
# 音效库建索引
python scripts/sfx_library.py scan --root "/你的音效库根" --db ~/.workbuddy/sfx_library/index.db

# 检索音效
python scripts/sfx_library.py search --kw whoosh --audio-only

# 找相似音效（默认导出桌面文件夹 + zip）
python scripts/sfx_library.py bandsim --query "/参考.wav" --library HOK --topk 20

# 语音修复
python scripts/voice_cleanup.py auto VO_raw.wav VO_clean.wav

# CG 音乐情绪分析
python scripts/music_emotion.py "某CG.mp4" --win 8
```

## 说明

- 本工具处理你已有的音频素材（匹配、混合、修复、检索、分析），不含作曲 / 生成音乐功能。
- bandsim / compsim 按频谱与材质相似，不按语义理解（「两个都是开门声」这类语义需靠文件名或 `--like` 圈定）。
- 全库任意秒回检索需先 `prewarm`（约 3–4 小时，可断点续跑）；不预热则把候选限定到某个库 / 子类更快。
- 仓库仅含技能定义与脚本（`models/cb.rnnn` 为语音降噪模型），不含音效库素材本身。


## 新增能力（2026-09 更新）

- **具名概念混合检索（scripts/hybrid_search.py）**：对「门 / 撞击 / 雷 / 警报」等具名概念，用「文件名关键词 + 8 段频谱声学门控」混合检索，解决纯频谱相似度误检。与 bandsim / compsim（找相似）互补。
- **粉红噪音校准（scripts/gen_pinknoise.py）**：一键生成 -23 LUFS 粉红噪音，用于监听校准锚点。
- **响度分析（scripts/analyze_loudness.py）**：单文件 LUFS / 真峰 / RMS / 质心验收。
- **离线索引器（scripts/build_index.py）**：为混合检索建 `index.db`（只读、脱敏、参数化）。
- **能力账本（CAPABILITY-LEDGER.json）**：声明能力实测状态，供二次检验对账。

新增脚本依赖：`pip install numpy soundfile pyloudnorm`（见 `requirements.txt`）。
