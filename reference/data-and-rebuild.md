# 数据、生成与维护

## 目录

```text
gduf-dangyuan-exam-coach/
├── SKILL.md
├── assets/
│   ├── source/                 # 原始 docx（只读，不修改）
│   ├── bank.json               # 统一题库
│   ├── taxonomy.json           # 18 个主题与关键词
│   ├── config.json             # 考试结构、分析参数、押题参数
│   ├── stats.json              # 频次、题组、词频、质量统计
│   ├── 高频知识点报告.md        # 人读版分析报告
│   ├── 预测卷_A/B/C.json/.md    # 三套预测卷
│   ├── corrections.json        # 存疑/笔误条目（不静默改答案）
│   ├── trainer_template.html   # 刷题页模板
│   └── 党考刷题.html            # 生成后的离线刷题页
├── scripts/
│   ├── build_bank.py           # docx → bank.json
│   ├── analyze.py              # 频次/题组/报告
│   ├── make_exam.py            # 预测卷
│   ├── build_trainer.py        # 注入数据生成 HTML
│   └── dangyan_lib.py          # 共用函数
├── reference/
└── data/
    └── profile.example.json    # 学习档案 schema
```

## 一键重建

在 skill 根目录运行（Windows 建议加 `PYTHONIOENCODING=utf-8`，避免中文乱码）：

```bash
PYTHONIOENCODING=utf-8 python scripts/build_bank.py
PYTHONIOENCODING=utf-8 python scripts/analyze.py
PYTHONIOENCODING=utf-8 python scripts/make_exam.py
PYTHONIOENCODING=utf-8 python scripts/build_trainer.py
```

依赖：Python 3.10+、`python-docx`。安装：

```bash
python -m pip install python-docx
```

## 题库格式与解析规则

`build_bank.py` 读取 `assets/source/` 下三类文件：

- `选择题题库.docx`：单选题、多选题。正确答案用**红色字体**标注，可能标在选项字母上，也可能标在选项正文上。
- `填空题题库.docx`：题干中的空格可能表现为红色、下划线或空白；答案从红色/下划线片段提取。
- `简答题题库.docx`：题干后紧跟“答：”或参考答案段落。

关键规则：

- 题号支持 `1、` `1.` `1．`，也处理题号与题干粘连（如 `78一切从实际出发`）。
- 选项支持 `A、` `A.` `A．`，也支持题干内联选项、缺首字母、无字母连续选项。
- 红字既可能是完整的 `A、xxx`，也可能只是选项正文片段（如 `民主 B、科学`）。
- 填空题答案支持多空，空位在题干中用 `__1__` 表示。
- 解析异常会写入 `needs_review` 与 `review_reason`，正常应为 0。

## bank.json 字段

```json
{
  "id": "single-xxxxxxxx",
  "type": "single|multi|fill|short",
  "section": "single|multi|fill|short",
  "number": 12,
  "stem": "题干（填空用 __1__ 标记空位）",
  "options": [{"key": "A", "text": "..."}],
  "answer": ["B"],
  "answer_text": "原始答案文本",
  "blanks": ["习近平", "李强"],
  "topic": "congress20",
  "topic_name": "党的二十大与中国式现代化",
  "tags": ["congress20", "thought"],
  "source": "选择题题库.docx",
  "order": 11,
  "needs_review": false,
  "review_reason": ""
}
```

- `answer` 只对选择题有意义；`blanks` 只对填空题有意义；`answer_text` 三种题型都保留。
- `id` 由题型 + 归一化题干生成，题干改动会导致 id 变化。改题库后要同步检查 `corrections.json` 的 id 是否仍能匹配。

## 频次与题组

- 用字符二元组 Jaccard 相似度聚类，阈值默认 `0.72`；包含关系作为兜底。
- 一个题组代表同一考点的不同问法；`count` 是题组内题目数，`weight` 是按题型、年份、政策热度加权后的分数。
- 题组代表题优先选文本更完整、题型更“重”的题。
- 统计结果在 `stats.json`：`clusters`、`repeat_clusters`、`topics`、`terms`、`answer_phrases`、`type_topic_matrix`、`quality`。

## 预测卷

`make_exam.py` 生成三套：

- A 卷：高频核心，优先高频题组。
- B 卷：均衡覆盖，按主题轮转。
- C 卷：补漏冲刺，偏向低频但覆盖盲区。

每套 54 题（30 单选 + 10 多选 + 10 填空 + 4 简答），答案后附题组频次、主题、加权分和免责声明。修改题量或分值后，先改 `config.json` 再重跑。

## 刷题页

`build_trainer.py` 把 `bank.json`、`stats.json`、`config.json`、`corrections.json` 注入 `trainer_template.html`，生成单文件离线 HTML：

```bash
PYTHONIOENCODING=utf-8 python scripts/build_trainer.py
# 自定义输出
PYTHONIOENCODING=utf-8 python scripts/build_trainer.py --out "D:/党考刷题.html"
```

特性：顺序/随机/高频/错题/模拟考/学习数据、题型与主题筛选、搜索、即时判题、填空逐空批改、简答自评、90 分钟计时、答题卡、错题本、收藏、进度导入导出、打印样式。

数据存在浏览器 `localStorage`，键前缀 `dangyan_v1_`；换浏览器或清缓存会丢进度，重要进度先“导出进度”。

## corrections.json

用于记录题库中的存疑、笔误、过时表述。字段：

```json
{
  "id": "short-xxxxxxxx",
  "type": "short",
  "number": 24,
  "severity": "high",
  "issue": "问题说明",
  "suggestion": "建议答案",
  "source": {"title": "来源标题", "url": "https://..."}
}
```

刷题页会在题目上显示“答案存疑”，在反馈和报告里展示提示与来源，但**不会自动替换题库答案**。新增条目后重跑 `build_trainer.py`。

## 新增题库

1. 把新 docx 放进 `assets/source/`，保持文件名或修改 `build_bank.py` 中的路径。
2. 运行 `build_bank.py`，检查输出中的“待复核”数量。
3. 抽查 10—20 题：题干、选项、答案、填空空位。
4. 运行 `analyze.py`、`make_exam.py`、`build_trainer.py`。
5. 如果新增了主题，先更新 `taxonomy.json`，再重建。

## 质量检查清单

- [ ] 题目总数与源文件大致匹配，`needs_review` 为 0 或有明确原因。
- [ ] 选择题每题至少 2 个选项；单选答案唯一；多选答案至少 2 个。
- [ ] 填空题空位数与 `blanks` 数量一致。
- [ ] 简答题有参考答案，无明显截断。
- [ ] 抽查高频题，答案与权威原文一致。
- [ ] 刷题页能打开，选项、填空、下一题、交卷、报告、错题本均可用。
- [ ] 移动端无横向溢出，打印预览不出现侧边栏。
- [ ] 原始 docx 未被修改。

## 已知数据问题（截至 2026-10-31）

- 题库共 381 题：单选 122、多选 65、填空 140、简答 54；解析待复核 0。
- 原文有 8 处下划线空格在 docx 中丢失，已按上下文补回“（ ）”标记，答案未改。
- `corrections.json` 记录 3 条：Q24“三个代表”答案缺少核心三句话；Q25“自立自强”应为“自信自强”；Q19 中心任务多出“十五五开好局起好步”。
- 题库含 2025/2026 时政题，可能随政策更新而过时；考前用官方最新发布复核。
