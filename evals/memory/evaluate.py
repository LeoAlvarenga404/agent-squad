"""Evaluates per-customer memory: hit@1, hit@3 (keyword vs vector vs hybrid) and isolation between customers.

  uv run python -m evals.memory.evaluate   (creates and drops a throwaway database; writes evals/memory/RESULTS.md)
"""
import datetime
import os
import sys
from pathlib import Path

import psycopg

from agent_squad import memory as m
from sim.run import throwaway_db

from . import data

HERE = Path(__file__).resolve().parent
MODES = ("text", "vector", "hybrid")
LABEL = {"text": "keyword only (tsvector)", "vector": "vector only (cosine)", "hybrid": "hybrid (RRF)"}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with throwaway_db() as dsn:
        os.environ["SQUAD_DSN"] = dsn
        with psycopg.connect(dsn, autocommit=True) as c:
            for slug in data.CUSTOMERS:
                c.execute("insert into deals(slug, origin, next_action, next_action_at) "
                          "values (%s, 'synthetic', 'evaluate', '2099-01-01')", (slug,))
        id_of, key_of = {}, {}
        for key, (slug, kind, text) in data.ITEMS.items():
            r = m.memory_add(slug, kind, text)
            assert r["chunks"] == 1, key
            id_of[key] = r["ids"][0]
            key_of[r["ids"][0]] = key

        hits = {md: {1: 0, 3: 0} for md in MODES}
        misses = {md: [] for md in MODES}
        for slug, question, expected in data.QUESTIONS:
            for md in MODES:
                ids = [r["id"] for r in m.search(slug, question, 3, md)]
                pos = ids.index(id_of[expected]) if id_of[expected] in ids else None
                for k in (1, 3):
                    hits[md][k] += pos is not None and pos < k
                if pos != 0:
                    misses[md].append(f"{question} (expected {expected}, got {[key_of[i] for i in ids]})")
        n = len(data.QUESTIONS)

        # Isolation: every text and question of one customer, copied verbatim, queried on ANOTHER customer's slug
        leaks = queries = 0
        for slug in data.CUSTOMERS:
            texts = [t for s, _, t in data.ITEMS.values() if s == slug] + [q for s, q, _ in data.QUESTIONS if s == slug]
            for other in data.CUSTOMERS:
                if other == slug:
                    continue
                for q in texts:
                    for md in MODES:
                        queries += 1
                        leaks += sum(data.ITEMS[key_of[r["id"]]][0] != other for r in m.search(other, q, 10, md))

    rows = "\n".join(f"| {LABEL[md]} | {hits[md][1]}/{n} ({hits[md][1] / n:.0%}) | {hits[md][3]}/{n} ({hits[md][3] / n:.0%}) |"
                     for md in MODES)
    miss_md = "\n".join(f"### {LABEL[md]}\n" + ("\n".join(f"- {f}" for f in misses[md]) or "- none") + "\n" for md in MODES)
    out = f"""# Per-customer memory (RAG) evaluation

- Date: {datetime.date.today().isoformat()}
- Embedding model: `{m.MODEL}` (fastembed, local, CPU)
- Set: {len(data.CUSTOMERS)} fictitious customers, {len(data.ITEMS)} memory items, {n} questions (1 expected item each)
- Search: k=3 per question, always filtered by the customer's slug

## Accuracy

| Method | hit@1 | hit@3 |
|---|---|---|
{rows}

hit@k: the expected item is among the first k results.

## Isolation between customers

{queries} queries (every text and question of one customer, copied verbatim, run on each other customer's slug, in the 3 modes, k=10).
Items from another customer returned: **{leaks}** (requirement: 0).

## Honest caveat

The data is synthetic, written by us, and small ({len(data.ITEMS)} items, {n} questions): differences of a few points
between methods are not statistically significant. The number checks that search works and isolation holds; it does
not promise a hit rate in production. Real memories will be longer, repetitive and noisy. Re-evaluate with real
(anonymized) conversations when there are some.

## Where the expected item was not first

{miss_md}"""
    (HERE / "RESULTS.md").write_text(out, encoding="utf-8")
    print(out)
    assert leaks == 0, "LEAK BETWEEN CUSTOMERS"


if __name__ == "__main__":
    main()
