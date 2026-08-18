# 音频设计师助手 · Audio Designer Helper

> 面向游戏音频设计师的本地工具箱：音乐 / 音效**响度匹配 + 融合混音**、**语音修复**、**音效库检索 / 审计 / 相似查找**、**CG 音乐情绪分析**。
> 纯 ffmpeg + numpy 实现，**离线可用、不调用任何云端模型**。

---

## 这是什么

一个给音频设计师用的「加工」技能，不是「创作」技能。它把你已有的素材精确对齐响度、干净地融在一起、修干净语音、在大库里找到相似的音效、审外包 CG 的音乐情绪弧——**但不替你作曲**。

设计红线（用户硬要求，已写死在流程里）：
- **双方清晰可辨，互不干扰**；**严禁 ducking / 大 sidechain / 自动闪避**
- 干预最小化：不靠疯狂 boost/scoop 频段去「融合」
- 永远 stereo（48kHz / 24bit），不偷偷转 mono

---

## ✅ 已落地能力（6 大模块）

| # | 模块 | 能做什么 | 关键脚本 / 命令 |
|---|------|----------|-----------------|
| 1 | **音乐+音效响度匹配与融合混音** | 被调版 MUS/SFX 精确命中参考版 LUFS（偏差 < 0.10，力争 < 0.03）；混音预览；导出各自独立分轨。适配标准平衡 / Idle 音效 / 音乐作氛围三种场景 | `standard_workflow.py` + ffmpeg 两段式 loudnorm |
| 2 | **VO 语音修复** | `diagnose` 诊断（齿音/削波/底噪频谱提示）→ `auto` 最小干预链路 → `verify` 客观验收（前→后 PASS/⚠️）；`report` 文件夹批量验收扫描（可出 CSV）、`batch` 批量修复。支持 `--declip --declick --denoise --arnndn --deess --target-lufs --fade`，带 OOM 防护 | `voice_cleanup.py` |
| 3 | **音效库管理** | `scan` 建 SQLite 索引（只读不碰原库）→ `search` 文件名模糊检索（可组合 `--library/--ext/--audio-only/--min-size/--like/--notlike`）→ `report` 健康报告 → `dupes` 找重复（size/hash）→ `specs` ffprobe 规格画像。实测索引 **359,800 文件（音频 348,223）** | `sfx_library.py` |
| 4 | **音效频段相似检索（bandsim）** | 参考音效 / 文字描述 → 按 8 频段能量分布余弦相似找「听感接近」的音效；搜完**默认自动导出桌面文件夹 + zip**（只复制不破坏原库）；最佳相似度过低自动兜底转 compsim | `sfx_library.py bandsim` |
| 5 | **音效材质 / 合成设计相似检索（compsim）** | 频段找不到时用：8 维「声音材质 / 合成设计」指纹余弦相似，找「合成手法相近」的音效——频段不同也能找 | `sfx_library.py compsim` |
| 6 | **CG / 视频音乐情绪分析** | 视频自动抽音轨 → 分窗 valence–arousal 情绪时间线 + 近似 BPM；纯 numpy+ffmpeg，**无训练模型** | `music_emotion.py` |

每个模块都有配套的 `references/*.md` 完整工作流文档。

---

## ❌ 诚实边界（做不到 / 没落地，不承诺）

- **不能作曲 / 生成音乐**。本技能只「分析现有音频 + 匹配 / 混合」。生成配乐需要另一个 `audio-cog` 技能，**当前未集成**；库里只有音乐素材积木（loop/drone/sting/cinematic），没有成品 BGM。
- **不做语义理解**。`bandsim` / `compsim` 按**频谱 / 材质**相似，不懂「两个都是开门声」这类语义——语义靠文件名 / 子目录或 `--like` 圈定。
- **Wwise 自动同步**（MixForge 的 `sync-references`）仍是 **🟡 规划中**；当前靠手动导入分轨（已给出 Wwise / FMOD 导入与还原 mix 平衡的完整指引）。
- **情绪分析是粗粒度启发式**，不是精确量表；只分析**音轨声学情绪**，**不分析画面叙事情绪**；混入人声 / 音效会污染（最好喂纯音乐轨）。
- **全库任意秒回检索需先 `prewarm`**（WAV 直读 + 向量化 + 多进程，外部 HDD 约 3–4h 长跑，可断点续跑）；不预热则要把候选限定到子集（库 / 子类）才快。

---

## 🟡 规划中（Roadmap，未实现）

- Wwise / FMOD 自动同步分轨（当前手动）
- 视频逐帧画面情绪 → 配乐建议的端到端链路（当前仅分析音轨情绪；画面分析能力曾用外部视频跑通过 PoC，但**未固化进本技能**）
- 成品 BGM 生成（依赖外部生成技能，不在本仓库范围）

> 上面这些是路线图，**现在还不能用**。README 不会把它们写成「已有功能」。

---

## 快速开始

**依赖**
- `ffmpeg`（必须在 PATH，用于响度测量 / 处理 / 抽音轨）
- `numpy`（仅 bandsim / compsim / analyze / prewarm / music_emotion 需要）：`pip install numpy`
- Python 3.10+（推荐用 WorkBuddy 受管 venv）

**最小可用闭环（混音）**
```bash
# 1) 测量参考 MUS 的 LUFS / 真峰
ffmpeg -hide_banner -nostdin -i "参考/MUS.wav" -af "loudnorm=print_format=json" -f null -
# 2) 迭代施加总增益 G dB 命中目标 LUFS（详见 SKILL.md Step 1 算法）
# 3) 混音预览 + 导出分轨（stereo / 48k / 24bit，禁 ducking）
```

**音效库第一次用**
```bash
python scripts/sfx_library.py scan --root "/你的音效库根" --db ~/.workbuddy/sfx_library/index.db
python scripts/sfx_library.py search --kw whoosh --audio-only
```

**找相似音效（默认自动导出桌面文件夹 + zip）**
```bash
python scripts/sfx_library.py bandsim --query "/参考.wav" --library HOK --topk 20
```

**CG 音乐情绪弧**
```bash
python scripts/music_emotion.py "某CG.mp4" --win 8
```

完整命令、参数安全区、踩坑全集见 `SKILL.md`（本仓库主文档）。

---

## 技术栈

- **ffmpeg**：响度测量（loudnorm）、处理（volume / highpass / equalizer / acompressor / alimiter / amix）、抽音轨
- **numpy**：频段向量、材质指纹、情绪描述子计算
- **SQLite**：音效库索引（外置，不含音效文件本身）
- 离线优先，无 API 调用、无模型下载

---

## 许可证

本仓库仅含技能定义与脚本（SKILL.md + scripts/ + references/ + models/cb.rnnn 语音降噪模型）。音效库素材不在本仓库内。
