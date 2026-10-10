"""Per-customer memory (RAG) for the sales and account agents.

Local embeddings (fastembed, ONNX, CPU). Hybrid search: vector (cosine) + full text, fused by RRF.
The slug filter is in BOTH branches: that is the isolation between customers.
Each function's docstring is the description the LLM sees.
"""
import json
import os
from functools import lru_cache
from pathlib import Path

import psycopg

from . import db
from .config import ROOT

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 384 dims: must match migrations
KINDS = ("fact", "preference", "decision", "conversation")
MAX_TEXT = 4000
CHUNK = 700
OVERLAP = 100
RRF_K = 60
CACHE = Path(os.environ.get("FASTEMBED_CACHE_PATH") or ROOT / ".cache" / "fastembed")


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding
    CACHE.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(MODEL, cache_dir=str(CACHE))


def _vectors(texts: list[str]) -> list[str]:
    """Embeddings as pgvector literals ('[0.1,0.2,...]')."""
    return ["[" + ",".join(f"{x:.6f}" for x in v) + "]" for v in _model().embed(texts)]


def chunks(text: str) -> list[str]:
    """~700-char pieces with ~100 overlap, preferring to cut at a sentence end."""
    if len(text) <= CHUNK:
        return [text]
    out, start = [], 0
    while start < len(text):
        end = min(start + CHUNK, len(text))
        if end < len(text):
            cut = max(text.rfind(ch, start + CHUNK // 2, end) for ch in (".", "!", "?", "\n"))
            if cut != -1:
                end = cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - OVERLAP, start + 1)
    return [p for p in out if p]


def _require_slug(c, slug: str) -> None:
    if not c.execute("select 1 from deals where slug=%s", (slug,)).fetchone():
        raise ValueError("deal not found")


def memory_add(slug: str, kind: str, text: str, meta: dict | None = None) -> dict:
    """Stores a memory about a customer (by slug) to recall later without rereading everything.
    kind: 'fact' (objective data), 'preference' (how to treat them: tone, channel, time, pace, objections),
    'decision' (what was agreed) or 'conversation' (a relevant excerpt).
    text: 1 to 4000 characters; long texts are split into ~700-character chunks.
    Returns {slug, chunks, ids}."""
    if kind not in KINDS:
        raise ValueError(f"invalid kind: {kind!r} (use {', '.join(KINDS)})")
    text = (text or "").strip()
    if not 1 <= len(text) <= MAX_TEXT:
        raise ValueError(f"text must be 1 to {MAX_TEXT} characters")
    meta = meta or {}
    if len(json.dumps(meta, default=str)) > 2000:
        raise ValueError("meta larger than 2000 characters")
    with db.connect() as c:
        _require_slug(c, slug)
        parts = chunks(text)
        ids = []
        with c.transaction():
            for i, (p, v) in enumerate(zip(parts, _vectors(parts))):
                m = dict(meta, **({"chunk": i + 1, "of": len(parts)} if len(parts) > 1 else {}))
                ids.append(c.execute(
                    "insert into memories(slug, kind, text, embedding, meta) values (%s,%s,%s,%s::vector,%s) returning id",
                    (slug, kind, p, v, json.dumps(m, default=str))).fetchone()["id"])
    return {"slug": slug, "chunks": len(ids), "ids": ids}


_SQL = """
with v as (
  select id, row_number() over (order by embedding <=> %(q)s::vector) r
  from memories where slug = %(slug)s and %(use_v)s
  order by embedding <=> %(q)s::vector limit 20),
t as (
  select id, row_number() over (order by ts_rank_cd(tsv, tq) desc, id) r
  from memories, (select replace(plainto_tsquery('squad', %(txt)s)::text, '&', '|')::tsquery tq) x
  where slug = %(slug)s and %(use_t)s and tsv @@ tq
  order by ts_rank_cd(tsv, tq) desc limit 20)
select m.id, m.kind, m.text, m.created_at,
       coalesce(1.0/(%(k)s + v.r), 0) + coalesce(1.0/(%(k)s + t.r), 0) as score
from v full join t using (id) join memories m using (id)
where m.slug = %(slug)s
order by score desc, m.id limit %(lim)s
"""


def search(slug: str, query: str, k: int, mode: str = "hybrid") -> list[dict]:
    """mode: 'hybrid', 'vector' or 'text' (the eval compares them)."""
    query = (query or "").strip()
    if not query:
        raise ValueError("empty query")
    q = _vectors([query])[0]
    with db.connect() as c:
        _require_slug(c, slug)
        with c.transaction():
            try:  # pgvector >= 0.8: without it HNSW may return fewer rows than the slug filter asks for
                with c.transaction():
                    c.execute("set local hnsw.iterative_scan = relaxed_order")
            except psycopg.Error:
                pass
            rows = c.execute(_SQL, dict(q=q, slug=slug, txt=query, k=RRF_K, lim=k,
                                        use_v=mode in ("hybrid", "vector"),
                                        use_t=mode in ("hybrid", "text"))).fetchall()
    for r in rows:
        r["created_at"] = r["created_at"].isoformat()
        r["score"] = float(r["score"])
    return rows


def memory_search(slug: str, query: str, k: int = 5) -> list[dict]:
    """Searches ONE customer's memory (slug) for what answers the query, in natural language
    (e.g. 'how do they prefer to be contacted?', 'price objection'). Combines meaning (vector) and words.
    Only returns that customer's memories. k between 1 and 10.
    Returns a list of {id, kind, text, created_at, score}, best first."""
    if not 1 <= k <= 10:
        raise ValueError("k must be between 1 and 10")
    return search(slug, query, k)


def memory_recent(slug: str, n: int = 20) -> list[dict]:
    """A customer's latest memories (newest first), to summarize into a behavior profile card (tone, channel, time,
    pace, objections) saved with update_customer. n between 1 and 50.
    Returns a list of {id, kind, text, created_at}."""
    if not 1 <= n <= 50:
        raise ValueError("n must be between 1 and 50")
    with db.connect() as c:
        _require_slug(c, slug)
        rows = c.execute("select id, kind, text, created_at from memories where slug=%s order by id desc limit %s",
                         (slug, n)).fetchall()
    for r in rows:
        r["created_at"] = r["created_at"].isoformat()
    return rows
