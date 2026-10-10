"""Shared CRM rules: roles, per-session customer scope, suspicious-text checks and the audit log."""
import functools
import os
import re
from pathlib import Path

from .. import db
from ..config import settings

STAGES = {"target", "contacted", "preview", "negotiation", "won", "delivered", "recurring", "lost"}
CUSTOMER_STAGES = ("won", "delivered", "recurring")
SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")

# Stages each role may move a deal into. 'contacted' and 'delivered' are always human:
# cold outreach and production releases are not the agent's call.
ROLE_STAGES = {
    "hunter": set(),
    "sales": {"preview", "negotiation", "lost", "won"},
    "account": {"recurring", "lost"},
    "builder": set(),
    "coordinator": set(),
    "runner": set(),
    "human": STAGES,
}


def role() -> str:
    r = os.environ.get("SQUAD_ROLE", "")
    if r not in ROLE_STAGES:
        raise ValueError(f"invalid SQUAD_ROLE: {r!r}")
    return r


def owner() -> str:
    return settings().business.owner


def log(c, action: str, ref, payload: dict | None = None) -> None:
    db.log_event(c, role(), action, ref, payload)


def check_scope(slug: str) -> None:
    """With SQUAD_SCOPE_SLUG set (one session = one customer), no tool touches another slug.
    The channel gateway sets it from the sender, never the model."""
    scope = os.environ.get("SQUAD_SCOPE_SLUG")
    if scope and slug != scope:
        raise ValueError("this conversation can only handle its own customer")


def scoped(fn):
    @functools.wraps(fn)
    def wrapper(slug, *a, **k):
        check_scope(slug)
        return fn(slug, *a, **k)
    return wrapper


def check_slug(slug: str) -> None:
    if not SLUG_RE.fullmatch(slug or ""):
        raise ValueError("invalid slug")


def lead_dir(slug: str) -> Path:
    check_slug(slug)
    return settings().workspace / "leads" / slug


def text_values(x) -> list[str]:
    """Every string value (12+ chars) in a JSON-like structure, ignoring keys."""
    if isinstance(x, str):
        return [x] if len(x) >= 12 else []
    if isinstance(x, dict):
        return [t for v in x.values() for t in text_values(v)]
    if isinstance(x, list):
        return [t for v in x for t in text_values(v)]
    return []


def suspicious(text: str | None, min_risk: int = 2) -> list[str] | None:
    """Prompt-manipulation categories found in `text` (risk >= min_risk), or None."""
    from ..guard import detector
    detector.set_owner(owner())
    a = detector.analyze(text or "")
    return a.categories if a.risk >= min_risk else None
