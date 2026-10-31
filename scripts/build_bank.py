#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把党考历史题库（docx）解析成统一的 bank.json。

支持三类文件：
- 选择题：红色字体标出正确选项（单选/多选）
- 填空题：红色（通常带下划线）标出答案
- 简答题：题号行 + 后续“答：...”参考答案

用法：
    python scripts/build_bank.py
    python scripts/build_bank.py --input assets/source --output assets/bank.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import docx
except ImportError:  # pragma: no cover
    print(
        "缺少 python-docx。请先运行：python -m pip install python-docx",
        file=sys.stderr,
    )
    raise SystemExit(1)

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = SKILL_ROOT / "assets" / "source"
DEFAULT_OUTPUT = SKILL_ROOT / "assets" / "bank.json"
TAXONOMY_PATH = SKILL_ROOT / "assets" / "taxonomy.json"

RED_RGB = {
    "FF0000",
    "EE0000",
    "BF0000",
    "C00000",
    "E60000",
    "CC0000",
    "D90000",
    "FF3333",
    "E63333",
}

QUESTION_START_RE = re.compile(r"^\s*(\d{1,3})\s*[、.．]\s*")
OPTION_START_RE = re.compile(r"^\s*([A-D])\s*[、.．]\s*")
OPTION_SPLIT_RE = re.compile(r"(?<![A-Za-z])([A-D])\s*[、.．]\s*")
SECTION_RE = re.compile(r"^[一二三四五六七八九十]+\s*[、.．]")
PUNCT_ONLY_RE = re.compile(r"^[\s，。；、：,.!?！？·\-—_（）()“”‘’《》<>]+$")
BLANK_MARK_RE = re.compile(r"[（(][\s_＿]*[）)]")


