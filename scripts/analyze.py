#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统计党考题库的高频知识点、主题覆盖、重复题与高频表述，并生成报告。

用法：
    python scripts/analyze.py
    python scripts/analyze.py --top 20
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dangyan_lib import (  # noqa: E402
    ASSETS,
    DEFAULT_BANK,
    DEFAULT_CONFIG,
    DEFAULT_TAXONOMY,
    cluster_questions,
    dump_json,
    load_all,
    load_json,
    normalize_text,
    question_text,
)

STOP_TERMS = {
    "中国", "人民", "发展", "建设", "坚持", "必须", "一个", "我们", "国家", "社会",
    "工作", "问题", "重要", "指出", "报告", "党的", "进行", "实现", "推进", "全面",
    "以下", "下列", "正确", "错误", "主要", "包括", "属于", "什么", "哪些", "如何",
    "可以", "不能", "应当", "关于", "有关", "这个", "那个", "以及", "还要", "就是",
    "第一", "第二", "第三", "方面", "内容", "要求", "任务", "目标", "原则", "基本",
    "民族", "书记", "代表大会", "革命", "开放", "社会主义", "中国共产党", "党中央",
    "总书记", "习近平", "现代化", "中国特色社会主义", "重要思想", "主要表现",
}


def is_meaningful_term(term: str) -> bool:
    if not term or len(term) < 2 or term in STOP_TERMS:
        return False
    if "_" in term or "（" in term or "）" in term or "(" in term or ")" in term:
        return False
    if not re.search(r"[\u4e00-\u9fff]", term):
        return False
    return True


def extract_keywords(text: str) -> set[str]:
    """从题干/答案里补充引号词、数字概括词。"""
    found = set()
    for m in re.finditer(r"“([^”]{2,24})”", text):
        found.add(m.group(1).strip())
    for m in re.finditer(r"“([^“”]{2,24})”", text):
        found.add(m.group(1).strip())
    for m in re.finditer(r"([一二三四五六七八九十两]{1,3}个[一-龥]{2,10})", text):
        found.add(m.group(1))
    for m in re.finditer(r"([一二三四五六七八九十两]{1,3}个[A-Za-z]{2,10})", text):
        found.add(m.group(1))
    return found


def term_ranking(clusters: list[dict], taxonomy: dict, top_n: int = 40) -> list[dict]:
    glossary = set()
    for t in taxonomy.get("topics", []):
        for kw in t.get("keywords", []):
            if is_meaningful_term(kw):
                glossary.add(kw)
    for c in clusters:
        glossary.update(extract_keywords(c.get("stem", "") + " " + str(c.get("answer_text", ""))))

    stats: dict[str, dict] = {}
    for c in clusters:
        stem = c.get("stem", "")
        ans = str(c.get("answer_text", ""))
        for term in glossary:
            if not is_meaningful_term(term):
                continue
            in_stem = term in stem
            in_ans = term in ans
            if not in_stem and not in_ans:
                continue
            s = stats.setdefault(term, {"term": term, "clusters": 0, "weight": 0.0, "stem_hits": 0, "answer_hits": 0})
            s["clusters"] += 1
            s["weight"] += c["weight"] * (1.0 if in_stem else 0.45)
            s["stem_hits"] += 1 if in_stem else 0
            s["answer_hits"] += 1 if in_ans else 0

    terms = [v for v in stats.values() if v["clusters"] >= 2]
    terms.sort(key=lambda x: (-x["clusters"], -x["weight"], -len(x["term"])))

    # 去冗余：若短词是更长高频词的一部分，且覆盖度相近，则去掉短词
    kept = []
    for t in terms:
        redundant = False
        for k in kept:
            if t["term"] != k["term"] and t["term"] in k["term"] and t["clusters"] <= k["clusters"] + 1:
                redundant = True
                break
        if not redundant:
            kept.append(t)
    return kept[:top_n]


def answer_phrase_ranking(clusters: list[dict], top_n: int = 30) -> list[dict]:
    counter: dict[str, dict] = {}
    for c in clusters:
        if not ({"fill", "short"} & set(c.get("types", []))):
            continue
        ans = c.get("answer_text", "")
        if isinstance(ans, list):
            ans = "；".join(ans)
        if not ans:
            continue
        ans = re.sub(r"^\s*答\s*[:：]\s*", "", ans)
        ans = re.sub(r"^[A-D]\s*[、.．]\s*", "", ans)
        # 按标点/序号切分，提取 2-40 字的短语
        parts = re.split(r"[；;。\n①②③④⑤⑥⑦⑧⑨⑩]|[（(]\d+[）)]", ans)
        seen = set()
        for p in parts:
            p = re.sub(r"^[：:，,、\s]+|[：:，,、\s]+$", "", p)
            p = re.sub(r"^\d+[、.．]\s*", "", p)
            p = p.strip()
            if 2 <= len(p) <= 40 and p not in seen:
                seen.add(p)
                item = counter.setdefault(p, {"phrase": p, "clusters": 0, "weight": 0.0})
                item["clusters"] += 1
                item["weight"] += c["weight"]
    out = [v for v in counter.values() if v["clusters"] >= 2]
    out.sort(key=lambda x: (-x["clusters"], -x["weight"], -len(x["phrase"])))
    return out[:top_n]


