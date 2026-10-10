"""You are the customer: chat by hand with the REAL sales or account agent (<role>_sim profiles), on the same kind of
throwaway database and with the builder off as sim.run. Costs tokens of the profiles' model.

  uv run python -m sim.chat sales sim-clinica
  uv run python -m sim.chat account sim-padaria
  uv run python -m sim.chat sales new          # unknown number (a lead out of nowhere)

Seeded customers: sim-clinica (negotiation), sim-padaria (recurring), sim-oficina (won), sim-salao (delivered).
Commands: /state shows what changed in the database, /quit ends (the database goes with it).
"""
import json
import sys

import psycopg
from psycopg.rows import dict_row

from .run import CUSTOMERS, UNKNOWN_PHONE, configure_mcp, header, install, seed, talk, throwaway_db


def state(dsn: str) -> None:
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        for t, sql in [("deals", "select slug, stage, next_action from deals order by slug"),
                       ("approvals", "select * from approvals order by id"),
                       ("tickets", "select id, slug, type, status, description from tickets order by id")]:
            print(f"--- {t}")
            for r in c.execute(sql).fetchall():
                print("  " + json.dumps(r, ensure_ascii=False, default=str))


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("sales", "account") or sys.argv[2] not in [*CUSTOMERS, "new"]:
        sys.exit(__doc__)
    role, customer = sys.argv[1], sys.argv[2]
    slug = None if customer == "new" else customer
    phone = UNKNOWN_PHONE if slug is None else CUSTOMERS[slug]["phone"]
    install(role)
    with throwaway_db() as dsn:
        seed(dsn, None)
        configure_mcp(role, dsn, slug)
        print(f"You are +{phone} ({customer}) talking to {role}. /state, /quit.")
        session = None
        while True:
            try:
                msg = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if msg == "/quit":
                break
            if msg == "/state":
                state(dsn)
                continue
            if msg:
                reply, session = talk(role, header(phone, msg), session, False, dsn)
                print(f"\n{role}> {reply}")
        state(dsn)


if __name__ == "__main__":
    main()
