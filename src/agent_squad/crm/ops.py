"""What the human delegates: directives, priorities, soft parameters and the dashboard."""
from .. import db
from .core import log, owner, role

DIRECTIVE_ROLES = ("hunter", "sales", "account", "builder", "all")

# Soft parameter -> validator. Safety caps never go here: they live in code and squad.toml.
PARAMS = {
    "leads_per_round": lambda v: v.isdigit() and 1 <= int(v) <= 10,
    "hunter_paused": lambda v: v in ("yes", "no"),
    "builder_mode": lambda v: v in ("spawn", "worker"),
    "preview_revisions": lambda v: v.isdigit() and 0 <= int(v) <= 5,
}


def active_directives() -> dict:
    """Current instructions from the owner for your role (and for 'all'), plus the current parameters.
    Read at the start of every cycle. The owner's instruction outranks history. Read-only."""
    with db.connect() as c:
        return db.jsonable({
            "owner": owner(),
            "directives": c.execute(
                "select id, role, text, expires_at from directives where active and expires_at > now() "
                "and role in (%s, 'all') order by id", (role(),)).fetchall(),
            "params": {r["key"]: r["value"] for r in c.execute("select key, value from params")},
        })


def all_directives() -> list[dict]:
    """Every active directive, for every role."""
    with db.connect() as c:
        return db.jsonable(c.execute("select id, role, text, expires_at from directives "
                                     "where active and expires_at > now() order by id").fetchall())


def delegate(role: str, text: str, days: int = 7) -> dict:
    """Creates a directive for a role (hunter, sales, account, builder or all), valid for 'days' (1 to 90).
    For the owner's priorities and focus. Cannot approve anything or change safety caps."""
    if role not in DIRECTIVE_ROLES:
        raise ValueError(f"role must be one of {', '.join(DIRECTIVE_ROLES)}")
    if not 1 <= days <= 90:
        raise ValueError("days must be 1 to 90 (every directive expires)")
    with db.connect() as c:
        row = c.execute("insert into directives(role, text, expires_at) "
                        "values (%s, %s, now() + make_interval(days => %s)) returning id", (role, text, days)).fetchone()
        log(c, "delegate", row["id"], {"role": role})
    return {"id": row["id"], "days": days}


def end_directive(id: int) -> dict:
    """Ends a directive before it expires."""
    with db.connect() as c:
        c.execute("update directives set active=false where id=%s", (id,))
        log(c, "end_directive", id)
    return {"id": id, "active": False}


def prioritize(target: str, ref: str, n: int) -> dict:
    """Sets the priority of a lead (target='lead', ref=slug) or a ticket (target='ticket', ref=id). Higher goes first."""
    if target not in ("lead", "ticket"):
        raise ValueError("target must be 'lead' or 'ticket'")
    table, col, val = ("deals", "slug", ref) if target == "lead" else ("tickets", "id", int(ref))
    with db.connect() as c:
        if not c.execute(f"update {table} set priority=%s where {col}=%s returning 1", (n, val)).fetchone():
            raise ValueError(f"{target} not found")
        log(c, "prioritize", ref, {"target": target, "n": n})
    return {"target": target, "ref": ref, "priority": n}


def set_param(key: str, value: str) -> dict:
    """Sets a soft parameter: leads_per_round (1 to 10), hunter_paused (yes/no), builder_mode (spawn/worker)
    or preview_revisions (0 to 5 free revision rounds of the sales preview)."""
    if key not in PARAMS or not PARAMS[key](value):
        raise ValueError(f"invalid parameter/value: {key}={value!r} (accepted: {', '.join(PARAMS)})")
    with db.connect() as c:
        db.put_param(c, key, value)
        log(c, "set_param", key, {"value": value})
    return {"key": key, "value": value}


def dashboard() -> dict:
    """Overview: deals by stage, overdue next actions, tickets by status, pending approvals, hours and machine cost."""
    with db.connect() as c:
        return db.jsonable({
            "deals_by_stage": c.execute("select stage, count(*) n from deals group by stage order by stage").fetchall(),
            "overdue_next_actions": c.execute(
                "select slug, stage, next_action_at from deals where next_action_at < current_date "
                "and stage not in ('lost','recurring') order by next_action_at limit 20").fetchall(),
            "tickets_by_status": c.execute("select status, count(*) n from tickets group by status").fetchall(),
            "pending_approvals": c.execute(
                "select id, kind, ref, context from approvals where status='pending' order by id").fetchall(),
            "recent_tickets": c.execute(
                "select id, slug, type, status, human_hours, machine_minutes, cost_usd from tickets "
                "order by id desc limit 20").fetchall(),
        })
