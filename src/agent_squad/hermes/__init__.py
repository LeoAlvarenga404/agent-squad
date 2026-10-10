"""Installs the squad's Hermes profiles from agents/ and switches their model.

  squad hermes install                      # hunter, sales and account
  squad hermes install sales                # just one
  squad hermes switch-model anthropic sales account
  squad hermes switch-model anthropic --model claude-sonnet-5-5 sales

The sources (SOUL, skills, catalog) live in this repository; the Hermes profile is DERIVED from them. Re-running
overwrites what the installer owns and keeps the rest. Customer-facing profiles keep only the chat model's keys in
their .env; channel tokens are always removed and channels stay disabled until you turn them on
(docs/production-checklist.md). Slash commands are restricted to [hermes] admin_ids.

Security per profile:
  * hunter: terminal, files and web (it runs the lead search and writes dossiers), no messaging channel.
  * sales and account (talk to customers): ONLY the CRM MCP and `clarify`. No terminal, files, web, browser, skills,
    native Hermes memory or past-session search: customer memory lives in the CRM (per slug), and an agent exposed
    to customer text must not be able to rewrite its own instructions.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .. import prompts
from ..config import ROOT, settings

PKG = Path(__file__).resolve().parents[1]
ROLES = ("hunter", "sales", "account")
CUSTOMER_FACING = {"sales", "account"}
# Toolsets disabled on customer-facing profiles (what is left: the crm MCP and clarify).
CUSTOMER_DISABLED = ["web", "browser", "terminal", "file", "code_execution", "vision", "image_gen", "tts", "skills",
                     "todo", "memory", "session_search", "delegation", "cronjob", "computer_use", "homeassistant",
                     "kanban", "x_search", "video", "video_gen", "discord", "discord_admin", "spotify", "feishu_doc",
                     "feishu_drive", "yuanbao", "project"]
# Keys kept in a customer-facing profile's .env: the chat model's credentials only. Anything else (tool API keys,
# channel tokens) stays out, because Hermes enables some toolsets just because their key exists.
MODEL_KEY_RE = r"^(DEEPSEEK|ANTHROPIC|OPENAI|OPENROUTER|GEMINI|GOOGLE|MISTRAL|GROQ|XAI|TOGETHER|FIREWORKS)_[A-Z_]*="
DESCRIPTIONS = {
    "hunter": "{business} hunter: picks a niche and city, generates lead candidates, checks their online presence and "
              "records them in the CRM. Never contacts anyone.",
    "sales": "{business} sales: answers local business owners on WhatsApp, presents the preview, negotiates within the "
             "catalog and takes the deal to closing (approved by {owner}).",
    "account": "{business} account manager: briefing, approval, change requests and triage (bug, plan or quote) for "
               "customers who already closed. Drives the builder through tickets.",
}
PROVIDERS = {
    "anthropic": {"provider": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-haiku-5-5",
                  "api_key_env": "ANTHROPIC_API_KEY"},
    "deepseek": {"provider": "deepseek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-v4-flash"},
}
SAFE_REPLY = "Sorry, I can't help with that. Is there anything else about your website I can help you with?"


def _conf() -> dict:
    return settings().section("hermes")


def home() -> Path:
    if h := _conf().get("home") or os.environ.get("HERMES_HOME"):
        return Path(h)
    return Path(os.environ["LOCALAPPDATA"]) / "hermes" if os.name == "nt" else Path.home() / ".hermes"


def _uv() -> str:
    return _conf().get("uv") or shutil.which("uv") or "uv"


def hermes(*args: str) -> str:
    binary = _conf().get("bin") or shutil.which("hermes") or "hermes"
    r = subprocess.run([binary, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"hermes {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout


def _yaml():
    try:
        import yaml
    except ImportError:
        sys.exit("pyyaml is missing: uv sync --extra hermes")
    return yaml


def _read_env(p: Path) -> dict[str, str]:
    if not p.exists():
        return {}
    return dict(l.split("=", 1) for l in p.read_text(encoding="utf-8").splitlines() if re.match(r"^[A-Z_0-9]+=", l))


def section(text: str, title: str) -> str:
    """A '## title' section up to the next '## '."""
    m = re.search(rf"^## {re.escape(title)}.*?\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(0) if m else ""


def install(role: str, profile: str | None = None, env_extra: dict[str, str] | None = None,
            guard_extra: dict | None = None) -> None:
    """role: hunter, sales or account. profile: Hermes profile name (default = role; tests use <role>_sim).
    env_extra: extra variables for the CRM MCP (e.g. SQUAD_DSN and SQUAD_DRY_RUN in tests).
    guard_extra: squad-guard plugin overrides (tests: accept_header=True)."""
    if role not in ROLES:
        raise ValueError(f"unknown role: {role} (use {', '.join(ROLES)})")
    yaml, s = _yaml(), settings()
    profile = profile or role
    dest = home() / "profiles" / profile
    if not dest.exists():
        hermes("profile", "create", profile, "--clone-from", "default", "--no-alias", "--description",
               DESCRIPTIONS[role].format(business=s.business.name, owner=s.business.owner))
    print(f"[{profile}] profile at {dest}")
    squad_env = {"SQUAD_HOME": str(ROOT), "SQUAD_WORKSPACE": str(s.workspace)}

    env_p = dest / ".env"
    lines = [l for l in (env_p.read_text(encoding="utf-8").splitlines() if env_p.exists() else [])
             if not re.match(r"^(TELEGRAM_|WHATSAPP_|SQUAD_)", l)]
    if role in CUSTOMER_FACING:
        lines = [l for l in lines if re.match(MODEL_KEY_RE, l)]
    env_p.write_text("\n".join(lines + [f"{k}={v}" for k, v in squad_env.items()]) + "\n", encoding="utf-8")

    cfg_p = dest / "config.yaml"
    c = (yaml.safe_load(cfg_p.read_text(encoding="utf-8")) if cfg_p.exists() else None) or {}
    # Slash commands (/model, /restart, /new...) only for the operator's ids; with no admin listed Hermes lets every
    # chatter run them, so an unknown placeholder admin keeps the gate on.
    admins = [str(a) for a in _conf().get("admin_ids", [])] or ["operator-not-configured"]
    for plat in ("telegram", "whatsapp"):
        p = c.setdefault("platforms", {}).setdefault(plat, {})
        p["enabled"] = False
        p.setdefault("extra", {}).update(allow_admin_from=admins, user_allowed_commands=[],
                                         group_allow_admin_from=admins, group_user_allowed_commands=[])
    c.setdefault("plugins", {})["enabled"] = ["squad-guard"]
    c.setdefault("skills", {})["creation_nudge_interval"] = 0  # never self-reprogram from text it read
    # Short sessions: what matters lives in the CRM, so a reset is cheap and context never grows unbounded.
    c["session_reset"] = {"mode": "idle", "idle_minutes": 120 if role in CUSTOMER_FACING else 60, "notify": False}
    c.setdefault("compression", {}).update(enabled=True, threshold=0.5)
    if role in CUSTOMER_FACING:
        c.setdefault("memory", {}).update(memory_enabled=False, user_profile_enabled=False)
    c["mcp_servers"] = {"crm": {"command": _uv(), "args": ["run", "--project", str(ROOT), "squad", "mcp"],
                                "env": {"SQUAD_ROLE": role, **squad_env, **(env_extra or {})},
                                "connect_timeout": 60.0}}
    passthrough = c.setdefault("terminal", {}).setdefault("env_passthrough", [])
    passthrough += [v for v in squad_env if v not in passthrough]
    cfg_p.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding="utf-8")

    security = prompts.load("common/security.md")
    if role == "hunter":
        soul = prompts.load("hunter/SOUL.md") + "\n\n" + security
        (dest / "SOUL.md").write_text(soul, encoding="utf-8")
        secrets = section(soul, "Hard rules") + security
        research = dest / "skills" / "research"
        for skill in (ROOT / "agents" / "hunter" / "skills").iterdir():
            shutil.rmtree(research / skill.name, ignore_errors=True)
            shutil.copytree(skill, research / skill.name)
            for md in (research / skill.name).rglob("*.md"):
                md.write_text(prompts.read(md), encoding="utf-8")
    else:
        agents = s.section("agents")
        base = prompts.load(f"{role}/SOUL.md")
        playbook = prompts.load(f"{role}/playbook.md")
        catalog = prompts.read(ROOT / agents.get("catalog", "examples/web-studio/catalog.md"))
        replies = prompts.read(ROOT / agents.get("replies", "examples/web-studio/replies.md"))
        parts = [base, "# Catalog\n\n" + catalog, "# Canned replies\n\n" + replies, playbook, security]
        (dest / "SOUL.md").write_text("\n\n".join(parts), encoding="utf-8")
        # What must NEVER show up in a reply: rules, playbook and defenses (not the identity or the catalog, which the
        # customer may hear). The plugin blocks any reply that repeats a long enough run of words from here.
        secrets = section(base, "Hard rules") + playbook + security
        shutil.rmtree(dest / "skills", ignore_errors=True)  # no skills: everything is in the SOUL
        (dest / "skills").mkdir()

    plug = dest / "plugins" / "squad-guard"
    shutil.rmtree(plug, ignore_errors=True)
    shutil.copytree(PKG / "guard" / "hermes_plugin", plug, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(PKG / "guard" / "detector.py", plug / "detector.py")
    shutil.copy(PKG / "phone.py", plug / "phone.py")
    (plug / "secrets.txt").write_text(secrets, encoding="utf-8")
    b = s.business
    (plug / "config.json").write_text(json.dumps({
        "role": role, "uv": _uv(), "project": str(ROOT), "secrets": str(plug / "secrets.txt"), "strike_hours": 24,
        "accept_header": False, "safe_reply": s.section("guard").get("safe_reply", SAFE_REPLY),
        "country_code": b.country_code, "area_code_digits": b.area_code_digits,
        "subscriber_digits": b.subscriber_digits, "owner": b.owner, **(guard_extra or {})}, indent=2), encoding="utf-8")

    if role in CUSTOMER_FACING:
        _disable_toolsets(profile)
    print(f"[{profile}] ok")


def _disable_toolsets(profile: str) -> None:
    """Toolset names vary across Hermes versions: try all at once, then one by one, tolerating unknown names."""
    binary = _conf().get("bin") or shutil.which("hermes") or "hermes"
    run = lambda *ts: subprocess.run([binary, "-p", profile, "tools", "disable", *ts], capture_output=True)  # noqa: E731
    if run(*CUSTOMER_DISABLED).returncode != 0:
        for t in CUSTOMER_DISABLED:
            run(t)


def providers() -> dict[str, dict]:
    return {**PROVIDERS, **_conf().get("providers", {})}


def switch_model(name: str, profiles: list[str], model: str | None = None) -> None:
    """Points profiles at another provider preset. A preset with api_key_env requires that key in the profile's
    .env (you put it there; this command never sees or copies it). The memory index does not depend on the chat
    model, so switching never requires reindexing."""
    yaml, preset = _yaml(), providers()[name]
    for p in profiles:
        base = home() / "profiles" / p
        if not base.exists():
            sys.exit(f"no such profile: {p}")
        key = preset.get("api_key_env")
        if key and not _read_env(base / ".env").get(key, "").strip().strip("'\""):
            sys.exit(f"[{p}] {key} is missing in {base / '.env'}. Add the key and run again.")
        cfg_p = base / "config.yaml"
        c = yaml.safe_load(cfg_p.read_text(encoding="utf-8")) or {}
        c["model"] = {**c.get("model", {}), "provider": preset["provider"], "base_url": preset["base_url"],
                      "default": model or preset["model"]}
        cfg_p.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"[{p}] -> {preset['provider']} / {c['model']['default']}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="squad hermes", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="install or refresh profiles").add_argument("roles", nargs="*", metavar="role")
    sw = sub.add_parser("switch-model", help="point profiles at another provider preset")
    sw.add_argument("provider", choices=list(providers()))
    sw.add_argument("--model")
    sw.add_argument("profiles", nargs="+")
    a = p.parse_args(sys.argv[1:] if argv is None else argv)
    if a.cmd == "install":
        if bad := set(a.roles) - set(ROLES):
            p.error(f"unknown role: {', '.join(bad)} (use {', '.join(ROLES)})")
        for role in a.roles or ROLES:
            install(role)
    else:
        switch_model(a.provider, a.profiles, a.model)


if __name__ == "__main__":
    main()
