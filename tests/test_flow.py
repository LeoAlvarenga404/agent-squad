"""Does the flow run on its own? From the contacted lead to the preview delivered on the channel, through the same code
as the bot (crm.decide), the worker (claim_next_ticket), the runner (request_send_preview) and the channel, including
a revision round. No LLM and no Hermes."""
import contextlib
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the repo root, for the sim package
from agent_squad import crm, db  # noqa: E402
from sim import channel  # noqa: E402

pytestmark = pytest.mark.usefixtures("squad_db")
SLUG = "flow-optician"


def test_lead_to_delivered_preview(as_role, workspace):
    (workspace / "leads" / SLUG).mkdir(parents=True)
    (workspace / "leads" / SLUG / "outreach.md").write_text("Oi! Posso montar uma prévia do site da ótica?",
                                                             encoding="utf-8")

    def pull():
        """What the worker does every 10 s."""
        as_role("runner")
        return crm.claim_next_ticket()

    def deliver(out, seen):
        with contextlib.redirect_stdout(out):
            channel.updates(SLUG, seen)  # the channel watcher, every 15 s

    def pending_send_preview():
        with db.connect() as c:
            return c.execute("select id from approvals where kind='send_preview' and status='pending'").fetchone()["id"]

    with db.connect() as c:
        c.execute("insert into deals(slug, origin, stage, next_action, next_action_at) "
                  "values (%s, 'cnpj', 'target', 'reach out', '2099-01-01')", (SLUG,))
        db.put_param(c, "builder_mode", "worker")
    as_role("human")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        channel.reach_out(SLUG, crm.get_deal(SLUG))  # 1. the owner sends the outreach
    d = crm.get_deal(SLUG)
    assert d["stage"] == "contacted" and d["phone"].startswith("5599"), d
    assert channel.pick_slug([]) == SLUG, "on its own the channel must resume the ongoing conversation"
    assert pull() == {"queue": "empty"}, "empty queue: the worker waits"

    as_role("sales")  # 2. the lead is interested
    ap = crm.request_approval("preview", SLUG, "ótica, seg-sab 9-18, óculos de grau e de sol")["id"]
    as_role("human")
    crm.decide(ap, True)  # 3. OK on Telegram
    t = pull()  # 4. the worker pulls it on its own
    assert t["slug"] == SLUG and t["type"] == "briefing", t
    assert pull() == {"queue": "empty"}, "one site at a time per customer"
    as_role("runner")
    crm.update_ticket(t["id"], status="preview", preview_url="https://flow.vercel.app")

    seen, out = {}, io.StringIO()
    deliver(out, seen)
    assert "Sua prévia" not in out.getvalue(), "without review and the owner's OK the preview does not go out"
    as_role("runner")
    crm.request_send_preview(t["id"], "Review: APPROVED")  # 5. the runner, after the review
    ep = pending_send_preview()
    deliver(out, seen)
    assert "Sua prévia" not in out.getvalue(), "without the owner's OK the preview does not go out"
    as_role("human")
    crm.decide(ep, True)  # 6. OK on send_preview
    deliver(out, seen)
    deliver(out, seen)
    assert out.getvalue().count("https://flow.vercel.app") == 1, "delivered only once"
    assert "ilustrativ" in out.getvalue(), "the delivery says what is illustrative and how many revision rounds"
    assert crm.get_deal(SLUG)["stage"] == "preview"

    as_role("sales")  # 7. the lead sends photos: a revision round
    rev = crm.request_approval("preview", SLUG, "mandou 3 fotos da loja e o logo")["id"]
    as_role("human")
    crm.decide(rev, True)
    t2 = pull()
    assert t2["type"] == "change" and t2["slug"] == SLUG, t2
    as_role("runner")
    crm.update_ticket(t2["id"], status="preview", preview_url="https://flow-v2.vercel.app")
    crm.request_send_preview(t2["id"])
    ep2 = pending_send_preview()
    as_role("human")
    crm.decide(ep2, True)
    deliver(out, seen)
    assert out.getvalue().count("https://flow-v2.vercel.app") == 1 and pull() == {"queue": "empty"}
