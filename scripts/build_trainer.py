#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 bank.json + stats.json 注入单文件 HTML 刷题页。

用法：
    python scripts/build_trainer.py
    python scripts/build_trainer.py --out "党考刷题.html"
"""

from __future__ import annotations

import argparse
import json
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
    load_all,
    load_json,
)

TEMPLATE = ASSETS / "trainer_template.html"


def main() -> None:
    ap = argparse.ArgumentParser(description="生成离线 HTML 刷题页")
    ap.add_argument("--bank", default=str(DEFAULT_BANK))
    ap.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--stats", default=str(ASSETS / "stats.json"))
    ap.add_argument("--corrections", default=str(ASSETS / "corrections.json"))
    ap.add_argument("--out", default=str(ASSETS / "党考刷题.html"))
    ap.add_argument("--title", default="党考突击 · 刷题台")
    args = ap.parse_args()

    bank, taxonomy = load_all(args.bank, args.taxonomy)
    config = load_json(args.config, {})
    stats = load_json(args.stats, {})
    corrections = load_json(args.corrections, {})
    correction_by_id = {item["id"]: item for item in corrections.get("items", []) if item.get("id")}
    if not stats:
        raise SystemExit("没有找到 stats.json，请先运行 scripts/analyze.py")
    if not TEMPLATE.exists():
        raise SystemExit(f"缺少模板：{TEMPLATE}")

    threshold = config.get("analysis", {}).get("cluster_threshold", 0.72)
    clusters = cluster_questions(bank["questions"], threshold)
    freq_by_id: dict[str, dict] = {}
    for c in clusters:
        for mid in c.get("members", []):
            freq_by_id[mid] = {
                "freq": c["count"],
                "weight": c["weight"],
                "clusterId": c["id"],
            }

    questions = []
    for q in bank["questions"]:
        f = freq_by_id.get(q["id"], {"freq": 1, "weight": 1.0, "clusterId": q["id"]})
        questions.append({
            "id": q["id"],
            "type": q["type"],
            "number": q.get("number"),
            "stem": q.get("stem", ""),
            "options": q.get("options", []),
            "answer": q.get("answer", []),
            "blanks": q.get("blanks", []),
            "answerText": q.get("answer_text", ""),
            "topic": q.get("topic", "other"),
            "topicName": q.get("topic_name", "其他/未分类"),
            "tags": q.get("tags", []),
            "source": q.get("source", ""),
            "order": q.get("order", 0),
            "freq": f["freq"],
            "weight": f["weight"],
            "clusterId": f["clusterId"],
            "needsReview": bool(q.get("needs_review")),
            "correction": correction_by_id.get(q["id"]),
        })

    topics = [
        {
            "id": t["id"],
            "name": t["name"],
            "weight": t.get("weight", 0),
            "clusters": t.get("clusters", 0),
            "questions": t.get("questions", 0),
        }
        for t in stats.get("topics", [])
    ]
    terms = [{"term": t["term"], "clusters": t["clusters"]} for t in stats.get("terms", [])[:40]]

    data = {
        "meta": {
            "title": args.title,
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "bankGeneratedAt": bank.get("meta", {}).get("generated_at"),
            "counts": bank.get("meta", {}).get("counts", {}),
            "total": len(questions),
        },
        "config": config,
        "topics": topics,
        "terms": terms,
        "questions": questions,
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    template = TEMPLATE.read_text(encoding="utf-8")
    html = template.replace("__DATA_JSON__", payload).replace("__TITLE__", args.title)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"已生成 {out}（{out.stat().st_size / 1024:.0f} KB，{len(questions)} 题）")


if __name__ == "__main__":
    main()
