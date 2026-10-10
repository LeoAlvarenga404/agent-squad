"""Fake WhatsApp: YOU are the lead, on the REAL CRM, with every piece live (Hermes sales/account agents, Telegram
approvals, builder worker). Nothing goes to the lead's real number: this terminal is the channel.

  uv run python -m sim.channel           # resumes the ongoing conversation; with none, reaches out to the newest lead
  uv run python -m sim.channel <slug>

1. If the lead is still 'target', the owner "sends" outreach.md (the lead becomes 'contacted').
2. You reply as the business owner. Before 'won' the sales agent answers; afterwards, the account agent.
3. [backstage] lines show approvals and tickets. A preview you approve (send_preview) arrives here as a message from
   the business, on its own (the channel checks every 15 s). /quit ends (the state stays in the CRM).
"""
import os
import sys
import threading
import time
import zlib
from datetime import date, timedelta

from agent_squad import crm, db, memory
from agent_squad.config import settings
from agent_squad.crm.approvals import _preview_revisions
from agent_squad.crm.core import lead_dir, log

# Customer-facing texts of the pt-BR example deployment (data, like the simulated customer messages).
FIRST_PREVIEW = ("Sua prévia ficou pronta: {url}\nAs fotos e alguns textos são ilustrativos, só para você ver como fica. "
                 "Me manda fotos, logo e o que quiser mudar: são até {n} rodadas de ajuste sem custo antes de publicar.")
NEXT_PREVIEW = "Prévia atualizada com o que você mandou: {url}\nO que acha?"


def pick_slug(argv: list[str]) -> str:
    if argv:
        return argv[0]
    with db.connect() as c:
        ongoing = c.execute("select d.slug from deals d where d.stage not in ('target','lost') and exists "
                            "(select 1 from events e where e.action='channel_outreach' and e.ref=d.slug) "
                            "order by d.updated_at desc limit 1").fetchone()
        if ongoing:
            return ongoing["slug"]
        for r in c.execute("select slug from deals where stage='target' order by created_at desc").fetchall():
            if (lead_dir(r["slug"]) / "outreach.md").exists():
                return r["slug"]
    sys.exit("no lead with outreach.md: run the hunter first")


def business(text: str) -> None:
    print(f"\n{settings().business.name}> {text.strip()}")


def reach_out(slug: str, deal: dict) -> str:
    """The owner's manual step: sends the outreach drafted by the hunter and marks the lead as contacted."""
    phone = deal["phone"] or f"5599{zlib.crc32(slug.encode()) % 10**9:09d}"  # fictitious number: never the real one
    text = (lead_dir(slug) / "outreach.md").read_text(encoding="utf-8")
    with db.connect() as c:
        c.execute("update deals set stage='contacted', phone=%s, next_action='wait for reply', next_action_at=%s, "
                  "updated_at=now() where slug=%s", (phone, date.today() + timedelta(days=3), slug))
        log(c, "channel_outreach", slug)
    memory.memory_add(slug, "conversation",
                      f"Outreach sent by {settings().business.owner} on {date.today()}: {text[:800]}")
    business(text)
    return phone


def updates(slug: str, seen: dict) -> bool:
    """Shows what changed backstage and delivers approved previews (once, recorded in events)."""
    with db.connect() as c:
        approvals = c.execute("select a.id, a.kind, a.status from approvals a left join tickets t on a.ref = t.id::text "
                              "where a.ref = %s or t.slug = %s", (slug, slug)).fetchall()
        tickets = c.execute("select id, status from tickets where slug=%s", (slug,)).fetchall()
        changed = False
        deliver = c.execute(
            "select a.id, t.preview_url, t.type from approvals a join tickets t on a.ref = t.id::text "
            "where a.kind='send_preview' and a.status='approved' and t.slug=%s and not exists "
            "(select 1 from events e where e.action='channel_delivered' and e.ref = a.id::text)", (slug,)).fetchall()
        for x in [*(("approval", a["id"], f"{a['kind']} {a['status']}") for a in approvals),
                  *(("ticket", t["id"], t["status"]) for t in tickets)]:
            if seen.get(x[:2]) != x[2]:
                seen[x[:2]] = x[2]
                print(f"\n  [backstage] {x[0]} #{x[1]}: {x[2]}", flush=True)
                changed = True
        for e in deliver:
            log(c, "channel_delivered", e["id"])
            c.execute("update deals set stage='preview', next_action='hear what they would change', "
                      "next_action_at=%s, updated_at=now() where slug=%s and stage in ('target','contacted')",
                      (date.today() + timedelta(days=2), slug))
            memory.memory_add(slug, "conversation",
                              f"{settings().business.owner} sent the preview to the customer: {e['preview_url']}")
            if e["type"] == "briefing":
                business(FIRST_PREVIEW.format(url=e["preview_url"], n=_preview_revisions(c)))
            else:
                business(NEXT_PREVIEW.format(url=e["preview_url"]))
            changed = True
    return changed


LOCK = threading.Lock()  # the watcher and the conversation never deliver the same preview twice


def watch(slug: str, seen: dict) -> None:
    while True:
        time.sleep(15)
        with LOCK:
            if updates(slug, seen):
                print("\nyou> ", end="", flush=True)


def main() -> None:
    from .run import GUARD_EXTRA, header, sim_profile, talk
    from agent_squad import hermes
    os.environ["SQUAD_ROLE"] = "human"
    slug = pick_slug(sys.argv[1:])
    deal = crm.get_deal(slug)
    print(f"You own {slug} (stage: {deal['stage']}). Dossier: {lead_dir(slug) / 'dossier.json'}")
    phone = reach_out(slug, deal) if deal["stage"] == "target" else deal["phone"]
    if not phone:
        sys.exit("lead has no phone and is past 'target': set deals.phone")
    installed, sessions, seen = set(), {}, {}
    updates(slug, seen)
    threading.Thread(target=watch, args=(slug, seen), daemon=True).start()
    while True:
        try:
            msg = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if msg == "/quit":
            break
        if msg:
            role = "account" if crm.get_deal(slug)["stage"] in crm.CUSTOMER_STAGES else "sales"
            if role not in installed:  # _sim profile on the real CRM, bound to this lead (what the gateway would do)
                hermes.install(role, sim_profile(role), env_extra={"SQUAD_SCOPE_SLUG": slug}, guard_extra=GUARD_EXTRA)
                installed.add(role)
            print(f"  ({role} typing... may take 1 to 3 min)", flush=True)
            reply, sessions[role] = talk(role, header(phone, msg), sessions.get(role), False)
            business(reply)
        with LOCK:
            updates(slug, seen)


if __name__ == "__main__":
    main()
