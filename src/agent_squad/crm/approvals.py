"""Human approvals. Agents ask; only the human decides (CLI or the Telegram bot)."""
import psycopg

from .. import db
from .core import CUSTOMER_STAGES, check_scope, log, owner, suspicious
from .tickets import dispatch

CLOSE_DEAL = "close_deal"
PREVIEW = "preview"
SEND_PREVIEW = "send_preview"
REPEAT_MANIPULATION = "repeat_manipulation"
MAX_CONTEXT = 2000
# What agents may ask for. send_preview and repeat_manipulation are raised by code only.
AGENT_KINDS = {CLOSE_DEAL, PREVIEW, "off_range_price", "suspicious_ticket", "manipulation", "new_contact", "quote",
               "go_live", "builder_failed", "warranty_question", "out_of_scope_contact"}
DEAL_KINDS = {CLOSE_DEAL, PREVIEW, "off_range_price", "quote", "go_live", "builder_failed", "warranty_question"}


def _preview_revisions(c) -> int:
    return int(db.get_param(c, "preview_revisions", "2"))


def request_approval(kind: str, ref: str, context: str) -> dict:
    """Asks for human approval (e.g. kind='close_deal', ref=slug). The person decides, never the agent.
    kind='preview' (ref=slug): the first one asks for the free preview; later ones, before closing, ask for a
    revision round with what the lead sent (photos, copy, colors), up to 'preview_revisions'. The context says what
    the lead wants on the site. Allowed kinds: close_deal, preview, off_range_price, suspicious_ticket, manipulation,
    new_contact, quote, go_live, builder_failed, warranty_question, out_of_scope_contact."""
    if kind not in AGENT_KINDS:
        raise ValueError(f"unknown approval kind: {kind!r}")
    if not 1 <= len(context or "") <= MAX_CONTEXT or not 1 <= len(ref or "") <= 100:
        raise ValueError(f"context must be 1 to {MAX_CONTEXT} characters and ref 1 to 100")
    if kind != "manipulation" and suspicious(context):
        raise ValueError("the context has text that looks like instructions to an assistant; describe only the facts")
    with db.connect() as c:
        if kind in DEAL_KINDS:
            check_scope(ref)
            if not c.execute("select 1 from deals where slug=%s", (ref,)).fetchone():
                raise ValueError("deal not found")
        if kind == PREVIEW:
            if c.execute("select 1 from approvals where kind=%s and ref=%s and status='pending'", (PREVIEW, ref)).fetchone():
                raise ValueError(f"a preview request for this lead is already pending: wait for {owner()} to decide")
            done = c.execute("select count(*) n from approvals where kind=%s and ref=%s and status='approved'",
                             (PREVIEW, ref)).fetchone()["n"]
            if done > _preview_revisions(c):
                raise ValueError("the preview used all free revision rounds: more changes only after closing")
        try:
            row = c.execute("insert into approvals(kind, ref, context) values (%s,%s,%s) returning id",
                            (kind, ref, context)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise ValueError(f"an identical request is already pending: wait for {owner()} to decide") from None
        log(c, "request_approval", ref, {"kind": kind, "id": row["id"]})
    return db.jsonable(row)


def request_send_preview(ticket_id: int, summary: str = "") -> None:
    """The runner, when a reviewed ticket is done: the human checks the preview before the customer sees it."""
    with db.connect() as c:
        t = c.execute("select slug, preview_url from tickets where id=%s", (ticket_id,)).fetchone()
        context = f"{t['slug']}: {t['preview_url']}" + (f"\n{summary}" if summary else "")
        c.execute("insert into approvals(kind, ref, context) select %s, %s, %s where not exists "
                  "(select 1 from approvals where kind=%s and ref=%s)",
                  (SEND_PREVIEW, str(ticket_id), context, SEND_PREVIEW, str(ticket_id)))


def pending() -> list[dict]:
    with db.connect() as c:
        return db.jsonable(c.execute("select * from approvals where status='pending' order by id").fetchall())


def decide(id: int, approve: bool) -> None:
    """Records the human decision. An approved 'preview' becomes a queued ticket right away (the lead is not a
    customer yet): 'briefing' the first time, 'change' for later revision rounds."""
    with db.connect() as c:
        a = c.execute("update approvals set status=%s::approval_status, decided_at=now() "
                      "where id=%s and status='pending' returning kind, ref, context",
                      ("approved" if approve else "rejected", id)).fetchone()
        log(c, "approve" if approve else "reject", id)
        preview = bool(a and approve and a["kind"] == PREVIEW)
        if preview:
            d = c.execute("select stage from deals where slug=%s", (a["ref"],)).fetchone()
            preview = bool(d and d["stage"] not in CUSTOMER_STAGES)  # customers change their site through tickets
        if preview:
            kind = "change" if c.execute("select 1 from tickets where slug=%s and type='briefing'",
                                         (a["ref"],)).fetchone() else "briefing"
            t = c.execute("insert into tickets(slug, type, description, status) values (%s, %s::ticket_type, %s, 'queued') "
                          "returning id", (a["ref"], kind, a["context"])).fetchone()
            log(c, "create_ticket", a["ref"], {"id": t["id"], "type": kind, "approval": id})
    if preview:
        dispatch()
