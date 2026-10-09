"""Settings: business values from squad.toml, secrets from .env / the environment.

Root resolution: $SQUAD_HOME, else the repository checkout this package lives in.
Config file: $SQUAD_CONFIG, else <root>/squad.toml (missing file = defaults).
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path

ROOT = Path(os.environ.get("SQUAD_HOME") or Path(__file__).resolve().parents[2])


@dataclass
class Business:
    name: str = "Acme Studio"
    owner: str = "the owner"
    language: str = "pt-BR"
    country_code: str = "55"
    area_code_digits: int = 2
    subscriber_digits: int = 8


@dataclass
class Limits:
    max_rounds_per_day: int = 3
    max_leads_per_round: int = 10
    max_builders: int = 2
    builder_timeout_min: int = 40
    builder_max_attempts: int = 3
    builder_max_usd: str = ""
    builder_max_usd_day: str = ""
    review_rounds: int = 3
    lead_retention_days: int = 30


@dataclass
class Adapters:
    deploy: str = "vercel"
    review: str = "codex"
    notify: str = "telegram"
    leads: str = "cnpj"


@dataclass
class Settings:
    business: Business = field(default_factory=Business)
    limits: Limits = field(default_factory=Limits)
    adapters: Adapters = field(default_factory=Adapters)
    workspace: Path = ROOT / "workspace"
    raw: dict = field(default_factory=dict)

    def section(self, name: str) -> dict:
        return self.raw.get(name, {})


def load_dotenv(path: Path | None = None) -> None:
    """KEY=value lines; never overrides the real environment. Inline ' #' comments are stripped."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip("'\"")
        if re.fullmatch(r"[A-Z_][A-Z0-9_]*", key.strip()) and value:
            os.environ.setdefault(key.strip(), value)


def _build(cls, data: dict):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


@lru_cache(maxsize=1)
def settings() -> Settings:
    load_dotenv()
    path = Path(os.environ.get("SQUAD_CONFIG") or ROOT / "squad.toml")
    raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    workspace = os.environ.get("SQUAD_WORKSPACE") or raw.get("paths", {}).get("workspace") or ROOT / "workspace"
    return Settings(
        business=_build(Business, raw.get("business", {})),
        limits=_build(Limits, raw.get("limits", {})),
        adapters=_build(Adapters, raw.get("adapters", {})),
        workspace=(ROOT / workspace).resolve(),
        raw=raw,
    )


# OS basics a child process needs. Everything else (tokens, DSNs, API keys loaded from .env) stays out.
_BASE_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME",
             "HOMEDRIVE", "HOMEPATH", "USERPROFILE", "USERNAME", "USER", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
             "PROGRAMFILES", "PROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "LANG",
             "LC_ALL", "TERM", "SHELL", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME")


def child_env(*keep: str, **extra: str) -> dict[str, str]:
    """Environment for processes that handle untrusted content (builder, reviewer, hunter): OS basics, `keep` and
    [security] env_passthrough. Secrets from .env never reach them unless listed there."""
    names = {n.upper() for n in (*_BASE_ENV, *keep, *settings().section("security").get("env_passthrough", []))}
    return {**{k: v for k, v in os.environ.items() if k.upper() in names}, **extra}