def build_stats(bank: dict, taxonomy: dict, config: dict, top_n: int = 15) -> dict:
    questions = bank["questions"]
    threshold = config.get("analysis", {}).get("cluster_threshold", 0.72)
    clusters = cluster_questions(questions, threshold)
    tindex = {t["id"]: t for t in taxonomy.get("topics", [])}

    type_counts = collections.Counter(q["type"] for q in questions)
    topic_scores: dict[str, dict] = {}
    for c in clusters:
        primary = c["topic"]
        topic_scores.setdefault(primary, {
            "id": primary,
            "name": c["topic_name"],
            "weight": 0.0,
            "clusters": 0,
            "questions": 0,
            "types": collections.Counter(),
            "stems": [],
        })
        ts = topic_scores[primary]
        ts["weight"] += c["weight"]
        ts["clusters"] += 1
        ts["questions"] += c["count"]
        for t in set(c["types"]):
            ts["types"][t] += 1
        if len(ts["stems"]) < 3:
            ts["stems"].append(c["stem"])
        # 次要主题给 0.25 权重
        for tag in c.get("tags", []):
            if tag == primary or tag not in tindex:
                continue
            topic_scores.setdefault(tag, {
                "id": tag,
                "name": tindex[tag]["name"],
                "weight": 0.0,
                "clusters": 0,
                "questions": 0,
                "types": collections.Counter(),
                "stems": [],
            })
            topic_scores[tag]["weight"] += c["weight"] * 0.25

    topics = []
    for t in topic_scores.values():
        topics.append({
            "id": t["id"],
            "name": t["name"],
            "weight": round(t["weight"], 2),
            "clusters": t["clusters"],
            "questions": t["questions"],
            "types": dict(t["types"]),
            "stems": t["stems"],
        })
    topics.sort(key=lambda x: (-x["weight"], -x["questions"]))

    terms = term_ranking(clusters, taxonomy, config.get("analysis", {}).get("top_terms", 40))
    phrases = answer_phrase_ranking(clusters, 30)

    # 题型 × 主题矩阵
    matrix: dict[str, dict[str, int]] = {}
    for q in questions:
        matrix.setdefault(q["type"], {})
        matrix[q["type"]][q["topic"]] = matrix[q["type"]].get(q["topic"], 0) + 1

    repeat = [c for c in clusters if c["count"] >= 2]
    repeat.sort(key=lambda c: (-c["count"], -c["weight"]))

    high_freq = clusters[: config.get("analysis", {}).get("high_freq_question_count", 40)]

    # 质量提示
    quality = {"needs_review": [], "long_blanks": [], "very_short_answers": [], "duplicate_clusters": len(repeat)}
    for q in questions:
        if q.get("needs_review"):
            quality["needs_review"].append({"id": q["id"], "stem": q["stem"], "reason": q.get("review_reason")})
        if q["type"] == "fill" and any(len(b) > 60 for b in q.get("blanks", [])):
            quality["long_blanks"].append({"id": q["id"], "number": q.get("number"), "stem": q["stem"][:80]})
        if q["type"] == "short" and len(q.get("answer", "")) < 15:
            quality["very_short_answers"].append({"id": q["id"], "number": q.get("number"), "stem": q["stem"][:80]})

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "bank_generated_at": bank.get("meta", {}).get("generated_at"),
            "total_questions": len(questions),
            "total_clusters": len(clusters),
            "cluster_threshold": threshold,
            "counts": dict(type_counts),
        },
        "topics": topics,
        "terms": terms,
        "clusters": high_freq,
        "repeat_clusters": repeat[:30],
        "answer_phrases": phrases,
        "type_topic_matrix": matrix,
        "quality": quality,
    }


def fmt_types(types: list[str] | dict) -> str:
    if isinstance(types, dict):
        items = types.items()
    else:
        items = [(t, 1) for t in types]
    name = {"single": "单选", "multi": "多选", "fill": "填空", "short": "简答"}
    return " / ".join(f"{name.get(k, k)}{'' if v == 1 else f'×{v}'}" for k, v in items)


