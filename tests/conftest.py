"""Every test runs against a throwaway Postgres database built from migrations/ and dropped afterwards."""
import os
import uuid

import psycopg
import pytest

from agent_squad.config import ROOT, settings

ADMIN_DSN = os.environ.get("SQUAD_ADMIN_DSN", "postgresql://squad:squad_dev@127.0.0.1:5433/postgres")


@pytest.fixture(scope="module")
def squad_db():
    name = "squad_test_" + uuid.uuid4().hex[:8]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(f'create database "{name}"')
    dsn = ADMIN_DSN.rsplit("/", 1)[0] + "/" + name
    try:
        with psycopg.connect(dsn, autocommit=True) as c:
            for f in sorted((ROOT / "migrations").glob("*.sql")):
                c.execute(f.read_text(encoding="utf-8"))
        old = {k: os.environ.get(k) for k in ("SQUAD_DSN", "SQUAD_AGENT_DSN")}
        os.environ["SQUAD_DSN"] = dsn
        os.environ["SQUAD_AGENT_DSN"] = f"postgresql://squad_agent:squad_agent_dev@{dsn.split('@', 1)[1]}"
        os.environ["SQUAD_DRY_RUN"] = "1"
        yield dsn
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
            c.execute(f'drop database if exists "{name}" with (force)')


@pytest.fixture
def as_role(monkeypatch):
    def set_role(role: str) -> None:
        monkeypatch.setenv("SQUAD_ROLE", role)
    return set_role


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "workspace", tmp_path)
    return tmp_path
