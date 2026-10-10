"""Per-customer memory: chunking, validation, paraphrase recall, isolation by slug, recency and cascade."""
import psycopg
import pytest

from agent_squad import memory as m


def refused(fn, *a, **k):
    with pytest.raises(ValueError):
        fn(*a, **k)


def test_memory(squad_db):
    def sql(q, *p):
        with psycopg.connect(squad_db, autocommit=True) as c:
            r = c.execute(q, p or None)
            return r.fetchall() if r.description else None

    for s in ("cli-a", "cli-b"):
        sql("insert into deals(slug, origin, next_action, next_action_at) values (%s,'test','x','2099-01-01')", s)

    # plain add, no chunking
    r = m.memory_add("cli-a", "preference", "prefere mensagens curtas e odeia áudio")
    assert r["slug"] == "cli-a" and r["chunks"] == 1 and len(r["ids"]) == 1, r

    # chunked add: long sentences, full coverage and overlap
    topics = ("banana violão trator cachoeira martelo girassol xadrez bicicleta lanterna vulcão "
              "tapete foguete biscoito relógio cadeira montanha guitarra pinguim farol maratona "
              "abacaxi telescópio caramelo ventilador zeppelin").split()
    sentences = [f"O cliente comentou sobre {t} durante a conversa de ontem, sem pressa." for t in topics]
    long_text = " ".join(sentences)
    assert 1500 < len(long_text) <= m.MAX_TEXT
    r = m.memory_add("cli-a", "conversation", long_text, meta={"source": "whatsapp"})
    assert r["chunks"] >= 3 and len(r["ids"]) == r["chunks"], r
    rows = sql("select text, meta from memories where id = any(%s) order by id", r["ids"])
    assert all(len(t) <= m.CHUNK for t, _ in rows), [len(t) for t, _ in rows]
    assert all(t.endswith(".") for t, _ in rows), "chunk does not end at a sentence end"
    assert all(s in " ".join(t for t, _ in rows) for s in sentences), "sentence lost in chunking"
    assert rows[0][1]["source"] == "whatsapp" and rows[1][1]["chunk"] == 2, rows[1][1]
    assert rows[0][0][-60:] in rows[1][0] or rows[1][0][:60] in rows[0][0], "no overlap"
    # a sentence at the end of the text is found through the right chunk
    hit = m.memory_search("cli-a", "ele falou de zeppelin?", k=1)[0]
    assert "zeppelin" in hit["text"], hit

    # validation
    refused(m.memory_add, "cli-a", "opinion", "texto")  # invalid kind
    refused(m.memory_add, "does-not-exist", "fact", "texto")  # unknown slug
    refused(m.memory_add, "cli-a", "fact", "")  # empty
    refused(m.memory_add, "cli-a", "fact", "   ")  # blank
    refused(m.memory_add, "cli-a", "fact", "x" * (m.MAX_TEXT + 1))  # too long
    refused(m.memory_search, "cli-a", "áudio", 0)
    refused(m.memory_search, "cli-a", "áudio", 11)
    refused(m.memory_search, "cli-a", "   ")
    refused(m.memory_search, "does-not-exist", "áudio")
    refused(m.memory_recent, "cli-a", 0)
    refused(m.memory_recent, "cli-a", 51)
    refused(m.memory_recent, "does-not-exist")
    m.memory_add("cli-a", "fact", "x" * m.MAX_TEXT)  # the exact limit passes

    # paraphrase: no word in common besides 'áudio'; the vector has to link the concepts
    m.memory_add("cli-a", "fact", "A loja abre de segunda a sábado, das 8h às 18h.")
    m.memory_add("cli-a", "decision", "Pagamento combinado em duas parcelas no boleto.")
    top = m.memory_search("cli-a", "ele gosta de áudio longo?", k=3)
    assert "odeia áudio" in top[0]["text"], top
    assert set(top[0]) == {"id", "kind", "text", "created_at", "score"}, top[0].keys()
    top = m.memory_search("cli-a", "preferência de comunicação por voz", k=3)
    assert "odeia áudio" in top[0]["text"], top

    # isolation: customer B never sees A, not even with A's text copied as the query
    m.memory_add("cli-b", "preference", "adora áudio longo e manda vários por dia")
    for q in ("prefere mensagens curtas e odeia áudio", "ele gosta de áudio longo?", long_text[:200],
              "Pagamento boleto parcelas"):
        res = m.memory_search("cli-b", q, k=10)
        assert res and all(sql("select slug from memories where id=%s", x["id"])[0][0] == "cli-b" for x in res), res
        assert all("odeia" not in x["text"] and "zeppelin" not in x["text"] for x in res), res
    assert all(sql("select slug from memories where id=%s", x["id"])[0][0] == "cli-b" for x in m.memory_recent("cli-b"))
    assert len(m.memory_recent("cli-b")) == 1

    # recent: newest first, honors n
    rec = m.memory_recent("cli-a", n=2)
    assert len(rec) == 2 and rec[0]["id"] > rec[1]["id"] and rec[0]["kind"] == "decision", rec

    # cascade
    assert sql("select count(*) from memories where slug='cli-a'")[0][0] > 0
    sql("delete from deals where slug='cli-a'")
    assert sql("select count(*) from memories where slug='cli-a'")[0][0] == 0
    assert sql("select count(*) from memories where slug='cli-b'")[0][0] == 1
