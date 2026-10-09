import json
import os

import psycopg
from psycopg.rows import dict_row

from .config import settings

# Roles that connect with the admin DSN. Agents and the runner use the least-privilege SQUAD_AGENT_DSN.
ADMIN_ROLES = {"human", "coordinator", ""}


def dsn() -> str:
    settings()  # loads .env
    var = "SQUAD_DSN" if os.environ.get("SQUAD_ROLE", "") in ADMIN_ROLES else "SQUAD_AGENT_DSN"
    value = os.environ.get(var)
    if not value:
        raise RuntimeError(f"{var} is not set (see .env.example)")
    return value


def connect() -> psycopg.Connection:
    return psycopg.connect(dsn(), row_factory=dict_row, autocommit=True)


def jsonable(x):
    return json.loads(json.dumps(x, default=str))


def get_param(c, key: str, default: str | None = None) -> str | None:
    row = c.execute("select value from params where key=%s", (key,)).fetchone()
    return row["value"] if row else default


def put_param(c, key: str, value: str) -> None:
    c.execute("insert into params(key, value) values (%s, %s) on conflict (key) do update "
              "set value=excluded.value, updated_at=now()", (key, value))


def log_event(c, actor: str, action: str, ref, payload: dict | None = None) -> None:
    c.execute("insert into events(actor, action, ref, payload) values (%s, %s, %s, %s)",
              (actor, action, str(ref), json.dumps(payload or {}, default=str)))