def clean(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = text.replace("\u2003", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# 题库原文中少量下划线空格在转换时丢失，按上下文补回“（ ）”标记（不改动答案）。
STEM_REPAIRS = [
    (re.compile(r"^在有中国特色的社会主义政党制度中，民主党派是$"), "在有中国特色的社会主义政党制度中，民主党派是（ ）。"),
    (re.compile(r"党的_永远在路上"), "党的（ ）永远在路上"),
    (re.compile(r"抓党建促 ，"), "抓党建促（ ），"),
    (re.compile(r"分别是一，实现"), "分别是（ ），实现"),
    (re.compile(r"货物贸易总额居，吸引"), "货物贸易总额居（ ），吸引"),
    (re.compile(r"^贯彻以坚定的意志品质维护"), "贯彻（ ），以坚定的意志品质维护"),
    (re.compile(r"加快建设 、科技强国"), "加快建设（ ）、科技强国"),
    (re.compile(r"鼓励依法自愿 。"), "鼓励依法自愿（ ）。"),
]


def repair_stem(stem: str) -> str:
    for pattern, replacement in STEM_REPAIRS:
        stem = pattern.sub(replacement, stem)
    return clean(stem)


def norm_key(text: str) -> str:
    """用于去重/匹配的归一化文本。"""
    text = re.sub(r"^\s*\d+\s*[、.．]\s*", "", text)
    text = re.sub(r"[A-D]\s*[、.．]", "", text)
    text = re.sub(r"[\s，。；、：,.!?！？·\-—_（）()“”‘’《》<>]+", "", text)
    return text.lower()


def stable_id(prefix: str, stem: str, used: set[str]) -> str:
    base = f"{prefix}-{hashlib.sha1(norm_key(stem).encode('utf-8')).hexdigest()[:10]}"
    if base not in used:
        used.add(base)
        return base
    i = 2
    while f"{base}-{i}" in used:
        i += 1
    used.add(f"{base}-{i}")
    return f"{base}-{i}"


def is_red(run) -> bool:
    color = run.font.color
    if not color or not color.rgb:
        return False
    return str(color.rgb).upper() in RED_RGB


def run_text(run) -> str:
    return run.text or ""


def paragraph_runs(paragraph):
    return [(run_text(r), is_red(r), bool(r.underline)) for r in paragraph.runs]


def red_fragments(paragraph, include_underline: bool = False) -> list[str]:
    frags: list[str] = []
    buf: list[str] = []
    for text, red, underline in paragraph_runs(paragraph):
        if red or (include_underline and underline):
            if text:
                buf.append(text)
        else:
            if buf:
                frags.append("".join(buf))
                buf = []
    if buf:
        frags.append("".join(buf))
    return [clean(f) for f in frags if clean(f)]


def split_options(line: str) -> list[tuple[str, str]]:
    """把一行中的 A、xxx B、yyy 拆成选项。

    题库里存在 “多方协同 B、立体高效 C、...” 这种缺 A 字母的写法，
    这里会把第一个选项标记前的文字补成前一个字母。
    """
    line = clean(line)
    matches = list(OPTION_SPLIT_RE.finditer(line))
    if not matches:
        return []
    out: list[tuple[str, str]] = []
    leading = clean(line[: matches[0].start()])
    if leading:
        first_key = matches[0].group(1).upper()
        if first_key > "A":
            out.append((chr(ord(first_key) - 1), leading))
        else:
            out.append(("A", leading))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
        text = clean(line[start:end])
        text = re.sub(r"^[A-D]\s*[、.．]\s*", "", text)
        if text:
            out.append((match.group(1), text))
    return out


def is_option_line(line: str) -> bool:
    line = clean(line)
    return bool(OPTION_START_RE.match(line)) or len(OPTION_SPLIT_RE.findall(line)) >= 2


def option_text_norm(text: str) -> str:
    text = re.sub(r"^\s*[A-D]\s*[、.．]\s*", "", text)
    return norm_key(text)


def parse_choice_docx(path: Path) -> tuple[list[dict], list[str]]:
    document = docx.Document(str(path))
    paragraphs = [p for p in document.paragraphs if clean(p.text)]
    questions: list[dict] = []
    warnings: list[str] = []
    used_ids: set[str] = set()

    section = "single"
    current: dict | None = None
    order = 0
    last_number = 0

    def finalize():
        nonlocal current, order
        if not current:
            return
        q = current
        current = None
        options = q["options"]
        if len(options) < 2:
            q["needs_review"] = True
            q["review_reason"] = "选项数量不足，可能是解析异常"
        # 收集红字
        reds: list[str] = []
        for para in q["paras"]:
            reds.extend(red_fragments(para))
        reds = [r for r in reds if not PUNCT_ONLY_RE.match(r)]
        answer: list[str] = []
        answer_fragments: list[str] = []

        def match_option_text(fragment: str) -> list[str]:
            fnorm = option_text_norm(fragment)
            if not fnorm:
                return []
            matched = []
            for opt in options:
                onorm = option_text_norm(opt["text"])
                if not onorm:
                    continue
                if fnorm == onorm or (len(fnorm) >= 4 and (fnorm in onorm or onorm in fnorm)):
                    matched.append(opt["key"])
            return matched

        for frag in reds:
            matches = list(OPTION_SPLIT_RE.finditer(frag))
            if matches:
                # 红字段可能从 A 选项的正文中间开始（如 “民主 B、科学”）
                leading = clean(frag[: matches[0].start()])
                if leading:
                    answer.extend(match_option_text(leading))
                answer.extend(m.group(1) for m in matches)
                answer_fragments.append(frag)
                continue
            # 红字可能只是选项内容，尝试按文本匹配
            matched = match_option_text(frag)
            if matched:
                answer.extend(matched)
                answer_fragments.append(frag)
            else:
                # 红字出现在题干中（少见），保留线索
                answer_fragments.append(frag)
        answer = sorted(set(answer))
        if not answer:
            q["needs_review"] = True
            q["review_reason"] = "未识别到红色答案标记"
        elif q["section"] == "single" and len(answer) > 1:
            q["needs_review"] = True
            q["review_reason"] = "单选题识别到多个答案，请复核"
        elif q["section"] == "multi" and len(answer) == 1:
            q["needs_review"] = True
            q["review_reason"] = "多选题只识别到一个答案，请复核"
        q["answer"] = answer
        q["answer_text"] = "；".join(answer_fragments)
        q.pop("paras", None)
        q["order"] = order
        order += 1
        if q["type"] in ("single", "multi"):
            q["stem"] = repair_stem(q["stem"])
        q["id"] = stable_id(q["type"], q["stem"], used_ids)
        questions.append(q)

    for para in paragraphs:
        text = clean(para.text)
        if not text:
            continue
        if text.startswith("选择题题库"):
            continue
        if "单项选择" in text and SECTION_RE.match(text):
            finalize()
            section = "single"
            continue
        if "多项选择" in text and SECTION_RE.match(text):
            finalize()
            section = "multi"
            continue
        if SECTION_RE.match(text) and len(text) < 20:
            # 其他大题标题
            finalize()
            continue

        if is_option_line(text):
            if current is None:
                current = {
                    "type": section,
                    "section": section,
                    "number": None,
                    "stem": "",
                    "options": [],
                    "paras": [],
                }
            current["paras"].append(para)
            for key, opt_text in split_options(text):
                if not any(o["key"] == key for o in current["options"]):
                    current["options"].append({"key": key, "text": opt_text})
            current["has_options"] = True
            continue

        # 非选项行：遇到新题号一定先收尾
        if current is not None and QUESTION_START_RE.match(text):
            finalize()
            current = None
        elif current is not None and current.get("has_options"):
            # 无字母选项连续出现时，继续收集到 4 个
            if (
                current.get("unlettered")
                and len(current["options"]) < 4
                and not is_option_line(text)
                and len(text) <= 80
            ):
                key = "ABCD"[len(current["options"])]
                current["options"].append({"key": key, "text": clean(text)})
                current["paras"].append(para)
                continue
            finalize()
            current = None

        if current is None:
            m = QUESTION_START_RE.match(text)
            number = int(m.group(1)) if m else None
            stem = QUESTION_START_RE.sub("", text, count=1) if m else text
            if m:
                last_number = number or last_number
            else:
                # 题号与题干粘连且无分隔符，如“78一切从实际出发……”：仅当序号连续时才剥离
                glued = re.match(r"^\s*(\d{1,3})(?=[\u4e00-\u9fff])", stem)
                if glued and int(glued.group(1)) == last_number + 1:
                    number = int(glued.group(1))
                    last_number = number
                    stem = stem[glued.end():]
            stem = clean(stem)
            inline_options: list[tuple[str, str]] = []
            # 少数题干与 A 选项挤在同一段，如 “123、……。A. 权威和集中统一领导”
            inline = OPTION_SPLIT_RE.search(stem)
            if inline and inline.start() > 0:
                inline_options = split_options(stem[inline.start():])
                stem = clean(stem[: inline.start()])
            current = {
                "type": section,
                "section": section,
                "number": number,
                "stem": stem,
                "options": [],
                "paras": [para],
                "has_options": bool(inline_options),
            }
            if inline_options:
                for key, opt_text in inline_options:
                    current["options"].append({"key": key, "text": opt_text})
            continue

        # 当前题尚无选项：判断是否是“无字母选项”（题库尾部存在）
        if (
            current.get("number") is not None
            or current.get("stem")
        ) and BLANK_MARK_RE.search(current["stem"]):
            t = clean(text)
            if (
                len(current["options"]) < 4
                and len(t) <= 60
                and not re.match(r"^\d", t)
                and "。" not in t
                and "？" not in t
            ):
                key = "ABCD"[len(current["options"])] if len(current["options"]) < 4 else None
                if key:
                    current["options"].append({"key": key, "text": t})
                    current["paras"].append(para)
                    current["has_options"] = True
                    current["unlettered"] = True
                    continue
        # 题干续行
        current["stem"] = clean(current["stem"] + " " + text)
        current["paras"].append(para)

    finalize()

    # 清理：去掉选项文本里残留的题号/重复选项
    for q in questions:
        seen = set()
        cleaned_options = []
        for opt in q["options"]:
            if opt["key"] in seen:
                continue
            seen.add(opt["key"])
            opt["text"] = clean(opt["text"])
            cleaned_options.append(opt)
        q["options"] = cleaned_options
        q["stem"] = clean(q["stem"])
        q["stem"] = re.sub(r"\s*[（(]\s+[）)]", "（ ）", q["stem"])
        if len(q["options"]) < 2:
            q["needs_review"] = True
            if "选项数量不足" not in (q.get("review_reason") or ""):
                q["review_reason"] = (q.get("review_reason") or "") + "；选项数量不足"
        if not q["stem"]:
            q["needs_review"] = True
            q["review_reason"] = (q.get("review_reason") or "") + "；题干为空"
        q["source"] = path.name
    return questions, warnings


def fill_from_runs(paragraph) -> tuple[str, list[str]]:
    """根据 run 样式重建填空题题干和答案。

    红色或下划线文字视为答案候选；连续候选合并为一个空。
    夹在两个答案之间的纯标点保留在题干中，用作分隔符。
    """
    stem_parts: list[str] = []
    blanks: list[str] = []
    buf: list[str] = []

    def flush():
        nonlocal buf
        if not buf:
            return
        value = clean("".join(buf)).strip("，。；、：:,.!?！？ ")
        if value:
            blanks.append(value)
            stem_parts.append(f"__{len(blanks)}__")
        buf = []

    for text, red, underline in paragraph_runs(paragraph):
        if not text:
            continue
        candidate = red or underline
        if candidate:
            if PUNCT_ONLY_RE.match(text):
                if buf:
                    flush()
                stem_parts.append(text)
            else:
                buf.append(text)
        else:
            flush()
            stem_parts.append(text)
    flush()
    stem = clean("".join(stem_parts))
    return stem, blanks


def parse_fill_docx(path: Path) -> tuple[list[dict], list[str]]:
    document = docx.Document(str(path))
    paragraphs = [p for p in document.paragraphs if clean(p.text)]
    questions: list[dict] = []
    warnings: list[str] = []
    used_ids: set[str] = set()
    order = 0
    for para in paragraphs:
        text = clean(para.text)
        if "填空题题库" in text or SECTION_RE.match(text):
            continue
        m = QUESTION_START_RE.match(text)
        number = int(m.group(1)) if m else None
        base_stem = QUESTION_START_RE.sub("", text, count=1) if m else text
        stem, blanks = fill_from_runs(para)
        stem = QUESTION_START_RE.sub("", stem, count=1).strip()
        if not stem:
            stem, blanks = clean(base_stem), []

        # 题库中存在跨段落的续写（无题号），合并到上一题
        if number is None and questions:
            prev = questions[-1]
            if not blanks:
                prev["stem"] = clean(prev["stem"] + " " + stem)
                continue
            offset = len(prev["blanks"])
            for i in range(len(blanks), 0, -1):
                stem = stem.replace(f"__{i}__", f"__{i + offset}__")
            prev["stem"] = clean(prev["stem"] + " " + stem)
            prev["blanks"].extend(blanks)
            prev["answer"] = prev["blanks"]
            prev["answer_text"] = "；".join(prev["blanks"])
            continue

        q = {
            "type": "fill",
            "section": "fill",
            "number": number,
            "stem": stem,
            "blanks": blanks,
            "answer": blanks,
            "answer_text": "；".join(blanks),
            "options": [],
            "source": path.name,
            "order": order,
            "needs_review": False,
            "review_reason": "",
        }
        if not blanks:
            q["needs_review"] = True
            q["review_reason"] = "未识别到红色/下划线答案"
        if number is None:
            q["needs_review"] = True
            q["review_reason"] = (q["review_reason"] + "；题号缺失").strip("；")
        order += 1
        q["id"] = stable_id("fill", q["stem"], used_ids)
        questions.append(q)
    return questions, warnings


def parse_short_docx(path: Path) -> tuple[list[dict], list[str]]:
    document = docx.Document(str(path))
    paragraphs = [clean(p.text) for p in document.paragraphs if clean(p.text)]
    questions: list[dict] = []
    warnings: list[str] = []
    used_ids: set[str] = set()
    order = 0
    current: dict | None = None
    for text in paragraphs:
        if text.startswith("简答题题库") or SECTION_RE.match(text):
            continue
        m = QUESTION_START_RE.match(text)
        if m:
            if current:
                questions.append(current)
            number = int(m.group(1))
            stem = clean(QUESTION_START_RE.sub("", text, count=1))
            current = {
                "type": "short",
                "section": "short",
                "number": number,
                "stem": stem,
                "options": [],
                "answer": "",
                "answer_text": "",
                "source": path.name,
                "order": order,
                "needs_review": False,
                "review_reason": "",
            }
            order += 1
            continue
        if current is not None:
            current["answer"] = clean(current["answer"] + "\n" + text)
    if current:
        questions.append(current)
    for q in questions:
        ans = q["answer"]
        ans = re.sub(r"^\s*答\s*[:：]\s*", "", ans)
        q["answer"] = ans
        q["answer_text"] = ans
        if not ans:
            q["needs_review"] = True
            q["review_reason"] = "未识别到参考答案"
        q["id"] = stable_id("short", q["stem"], used_ids)
    return questions, warnings


def load_taxonomy() -> dict:
    if not TAXONOMY_PATH.exists():
        return {"topics": []}
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


def tag_question(q: dict, taxonomy: dict) -> None:
    text = " ".join(
        [
            q.get("stem", ""),
            " ".join(o.get("text", "") for o in q.get("options", [])),
            q.get("answer_text", "") if isinstance(q.get("answer_text"), str) else " ".join(q.get("answer_text") or []),
        ]
    )
    scores = []
    stem_text = q.get("stem", "")
    for topic in taxonomy.get("topics", []):
        score = 0
        for kw in topic.get("keywords", []):
            # 题干决定题目考点，答案/选项中的关键词只作辅助，避免被答案长文本带偏
            count = text.count(kw) + 2 * stem_text.count(kw)
            if count:
                score += count * (1 + min(len(kw), 8) * 0.25)
        if score:
            scores.append((score, topic))
    if not scores:
        q["topic"] = "other"
        q["topic_name"] = "其他/未分类"
        q["tags"] = []
        return
    scores.sort(key=lambda x: (-x[0], x[1]["id"]))
    primary = scores[0]
    q["topic"] = primary[1]["id"]
    q["topic_name"] = primary[1]["name"]
    max_score = primary[0]
    q["tags"] = [
        t["id"]
        for s, t in scores
        if s >= max(1.0, max_score * 0.45)
    ][:4]
    if q["topic"] not in q["tags"]:
        q["tags"].insert(0, q["topic"])


def main() -> None:
    parser = argparse.ArgumentParser(description="解析党考题库 docx 为 bank.json")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="题库目录或单个 docx")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 bank.json")
    args = parser.parse_args()

    input_path = Path(args.input)
    if input_path.is_file():
        files = [input_path]
    else:
        files = sorted(input_path.glob("*.docx"))
    if not files:
        print(f"没有找到 docx：{input_path}", file=sys.stderr)
        raise SystemExit(1)

    all_questions: list[dict] = []
    all_warnings: list[str] = []
    sources = []
    for path in files:
        name = path.name
        if "选择" in name:
            qs, warns = parse_choice_docx(path)
        elif "填空" in name:
            qs, warns = parse_fill_docx(path)
        elif "简答" in name:
            qs, warns = parse_short_docx(path)
        else:
            print(f"跳过未识别文件：{name}", file=sys.stderr)
            continue
        all_questions.extend(qs)
        all_warnings.extend(f"{name}: {w}" for w in warns)
        sources.append({"file": name, "count": len(qs)})

    taxonomy = load_taxonomy()
    for q in all_questions:
        tag_question(q, taxonomy)

    # 统计
    counts = {}
    for q in all_questions:
        counts[q["type"]] = counts.get(q["type"], 0) + 1
    needs_review = [q for q in all_questions if q.get("needs_review")]

    bank = {
        "meta": {
            "version": 1,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "sources": sources,
            "counts": counts,
            "total": len(all_questions),
            "needs_review": len(needs_review),
            "schema": "dangyan-bank-v1",
        },
        "questions": all_questions,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已写入 {output}")
    print(f"题目总数：{len(all_questions)}  {counts}")
    print(f"待复核：{len(needs_review)}")
    for q in needs_review[:15]:
        print(f"  - [{q['type']}] {q.get('number')} {q['stem'][:50]} -> {q.get('review_reason')}")
    if len(needs_review) > 15:
        print(f"  ... 其余 {len(needs_review) - 15} 条见 bank.json")


if __name__ == "__main__":
    main()
