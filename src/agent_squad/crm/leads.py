"""Hunter tools: campaigns, saving leads from a validated dossier on disk, and retention."""
import json
import shutil

from .. import db
from ..config import settings
from .core import check_slug, lead_dir, log, owner, suspicious, text_values


def create_campaign(source: str, criteria: dict, location: str, lead_cap: int) -> dict:
    """Registers a search round: lead source (e.g. 'cnpj'), its criteria (e.g. {"cnae": ["8630504"]}), location and
    lead cap (1 to the configured max). Limited rounds per day."""
    lim = settings().limits
    if not 1 <= lead_cap <= lim.max_leads_per_round:
        raise ValueError(f"lead_cap must be 1 to {lim.max_leads_per_round}")
    with db.connect() as c:
        if db.get_param(c, "hunter_paused") == "yes":
            raise ValueError(f"hunter paused by {owner()}; check active_directives")
        today = c.execute("select count(*) n from campaigns where created_at >= date_trunc('day', now())").fetchone()["n"]
        if today >= lim.max_rounds_per_day:
            raise ValueError(f"cap of {lim.max_rounds_per_day} rounds per day reached; continue tomorrow")
        row = c.execute("insert into campaigns(source, criteria, location, lead_cap) values (%s,%s,%s,%s) returning *",
                        (source, json.dumps(criteria), location, lead_cap)).fetchone()
        log(c, "create_campaign", row["id"], {"source": source, "location": location})
    return db.jsonable(row)


def list_campaigns() -> list[dict]:
    """Past rounds, with how many leads each produced, advanced and closed."""
    with db.connect() as c:
        return db.jsonable(c.execute("""
            select ca.*, count(d.slug) leads,
                   count(d.slug) filter (where d.stage not in ('target','lost')) advanced,
                   count(d.slug) filter (where d.stage in ('won','delivered','recurring')) won
            from campaigns ca left join deals d on d.campaign_id = ca.id
            group by ca.id order by ca.id desc""").fetchall())


def save_lead(campaign_id: int, slug: str, origin: str, next_action: str, next_action_at: str) -> dict:
    """Saves a lead (stage 'target') from <workspace>/leads/<slug>/dossier.json, which must already be validated.
    Respects the campaign cap. The score comes from the dossier's verdict.score."""
    path = lead_dir(slug) / "dossier.json"
    if not path.exists():
        raise ValueError(f"dossier not found: leads/{slug}/dossier.json (write and validate it first)")
    if path.stat().st_size > 100_000:
        raise ValueError("dossier larger than 100 KB: keep it to the facts the schema asks for")
    dossier = json.loads(path.read_text(encoding="utf-8"))
    cats = suspicious(" ".join(text_values(dossier)))
    if cats:
        raise ValueError(f"the dossier contains text that looks like instructions to an assistant ({', '.join(cats)}): "
                         f"page content may be poisoned. Review leads/{slug}/ and tell {owner()}; not saved.")
    if dossier.get("slug") != slug:
        raise ValueError("slug in the file differs from the slug given")
    score = (dossier.get("verdict") or {}).get("score")
    if not isinstance(score, int) or not 0 <= score <= 10:
        raise ValueError("dossier.verdict.score must be an integer from 0 to 10")
    with db.connect() as c:
        camp = c.execute("select lead_cap from campaigns where id=%s", (campaign_id,)).fetchone()
        if not camp:
            raise ValueError("campaign not found")
        n = c.execute("select count(*) n from deals where campaign_id=%s", (campaign_id,)).fetchone()["n"]
        if n >= camp["lead_cap"]:
            raise ValueError(f"campaign cap reached ({camp['lead_cap']} leads)")
        row = c.execute(
            """insert into deals(slug, campaign_id, origin, dossier, score, next_action, next_action_at, expires_at)
               values (%s,%s,%s,%s,%s,%s,%s, now() + make_interval(days => %s))
               on conflict (slug) do nothing returning slug""",
            (slug, campaign_id, origin, json.dumps(dossier), score, next_action, next_action_at,
             settings().limits.lead_retention_days)).fetchone()
        if not row:
            raise ValueError("slug already exists")
        log(c, "save_lead", slug, {"score": score})
    return {"slug": slug}


def purge() -> int:
    """Deletes expired 'target' leads from the database AND their lead folder (retention covers both)."""
    with db.connect() as c:
        slugs = [r["slug"] for r in c.execute(
            "delete from deals where stage='target' and expires_at < now() returning slug").fetchall()]
        log(c, "purge", "-", {"deleted": len(slugs)})
    for s in slugs:
        try:
            check_slug(s)
        except ValueError:
            continue
        shutil.rmtree(lead_dir(s), ignore_errors=True)
    return len(slugs)
