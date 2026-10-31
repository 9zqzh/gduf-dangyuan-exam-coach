#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""党考 skill 的共享工具：加载、归一化、近重复聚类、权重。"""

from __future__ import annotations

import json
import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets"
DEFAULT_BANK = ASSETS / "bank.json"
DEFAULT_TAXONOMY = ASSETS / "taxonomy.json"
DEFAULT_CONFIG = ASSETS / "config.json"


def load_json(path: Path | str, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(obj, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def normalize_text(text: str) -> str:
    text = text or ""
    text = re.sub(r"__\d+__", "", text)
    text = re.sub(r"^\s*\d+\s*[、.．]\s*", "", text)
    text = re.sub(r"[A-D]\s*[、.．]", "", text)
    text = re.sub(r"[\s，。；、：,.!?！？·\-—_（）()“”‘’《》<>【】\[\]…—]+", "", text)
    return text.lower()


def char_bigrams(text: str) -> set[str]:
    text = normalize_text(text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def question_text(q: dict) -> str:
    parts = [q.get("stem", "")]
    if q.get("options"):
        parts.extend(o.get("text", "") for o in q["options"])
    ans = q.get("answer_text", "")
    if isinstance(ans, list):
        ans = " ".join(ans)
    parts.append(ans or "")
    return " ".join(parts)


def question_weight(q: dict) -> float:
    base = {"single": 1.0, "multi": 1.2, "fill": 1.1, "short": 1.4}.get(q.get("type"), 1.0)
    text = question_text(q)
    recency = 1.0
    if "2026" in text:
        recency = 1.25
    elif "2025" in text:
        recency = 1.15
    elif "2024" in text:
        recency = 1.08
    elif "2023" in text:
        recency = 1.03
    if "二十届四中全会" in text or "十五五" in text:
        recency = max(recency, 1.2)
    return base * recency


class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_questions(questions: list[dict], threshold: float = 0.72) -> list[dict]:
    """按题干字符二元组相似度做近重复聚类。"""
    n = len(questions)
    uf = UnionFind(n)
    norms = [normalize_text(q.get("stem", "")) for q in questions]
    grams = [char_bigrams(q.get("stem", "")) for q in questions]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = norms[i], norms[j]
            if not a or not b:
                continue
            # 短题干完全包含关系
            shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
            if len(shorter) >= 10 and shorter in longer and len(shorter) / len(longer) >= 0.8:
                uf.union(i, j)
                continue
            if jaccard(grams[i], grams[j]) >= threshold:
                uf.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    clusters = []
    for members in groups.values():
        qs = [questions[i] for i in members]
        # 代表题：优先信息量最大（题干+答案最长），类型优先级 short>fill>multi>single
        type_rank = {"short": 4, "fill": 3, "multi": 2, "single": 1}
        rep = max(qs, key=lambda q: (len(question_text(q)), type_rank.get(q["type"], 0)))
        weight = sum(question_weight(q) for q in qs)
        topic_counts: dict[str, int] = {}
        for q in qs:
            topic_counts[q.get("topic", "other")] = topic_counts.get(q.get("topic", "other"), 0) + 1
        topic = max(topic_counts, key=topic_counts.get)
        clusters.append(
            {
                "id": rep["id"],
                "representative": rep["id"],
                "members": [q["id"] for q in qs],
                "count": len(qs),
                "weight": round(weight, 3),
                "types": sorted({q["type"] for q in qs}),
                "topic": topic,
                "topic_name": next((q.get("topic_name") for q in qs if q.get("topic") == topic), "其他/未分类"),
                "stem": rep.get("stem", ""),
                "answer_text": rep.get("answer_text", ""),
                "tags": sorted({t for q in qs for t in q.get("tags", [])}),
            }
        )
    clusters.sort(key=lambda c: (-c["weight"], -c["count"], c["stem"]))
    return clusters


def topic_index(taxonomy: dict) -> dict[str, dict]:
    return {t["id"]: t for t in taxonomy.get("topics", [])}


def load_all(bank_path=None, taxonomy_path=None):
    bank = load_json(bank_path or DEFAULT_BANK)
    if not bank:
        raise SystemExit(f"没有找到题库：{bank_path or DEFAULT_BANK}，请先运行 scripts/build_bank.py")
    taxonomy = load_json(taxonomy_path or DEFAULT_TAXONOMY, {"topics": []})
    return bank, taxonomy
