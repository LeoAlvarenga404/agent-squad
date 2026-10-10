"""`squad` command: the human's controls, process entry points and the guard plugin's internal calls."""
import argparse
import importlib
import json
import os
import sys
import time

# Long-running processes and tools with their own arguments: `squad <name> ...` -> module.main(argv)
ENTRY_POINTS = {
    "mcp": "agent_squad.crm.server",
    "build": "agent_squad.builder.runner",
    "worker": "agent_squad.builder.worker",
    "logs": "agent_squad.builder.logs",
    "bot": "agent_squad.bot",
    "hermes": "agent_squad.hermes",
}


def _print(x) -> None:
    print(json.dumps(x, indent=2, ensure_ascii=False, default=str))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="squad", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pending", help="pending approvals")
    for name in ("approve", "reject"):
        sub.add_parser(name).add_argument("id", type=int)
    m = sub.add_parser("move", help="move a deal to another stage")
    m.add_argument("slug"), m.add_argument("stage"), m.add_argument("next_action"), m.add_argument("next_action_at")
    m.add_argument("lost_reason", nargs="?")
    sub.add_parser("purge", help="delete expired 'target' leads and their folders")
    d = sub.add_parser("directive", help="standing instructions for a role")
    dsub = d.add_subparsers(dest="action", required=True)
    add = dsub.add_parser("add")
    add.add_argument("role"), add.add_argument("text"), add.add_argument("days", type=int, nargs="?", default=7)
    dsub.add_parser("ls")
    dsub.add_parser("rm").add_argument("id", type=int)
    pr = sub.add_parser("priority", help="higher goes first")
    pr.add_argument("target", choices=["lead", "ticket"]), pr.add_argument("ref"), pr.add_argument("n", type=int)
    pa = sub.add_parser("param", help="soft parameters")
    pa.add_argument("key"), pa.add_argument("value")
    h = sub.add_parser("hours", help="add YOUR hours to a ticket")
    h.add_argument("ticket", type=int), h.add_argument("hours", type=float)
    sub.add_parser("dashboard").add_argument("--watch", type=int, metavar="SECONDS")
    sub.add_parser("start", help="queue a ticket for the builder").add_argument("ticket", type=int)
    sub.add_parser("dispatch", help="start whatever fits in the queue now")
    g = sub.add_parser("guard", help="internal: used by the Hermes guard plugin")
    gsub = g.add_subparsers(dest="action", required=True)
    gsub.add_parser("identify").add_argument("phone")
    gsub.add_parser("ticket-slug").add_argument("ticket", type=int)
    ev = gsub.add_parser("event")
    ev.add_argument("action_name"), ev.add_argument("ref"), ev.add_argument("payload", nargs="?", default="{}")
    st = gsub.add_parser("strikes")
    st.add_argument("hash"), st.add_argument("hours", type=int), st.add_argument("mask", nargs="?", default="?")
    return p


def _guard(a) -> None:
    from . import crm, db
    from .crm.approvals import REPEAT_MANIPULATION
    if a.action == "identify":
        try:
            _print(crm.identify_customer(a.phone))
        except ValueError as e:
            _print({"error": str(e)})
            sys.exit(3)
    elif a.action == "ticket-slug":
        with db.connect() as c:
            r = c.execute("select slug from tickets where id=%s", (a.ticket,)).fetchone()
        _print({"slug": r["slug"] if r else None})
    elif a.action == "event":
        json.loads(a.payload)
        with db.connect() as c:
            db.log_event(c, "guard", a.action_name, a.ref, json.loads(a.payload))
    elif a.action == "strikes":
        with db.connect() as c:
            n = c.execute("select count(*) n from events where actor='guard' and action='high_risk_attempt' and ref=%s "
                          "and at > now() - make_interval(hours => %s)", (a.hash, a.hours)).fetchone()["n"]
            if n >= 3 and not c.execute("select 1 from approvals where kind=%s and ref=%s and status='pending'",
                                        (REPEAT_MANIPULATION, a.hash)).fetchone():
                c.execute("insert into approvals(kind, ref, context) values (%s, %s, %s)",
                          (REPEAT_MANIPULATION, a.hash, f"{n} manipulation attempts in {a.hours}h from sender ending "
                                                        f"{a.mask}. See events with actor 'guard'."))
        print(n)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "leads":
        os.environ.setdefault("SQUAD_ROLE", "human")
        from .adapters import load
        return load("leads").main(argv[1:])
    if argv and argv[0] in ENTRY_POINTS:
        if argv[0] != "mcp":
            os.environ.setdefault("SQUAD_ROLE", "runner" if argv[0] in ("build", "worker") else "human")
        return importlib.import_module(ENTRY_POINTS[argv[0]]).main(argv[1:])
    os.environ["SQUAD_ROLE"] = "human"
    from . import crm
    a = _parser().parse_args(argv)
    try:
        if a.cmd == "pending":
            _print(crm.pending())
        elif a.cmd in ("approve", "reject"):
            crm.decide(a.id, a.cmd == "approve")
        elif a.cmd == "move":
            _print(crm.move_stage(a.slug, a.stage, a.next_action, a.next_action_at, a.lost_reason))
        elif a.cmd == "purge":
            print(f"{crm.purge()} leads deleted")
        elif a.cmd == "directive":
            if a.action == "add":
                _print(crm.delegate(a.role, a.text, a.days))
            elif a.action == "ls":
                _print(crm.all_directives())
            else:
                _print(crm.end_directive(a.id))
        elif a.cmd == "priority":
            _print(crm.prioritize(a.target, a.ref, a.n))
        elif a.cmd == "param":
            _print(crm.set_param(a.key, a.value))
        elif a.cmd == "hours":
            print(f"ticket {a.ticket}: {crm.add_hours(a.ticket, a.hours)} h")
        elif a.cmd == "dashboard":
            while True:
                if a.watch:
                    print("\033[2J\033[H", end="")
                _print(crm.dashboard())
                if not a.watch:
                    break
                time.sleep(a.watch)
        elif a.cmd == "start":
            _print(crm.start_ticket(a.ticket))
        elif a.cmd == "dispatch":
            _print(crm.dispatch())
        elif a.cmd == "guard":
            _guard(a)
    except ValueError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
