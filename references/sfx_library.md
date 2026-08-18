# 音效库管理模块（SFX Library Management）

> 配套脚本：`scripts/sfx_library.py`
> 适用：游戏音频工程师的个人/商业音效库（含参考库 + 个人交付物）的检索、审计、去重、规格画像。
> 场景触发词：音效库管理、盘库、找音效、SFX library、去重、规格审计、音效库扫描

---

## 0. 设计原则（红线）

1. **只读优先**：所有命令除生成索引 DB 外，**不修改/不删除库内任何文件**。清理、去重、转码都是"先出报告 → 你确认 → 再执行"的两段式。
2. **索引外置**：索引 SQLite 默认存 `~/.workbuddy/sfx_library/index.db`，**绝不写入音效库目录**（避免污染备份盘、避免被下次扫描卷进去）。
3. **最小干预**：能检索就不搬动；能抽样审计就不全量转码。
4. **一遍扫描，多次查询**：619k 文件扫一次约 7 分钟，之后所有检索/报告都是毫秒级 SQLite 查询。

---

## 1. 建索引（scan）

```bash
python scripts/sfx_library.py scan \
  --root "/Volumes/Backup Plus/SFX" \
  --db ~/.workbuddy/sfx_library/index.db
```

- 单遍 `os.walk`，对每个文件记录：路径、顶层库、子目录、文件名、扩展名、大小、mtime、类型（audio/other/junk）。
- 自动跳过库内 `.workbuddy` 目录（不把索引工具自身卷进去）。
- `junk` 判定：`._*` / `.DS_Store` / 其他点开头隐藏文件。
- `audio` 判定：扩展名在白名单（wav/aif/mp3/flac/ogg/ape/wem/…）。
- 重建是幂等的（先 DROP 再建）。改了库内容后重跑即可。

---

## 2. 检索（search）—— 最常用的能力

```bash
# 全库搜 whoosh（默认排除垃圾文件）
python scripts/sfx_library.py search --kw whoosh --audio-only

# 限定 HOK 库 + UI 关键词
python scripts/sfx_library.py search --library HOK --kw UI --limit 20

# 按格式 + 大小过滤（找大于 10MB 的 wav）
python scripts/sfx_library.py search --ext wav --min-size 10485760

# 需要连垃圾一起搜时显式开
python scripts/sfx_library.py search --kw UI --include-junk
```

- 模糊匹配文件名（`LIKE %kw%`），大小写不敏感。
- 可组合：`--library` / `--ext` / `--audio-only` / `--min-size` / `--max-size` / `--limit`。

---

## 3. 库健康报告（report）

```bash
python scripts/sfx_library.py report
```

输出：总量 / 音频占比 / 垃圾文件数 / 各顶层库计数 / 格式分布（不含垃圾）/ 非音频混杂 Top 目录。

---

## 4. 重复文件（dupes）—— 注意"体积碰撞 ≠ 重复"

```bash
# 快速候选：同字节大小分组（秒级，但误报多）
python scripts/sfx_library.py dupes --mode size --limit 20

# 精确：先做 size 分组，再对碰撞组做 md5（慢，建议后台）
python scripts/sfx_library.py dupes --mode hash --limit 50
```

**关键坑**（已实测）：
- 5.1 环绕素材（L/R/C/LFE/Surround 各通道）天然同字节大小 → 体积碰撞组里的大组往往是**合法多声道 stem，不是重复**。
- 等长 loop / hit（如 `HITS/` 下多个恰好等长的 wav）也会体积碰撞，但不是重复。
- 因此：size 模式只用来**缩小范围**；真重复必须走 `hash` 模式，且结果要**人工确认同源**再处理（保留一份、其余移入 `_dupes` 归档或改软链）。

---

## 5. 规格画像（specs）—— 接入引擎前的格式审计

```bash
# 全库随机抽样 200 个音频做 ffprobe
python scripts/sfx_library.py specs --sample 200

# 只查某个库
python scripts/sfx_library.py specs --library HOK --sample 100

# 全量（慢，后台）：遍历所有音频
python scripts/sfx_library.py specs --deep
```

- 每个音频取：采样率 / 位深 / 声道数 / 时长。
- 结果存 `specs` 表，按 (sr,bits,ch) 聚合输出分布。
- 用途：确认交付库是否统一在 48k/24bit/stereo；发现 44.1k 遗留、压缩格式（bits=0 即 mp3/ogg/aac）、5.1 多声道等"异类"，决定要不要做规格规整。

---

## 6. 与主工作流（响度匹配/混音）的衔接

典型闭环：
1. `search` 找到需要的参考音效 →
2. 用主工作流把它**响度匹配**到你的目标 LUFS、或**融合混音**进场景 →
3. 交付前用 VO 模块（若含人声）做**语音修复** →
4. 用 `specs` 确认产物规格符合引擎要求（48k/24bit）。

音效库管理是"**找原料**"的环节，主工作流是"**加工**"的环节，VO 模块是"**修人声**"的环节——三者共用同一个 skill。

---

## 7. 已知坑 / 注意事项

| 现象 | 原因 | 处理 |
|------|------|------|
| 检索结果出现 `._XXX.wav` | 这些是 macOS 垃圾影子文件 | 默认已排除；要连搜加 `--include-junk` |
| dupes 大组不是真重复 | 5.1 stem / 等长 loop 体积相同 | 只用 size 缩小范围，真重复走 hash + 人工确认 |
| specs 显示 `0bit` | mp3/ogg/aac 等压缩格式无 PCM 位深 | 正常，标记其为压缩格式即可 |
| scan 慢（几分钟） | 619k 文件在外部 USB 盘 | 只读一次，之后靠索引；内容变动后重跑 |
| database is locked | 多个 specs 并发写 | 已加 busy_timeout；不要同时跑两个写命令 |
| 视频/文档混在音效目录 | 历史拷贝习惯 | 检索用 `--audio-only`；或归到 `_previews` 子目录 |
