#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按高频统计生成预测卷（A 高频核心 / B 均衡覆盖 / C 补漏）。

用法：
    python scripts/make_exam.py
    python scripts/make_exam.py --paper A
    python scripts/make_exam.py --paper all --out-dir .
"""

from __future__ import annotations

import argparse
import json
import random
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
    question_weight,
)

TYPE_NAME = {"single": "单项选择题", "multi": "多项选择题", "fill": "填空题", "short": "简答题"}
TYPE_SHORT = {"single": "单选", "multi": "多选", "fill": "填空", "short": "简答"}
PAPER_NAME = {
    "A": "A 卷 · 高频核心",
    "B": "B 卷 · 均衡覆盖",
    "C": "C 卷 · 补漏冲刺",
}


def q_info_score(q: dict) -> float:
    return len((q.get("stem") or "") + str(q.get("answer_text") or ""))


def build_candidates(questions: list[dict], clusters: list[dict], stats: dict) -> dict[str, list[dict]]:
    by_id = {q["id"]: q for q in questions}
    topic_weight = {t["id"]: t["weight"] for t in stats.get("topics", [])}
    pools: dict[str, list[dict]] = {"single": [], "multi": [], "fill": [], "short": []}
    for c in clusters:
        members = [by_id[i] for i in c.get("members", []) if i in by_id]
        for t in pools:
            qs = [q for q in members if q["type"] == t and not q.get("needs_review")]
            if not qs:
                continue
            q = max(qs, key=q_info_score)
            pools[t].append({
                "cluster": c,
                "question": q,
                "score": round(c["weight"] + topic_weight.get(c["topic"], 0) * 0.3 + question_weight(q) * 0.2, 3),
                "topic": c["topic"],
                "topic_name": c["topic_name"],
                "count": c["count"],
            })
    for t in pools:
        pools[t].sort(key=lambda x: (-x["score"], -x["count"]))
    return pools


def select_paper(pools: dict[str, list[dict]], blueprint: list[dict], mode: str, rng: random.Random) -> list[dict]:
    selected: list[dict] = []
    used_clusters: set[str] = set()
    for spec in blueprint:
        t = spec["type"]
        count = spec["count"]
        pool = pools.get(t, [])
        # 按主题分组
        by_topic: dict[str, list[dict]] = {}
        for item in pool:
            if item["cluster"]["id"] in used_clusters:
                continue
            by_topic.setdefault(item["topic"], []).append(item)
        if mode == "A":
            topic_order = sorted(by_topic, key=lambda k: -max(i["score"] for i in by_topic[k]))
        elif mode == "B":
            topic_order = sorted(by_topic, key=lambda k: -len(by_topic[k]))
        else:
            topic_order = sorted(by_topic, key=lambda k: min(i["score"] for i in by_topic[k]))
        # 轮转选题，保证主题覆盖
        picked: list[dict] = []
        while len(picked) < count:
            progressed = False
            for topic in topic_order:
                if len(picked) >= count:
                    break
                bucket = by_topic.get(topic, [])
                if not bucket:
                    continue
                if mode == "C":
                    item = bucket.pop()  # 从低分端取，做补漏
                else:
                    item = bucket.pop(0)
                if item["cluster"]["id"] in used_clusters:
                    continue
                used_clusters.add(item["cluster"]["id"])
                picked.append(item)
                progressed = True
            if not progressed:
                break
        # 不足时从剩余池补齐
        if len(picked) < count:
            rest = [i for i in pool if i["cluster"]["id"] not in used_clusters]
            if mode == "C":
                rest = list(reversed(rest))
            for item in rest:
                if len(picked) >= count:
                    break
                used_clusters.add(item["cluster"]["id"])
                picked.append(item)
        # 避免同一主题连续太多：A 卷保留分数顺序，B/C 轻量洗牌同主题
        if mode in {"B", "C"}:
            rng.shuffle(picked)
        selected.append({"spec": spec, "items": picked})
    return selected


def render_question(q: dict, index: int) -> list[str]:
    lines = [f"**{index}. {q['stem']}**", ""]
    if q["type"] in {"single", "multi"}:
        for opt in q.get("options", []):
            lines.append(f"- {opt['key']}. {opt['text']}")
        lines.append("")
    elif q["type"] == "fill":
        lines.append("")
    return lines


def paper_markdown(paper_key: str, sections: list[dict], config: dict) -> str:
    exam = config.get("exam", {})
    lines = [f"# {PAPER_NAME.get(paper_key, paper_key)}", ""]
    lines.append(f"> {exam.get('name', '党考')} 预测练习卷  ")
    lines.append(f"> 建议时长：{exam.get('duration_minutes', 90)} 分钟｜满分：{exam.get('total_points', 100)} 分  ")
    lines.append("> 说明：本卷按历史题库的加权频次与主题覆盖生成，用于查漏补缺，不是命题预测，不保证命中。")
    lines.append("")
    lines.append("---")
    lines.append("")
    qno = 0
    answer_lines = ["## 参考答案与依据", ""]
    for sec in sections:
        spec = sec["spec"]
        lines.append(f"## {spec['name']}（{spec['count']} 题，每题 {spec['points']} 分）")
        lines.append("")
        for item in sec["items"]:
            qno += 1
            q = item["question"]
            lines.extend(render_question(q, qno))
            # 答案
            if q["type"] in {"single", "multi"}:
                ans = "、".join(q.get("answer", []))
            elif q["type"] == "fill":
                ans = "；".join(f"（{i}）{b}" for i, b in enumerate(q.get("blanks", []), 1))
            else:
                ans = q.get("answer", "")
            answer_lines.append(f"**{qno}. {ans}**")
            answer_lines.append("")
            answer_lines.append(
                f"> 依据：题组出现 {item['count']} 次｜主题：{item['topic_name']}｜加权分 {item['score']}"
            )
            answer_lines.append("")
    lines.append("---")
    lines.append("")
    lines.extend(answer_lines)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="生成党考预测卷")
    ap.add_argument("--bank", default=str(DEFAULT_BANK))
    ap.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--stats", default=str(ASSETS / "stats.json"))
    ap.add_argument("--out-dir", default=str(ASSETS))
    ap.add_argument("--paper", default="all", choices=["A", "B", "C", "all"])
    args = ap.parse_args()

    bank, taxonomy = load_all(args.bank, args.taxonomy)
    config = load_json(args.config, {})
    stats = load_json(args.stats)
    if not stats:
        raise SystemExit("没有找到 stats.json，请先运行 scripts/analyze.py")
    threshold = config.get("analysis", {}).get("cluster_threshold", 0.72)
    clusters = cluster_questions(bank["questions"], threshold)
    pools = build_candidates(bank["questions"], clusters, stats)
    blueprint = config.get("exam", {}).get("blueprint", [])
    seed = config.get("prediction", {}).get("seed", 20261031)
    out_dir = Path(args.out_dir)
    papers = ["A", "B", "C"] if args.paper == "all" else [args.paper]
    for key in papers:
        rng = random.Random(seed + ord(key))
        sections = select_paper(pools, blueprint, key, rng)
        paper = {
            "meta": {
                "paper": key,
                "name": PAPER_NAME.get(key, key),
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "config": config.get("exam", {}),
                "disclaimer": config.get("prediction", {}).get("note", ""),
            },
            "sections": [
                {
                    "type": sec["spec"]["type"],
                    "name": sec["spec"]["name"],
                    "points": sec["spec"]["points"],
                    "questions": [
                        {**item["question"], "rationale": {
                            "cluster_count": item["count"],
                            "topic": item["topic"],
                            "topic_name": item["topic_name"],
                            "weighted_score": item["score"],
                        }}
                        for item in sec["items"]
                    ],
                }
                for sec in sections
            ],
        }
        dump_json(paper, out_dir / f"预测卷_{key}.json")
        md_path = out_dir / f"预测卷_{key}.md"
        md_path.write_text(paper_markdown(key, sections, config), encoding="utf-8")
        total = sum(len(s["items"]) for s in sections)
        print(f"已生成 {PAPER_NAME.get(key, key)}：{total} 题 -> {md_path}")


if __name__ == "__main__":
    main()