def write_report(stats: dict, out_path: Path, config: dict) -> None:
    totals = stats["meta"]["counts"]
    lines = []
    lines.append("# 党考高频知识点报告")
    lines.append("")
    lines.append(f"> 生成时间：{stats['meta']['generated_at']}  ")
    lines.append(f"> 题库：{stats['meta']['total_questions']} 道题，归并为 {stats['meta']['total_clusters']} 个题组  ")
    lines.append(f"> 题型：单选 {totals.get('single', 0)} / 多选 {totals.get('multi', 0)} / 填空 {totals.get('fill', 0)} / 简答 {totals.get('short', 0)}")
    lines.append("")
    lines.append("## 一、先看结论")
    lines.append("")
    top_topics = stats["topics"][:5]
    for i, t in enumerate(top_topics, 1):
        lines.append(f"{i}. **{t['name']}**：加权频次 {t['weight']}，涉及 {t['clusters']} 个题组、{t['questions']} 道题。")
    lines.append("")
    if stats["repeat_clusters"]:
        r = stats["repeat_clusters"][0]
        lines.append(f"- 重复最多的一道题出现在 {r['count']} 个位置：{r['stem'][:70]}")
    lines.append(f"- 共发现 {stats['quality']['duplicate_clusters']} 个重复/近重复题组，统计时已按题组去重加权。")
    lines.append("")
    lines.append("## 二、高频知识点排行")
    lines.append("")
    lines.append("| 排名 | 知识点 | 加权频次 | 题组 | 题数 | 代表题 |")
    lines.append("|---:|---|---:|---:|---:|---|")
    for i, t in enumerate(stats["topics"][: config.get("analysis", {}).get("top_topics", 15)], 1):
        stem = (t["stems"][0] if t["stems"] else "").replace("|", "\\|")
        lines.append(f"| {i} | {t['name']} | {t['weight']} | {t['clusters']} | {t['questions']} | {stem[:50]} |")
    lines.append("")
    lines.append("## 三、高频考点词")
    lines.append("")
    lines.append("> 按“出现在多少个题组”排序；括号内为该词覆盖的题组数。适合做睡前速览和考前最后一遍扫读。")
    lines.append("")
    for i, term in enumerate(stats["terms"], 1):
        lines.append(f"{i}. {term['term']}（{term['clusters']} 组）")
    lines.append("")
    lines.append("## 四、重复 / 近重复题")
    lines.append("")
    lines.append("> 同一道题在不同年份、不同题型里反复出现，是最值得优先拿下的“送分题”。")
    lines.append("")
    for c in stats["repeat_clusters"][:15]:
        lines.append(f"- **{c['count']} 次**｜{c['topic_name']}｜{c['stem'][:90]}")
    lines.append("")
    lines.append("## 五、高频答案 / 表述")
    lines.append("")
    for p in stats["answer_phrases"][:20]:
        lines.append(f"- {p['phrase']}（{p['clusters']} 组）")
    lines.append("")
    lines.append("## 六、考前突击建议")
    lines.append("")
    lines.append("按剩余时间选择，不要平均用力：")
    lines.append("")
    lines.append("1. **先拿重复题和高频主题**：上面第一、四部分的内容优先过一遍，目标是看到题干能立刻选出/填出。")
    lines.append("2. **再做限时模拟**：用 `make_exam.py` 生成 A/B 卷，按真实时长做，错题当天回炉。")
    lines.append("3. **最后背高频表述**：填空题和简答题的采分点集中在第三、五部分，考前 1 天只扫这些。")
    lines.append("4. **时政单独处理**：带 2024—2026 年份的题单独成组，考前用官方新闻源核对最新表述。")
    lines.append("")
    lines.append("## 七、数据质量与使用边界")
    lines.append("")
    lines.append(f"- 解析时待人工复核：{len(stats['quality']['needs_review'])} 道。")
    lines.append(f"- 超长填空答案：{len(stats['quality']['long_blanks'])} 道（已保留，答题时可按关键词记忆）。")
    lines.append(f"- 过短简答答案：{len(stats['quality']['very_short_answers'])} 道（建议对照官方表述补充）。")
    lines.append("- 题库是历史资料，个别答案可能存在笔误或表述过时；答疑和教学时会优先核对官方来源，并在冲突时明确提示。")
    lines.append("- 押题是基于历史频率和主题覆盖的相对排序，不是命题预测，也不保证命中。")
    lines.append("")
    lines.append("## 八、统计口径")
    lines.append("")
    lines.append("- 近重复题组：题干字符二元组 Jaccard 相似度 ≥ "
                 f"{stats['meta']['cluster_threshold']}，或短题干被长题干包含且长度比 ≥ 0.8。")
    lines.append("- 加权频次：单选 1.0、多选 1.2、填空 1.1、简答 1.4；含 2026/2025/2024 或“十五五/二十届四中全会”的题额外加权。")
    lines.append("- 主题统计：以主主题计全权重，次要主题计 0.25 权重。")
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="统计党考题库高频知识点")
    ap.add_argument("--bank", default=str(DEFAULT_BANK))
    ap.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--out-dir", default=str(ASSETS))
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    bank, taxonomy = load_all(args.bank, args.taxonomy)
    config = load_json(args.config, {})
    stats = build_stats(bank, taxonomy, config, args.top)
    out_dir = Path(args.out_dir)
    dump_json(stats, out_dir / "stats.json")
    report_path = out_dir / "高频知识点报告.md"
    write_report(stats, report_path, config)
    print(f"已写入 {out_dir / 'stats.json'}")
    print(f"已写入 {report_path}")
    print("Top 主题：")
    for t in stats["topics"][:8]:
        print(f"  {t['name']}: {t['weight']}（{t['clusters']} 组 / {t['questions']} 题）")


if __name__ == "__main__":
    main()
