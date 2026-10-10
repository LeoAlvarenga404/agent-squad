"""Builder runner: executes ONE CRM ticket with Claude Code (claude -p) and reports the result back to the CRM.

  squad build TICKET           run a ticket the CRM marked 'running'
  squad build notify TICKET    resend the end-of-run summary

Principles:
  * Only the ticket id goes into the prompt. The description comes from a customer (untrusted input): Claude reads it
    through the CRM MCP server, marked as DATA.
  * Claude only builds. It runs inside the site folder, with a minimal tool list, a scrubbed environment and, where
    available, the Claude Code sandbox. It cannot deploy, read other tickets or set the preview URL.
  * The runner (code) deploys the preview through the deploy adapter, records it, runs the reviewer, starts fix
    sessions and finally asks the human to approve sending the preview (send_preview). Production never happens here.
  * Stateless: every session is new. The state is the site repository and the ticket.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .. import adapters, crm, db, prompts
from ..config import ROOT, child_env, settings
from ..crm.core import lead_dir, log

HERE = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
DOMAINS = ["registry.npmjs.org", "*.npmjs.org", "registry.yarnpkg.com", "fonts.googleapis.com", "fonts.gstatic.com"]


def parse_result(stdout: str) -> dict:
    """Claude Code's final result. Accepts a single object, a LIST of events (--output-format json) and JSON lines
    (stream-json). For lists and lines the last 'result' event wins."""
    if not stdout.strip():
        return {}
    try:
        j = json.loads(stdout)
    except json.JSONDecodeError:
        j = []
        for line in stdout.splitlines():
            try:
                j.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if isinstance(j, list):
        results = [x for x in j if isinstance(x, dict) and x.get("type") == "result"]
        return results[-1] if results else {}
    return j if isinstance(j, dict) else {}


def session_settings() -> dict:
    """Per-run Claude Code settings: deny rules plus the OS sandbox ([builder] sandbox = auto | on | off).
    The sandbox runs on macOS and Linux/WSL2; on native Windows 'auto' leaves it off (see docs/security.md)."""
    conf = settings().section("builder")
    mode = conf.get("sandbox", "auto")
    out: dict = {"permissions": {"deny": json.loads((HERE / "permissions.json").read_text(encoding="utf-8"))["deny"]}}
    if mode == "on" or (mode == "auto" and sys.platform != "win32"):
        out["sandbox"] = {
            "enabled": True, "failIfUnavailable": mode == "on", "allowUnsandboxedCommands": False,
            "network": {"allowedDomains": conf.get("allowed_domains", DOMAINS), "strictAllowlist": True},
            "filesystem": {"denyRead": ["~/.ssh", "~/.aws", "~/.gnupg", str(ROOT / ".env"), str(ROOT / "squad.toml")]},
        }
    return out


def build_command(prompt: str, claude_cmd: list[str], mcp_json: Path, slug: str) -> list[str]:
    perm = json.loads((HERE / "permissions.json").read_text(encoding="utf-8"))
    cmd = [*claude_cmd, "-p", prompt,
           "--output-format", "stream-json", "--verbose",  # live events for `squad logs`
           "--mcp-config", str(mcp_json), "--strict-mcp-config",
           "--settings", json.dumps(session_settings()),
           "--add-dir", str(lead_dir(slug)),
           "--permission-mode", "acceptEdits",
           "--no-session-persistence",
           "--allowedTools", *perm["allow"], "mcp__crm",
           "--disallowedTools", *perm["deny"]]
    if settings().limits.builder_max_usd:
        cmd += ["--max-budget-usd", str(settings().limits.builder_max_usd)]
    if os.environ.get("BUILDER_MODEL"):
        cmd += ["--model", os.environ["BUILDER_MODEL"]]
    return cmd


def write_mcp_config(dest: Path, ticket_id: int) -> Path:
    env = {"SQUAD_ROLE": "builder", "SQUAD_TICKET_ID": str(ticket_id), "SQUAD_AGENT_DSN": db.dsn(),
           "SQUAD_HOME": str(ROOT), "SQUAD_WORKSPACE": str(settings().workspace)}
    if os.environ.get("PEXELS_API_KEY"):
        env["PEXELS_API_KEY"] = os.environ["PEXELS_API_KEY"]  # used by the MCP server, never by the model's shell
    dest.write_text(json.dumps({"mcpServers": {"crm": {
        "command": os.environ.get("UV_BIN") or "uv", "args": ["run", "--project", str(ROOT), "squad", "mcp"],
        "env": env}}}), encoding="utf-8")
    return dest


def render(name: str, ticket_id: int, slug: str, **extra) -> str:
    skill = settings().section("builder").get("site_skill", "")
    new_site = (f"use the `{skill}` skill with the slug (the dossier already exists)." if skill
                else "create it from the dossier.")
    return prompts.read(HERE / "prompts" / name, ticket=str(ticket_id), new_site=new_site,
                        dossier=str(lead_dir(slug) / "dossier.json"), **extra)


def summary(ticket_id: int) -> str:
    """End-of-run text: site ready with the links, or failure with the reason."""
    with db.connect() as c:
        t = c.execute("select t.*, coalesce(d.dossier->'identity'->>'name', t.slug) as name from tickets t "
                      "join deals d using (slug) where t.id=%s", (ticket_id,)).fetchone()
    if t["status"] == "preview" and t["preview_url"]:
        lines = [f"Site ready: {t['name']} (ticket {t['id']}, {t['type']})", f"Preview: {t['preview_url']}"]
        if t["public_url"] and t["public_url"] != t["preview_url"]:
            lines.append(f"Project link: {t['public_url']}")
        lines.append(f"Builder: {t['machine_minutes']} min, US$ {t['cost_usd']} (API-equivalent).")
        if t["run_note"]:
            lines.append(f"What was done (builder's note): {t['run_note']}")
        return "\n".join(lines)
    return "\n".join([f"The builder did not finish ticket {t['id']} ({t['name']}, {t['type']}). Status: {t['status']}, "
                      f"attempt {t['attempts']} of {settings().limits.builder_max_attempts}.",
                      f"Reason: {t['run_note'] or 'no detail'}"])


def notify(ticket_id: int, send=None) -> str:
    """Sends the summary through the notify adapter (failures, and `squad build notify N`). Returns the text."""
    text = summary(ticket_id)
    if (send or adapters.load("notify").send)(text):
        with db.connect() as c:
            c.execute("update tickets set preview_notified=true where id=%s and status='preview'", (ticket_id,))
    return text


def review(ticket_id: int, round: int) -> dict | None:
    with db.connect() as c:
        t = c.execute("select id, slug, preview_url from tickets where id=%s", (ticket_id,)).fetchone()
    if not t["preview_url"]:
        return None
    return adapters.load("review").review(t, round, LOGS / f"ticket-{ticket_id}-review-{round}")


def run_claude(prompt: str, claude_cmd: list[str], log_file: Path, timeout_min: float, ticket_id: int,
               slug: str) -> tuple[str | None, list, float]:
    """One NEW Claude Code session inside the site folder. Returns (failure reason or None, permission denials, cost)."""
    site = settings().workspace / "sites" / slug
    site.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        cmd = build_command(prompt, claude_cmd, write_mcp_config(Path(tmp) / "mcp.json", ticket_id), slug)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(log_file, "wb") as f:  # one event per line, live
                r = subprocess.run(cmd, cwd=site, stdout=f, stderr=subprocess.PIPE, timeout=timeout_min * 60,
                                   env=child_env("CLAUDE_CONFIG_DIR"))
        except subprocess.TimeoutExpired:
            return f"timed out after {timeout_min} min", [], 0.0
    out = parse_result(log_file.read_text(encoding="utf-8", errors="replace"))
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    denials = [d.get("tool_name", "?") for d in out.get("permission_denials", [])]
    reason = None
    if r.returncode != 0 or out.get("is_error"):
        reason = f"claude exited with an error ({r.returncode}): {(out.get('result') or err or '')[:300]}"
    else:
        with db.connect() as c:
            note = c.execute("select run_note from tickets where id=%s", (ticket_id,)).fetchone()["run_note"] or ""
        if note.lower().startswith("failed"):
            reason = note[:300]
    return reason, denials, float(out.get("total_cost_usd") or 0)


def deploy(ticket_id: int, slug: str, deploy_fn=None) -> str | None:
    """Deploys the preview (code, not the model) and records it. Returns the failure reason or None."""
    try:
        url = (deploy_fn or adapters.load("deploy").deploy)(settings().workspace / "sites" / slug, slug)
    except Exception as e:
        return f"deploy failed: {e}"[:300]
    crm.update_ticket(ticket_id, status="preview", preview_url=url)
    return None


def execute(ticket_id: int, claude_cmd: list[str] | None = None, timeout_min: float | None = None,
            send=None, public_url_fn=None, reviewer=None, deploy_fn=None) -> dict:
    """Builds, deploys the preview, lets the reviewer look (up to review_rounds, with a HIGH/MEDIUM fix and a redeploy
    between rounds), then asks the human for the OK to show the customer. On failure: reopens the ticket (cancels
    after builder_max_attempts) and notifies."""
    lim = settings().limits
    claude_cmd = claude_cmd or [os.environ.get("CLAUDE_BIN", "claude")]
    timeout_min = timeout_min or lim.builder_timeout_min
    public_url_fn = public_url_fn or (lambda slug: adapters.load("deploy").public_url(settings().workspace / "sites" / slug))
    reviewer = reviewer or review
    with db.connect() as c:
        t = c.execute("select id, slug, status from tickets where id=%s", (ticket_id,)).fetchone()
        if not t or t["status"] != "running":
            raise SystemExit(f"ticket {ticket_id} is not running (queue it with start_ticket)")
        c.execute("update tickets set run_note=null where id=%s", (ticket_id,))
    slug, start = t["slug"], time.time()
    reason, denials, cost = run_claude(render("build.md", ticket_id, slug), claude_cmd,
                                       LOGS / f"ticket-{ticket_id}.jsonl", timeout_min, ticket_id, slug)
    reason = reason or deploy(ticket_id, slug, deploy_fn)
    ok, verdict = reason is None, None
    if ok:
        for rnd in range(1, lim.review_rounds + 1):
            verdict = reviewer(ticket_id, rnd)
            if not verdict or verdict["verdict"] == "APPROVED" or rnd == lim.review_rounds:
                break
            print(f"\nreviewer asked for changes (round {rnd}):\n{verdict['text']}\n", flush=True)
            r, d, usd = run_claude(render("fix.md", ticket_id, slug, review_file=str(verdict["file"])), claude_cmd,
                                   LOGS / f"ticket-{ticket_id}-fix-{rnd}.jsonl", timeout_min, ticket_id, slug)
            denials, cost = denials + d, cost + usd
            r = r or deploy(ticket_id, slug, deploy_fn)
            if r:  # the fix failed: the previous preview stays
                print(f"fix failed: {r}", flush=True)
                break
    minutes = round((time.time() - start) / 60, 1)
    with db.connect() as c:
        note = None if ok else f"failed: {reason.removeprefix('failed: ')}"
        if denials:
            note = (note or "") + f" [permission denials: {', '.join(sorted(set(denials)))}]"
        if ok:
            row = c.execute("select type from tickets where id=%s", (ticket_id,)).fetchone()
            public = public_url_fn(slug) if row["type"] == "briefing" else None  # only the first version counts
            c.execute("update tickets set machine_minutes = machine_minutes + %s, cost_usd = cost_usd + %s, "
                      "public_url = coalesce(%s, public_url), preview_notified = true where id=%s",
                      (minutes, cost, public, ticket_id))
        else:
            attempts = c.execute("select attempts from tickets where id=%s", (ticket_id,)).fetchone()["attempts"]
            status = "cancelled" if attempts >= lim.builder_max_attempts else "open"
            c.execute("update tickets set status=%s::ticket_status, machine_minutes = machine_minutes + %s, "
                      "cost_usd = cost_usd + %s, run_note = %s, updated_at=now() where id=%s",
                      (status, minutes, cost, note, ticket_id))
        log(c, "builder_ok" if ok else "builder_failed", ticket_id,
            {"min": minutes, "usd": cost, "reason": reason, "review": verdict and verdict["verdict"]})
    if ok:
        rev = ("Review: did not run (no reviewer or no preview)." if not verdict else f"Review: {verdict['verdict']}"
               + ("" if verdict["verdict"] == "APPROVED" else f" (what is left)\n{verdict['text'][:900]}"))
        if denials:
            rev += f"\nPermission denials: {', '.join(sorted(set(denials)))}"
        crm.request_send_preview(ticket_id, f"{summary(ticket_id)}\n{rev}\nApprove = the customer gets the link.")
    else:
        notify(ticket_id, send)
    crm.dispatch()  # spawn mode: start the next one in the queue
    return {"ok": ok, "minutes": minutes, "cost_usd": cost, "reason": reason, "denials": denials,
            "review": verdict and verdict["verdict"]}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    os.environ["SQUAD_ROLE"] = "runner"
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) == 2 and argv[0] == "notify" and argv[1].isdigit():
        print(notify(int(argv[1])))
    elif len(argv) == 1 and argv[0].isdigit():
        print(json.dumps(execute(int(argv[0])), ensure_ascii=False))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
