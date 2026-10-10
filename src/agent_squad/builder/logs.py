"""Read-only live view of what each agent is doing. Controls nothing.

  squad logs                     wait for the next builder session and follow it
  squad logs TICKET [--final]    follow ticket N from the start (--final: only the result)
  squad logs claude              follow the builder's Claude Code sessions (builds and fixes), switching to new ones
  squad logs codex               follow the reviewer
  squad logs hunter|sales|account   follow a Hermes profile (and its <role>_sim test profile)

Builder logs are ROOT/logs/ticket-N*.jsonl, written event by event by the runner. Hermes profiles are read from
<home>/profiles/<profile>/state.db ([hermes] home, else $HERMES_HOME, else the platform default).
"""
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from ..config import ROOT, settings

LOGS = ROOT / "logs"
GREEN, YELLOW, RED, GRAY, END = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"
HERMES_ROLES = ("hunter", "sales", "account")


def short(x, n=110) -> str:
    s = " ".join(str(x).split())
    return s if len(s) <= n else s[:n - 3] + "..."


def show(ev: dict) -> None:
    t = ev.get("type")
    if t == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                print(f"{GREEN}Builder:{END} {short(block['text'], 300)}")
            elif block.get("type") == "tool_use":
                inp = block.get("input", {})
                target = inp.get("command") or inp.get("file_path") or inp.get("path") or inp.get("id") \
                    or inp.get("pattern") or json.dumps(inp, ensure_ascii=False)
                print(f"{YELLOW}  > {block.get('name')}{END} {GRAY}{short(target)}{END}")
    elif t == "user":
        for block in ev.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(x.get("text", "") for x in content if isinstance(x, dict))
                print(f"{RED if block.get('is_error') else GRAY}    < {short(content or '(empty)', 160)}{END}")
    elif t == "result":
        denials = [d.get("tool_name") for d in ev.get("permission_denials", [])]
        print(f"\n{RED if ev.get('is_error') else GREEN}END{END} cost (API-equivalent): US$ {ev.get('total_cost_usd')} "
              f"| duration: {round((ev.get('duration_ms') or 0) / 60000, 1)} min"
              + (f" | permission denials: {denials}" if denials else ""), flush=True)
        print(short(ev.get("result", ""), 600), flush=True)


def show_line(line: str) -> None:
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return
    if isinstance(ev, dict):
        show(ev)
        sys.stdout.flush()


def follow_ticket(path: Path | None, final_only: bool) -> None:
    if path is None:
        before = {p.name for p in LOGS.glob("ticket-*.jsonl")}
        print("waiting for the next builder session... (Ctrl+C to quit)")
        while not (new := [p for p in LOGS.glob("ticket-*.jsonl") if p.name not in before]):
            time.sleep(1)
        path = max(new, key=lambda p: p.stat().st_mtime)
    while not path.exists():
        time.sleep(1)
    print(f"following {path.name}\n")
    with open(path, encoding="utf-8", errors="replace") as f:
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not final_only or ev.get("type") == "result":
                show(ev)
            if ev.get("type") == "result":
                return


def follow_newest(pattern: str, show_fn) -> None:
    """Follows the newest file matching the pattern and switches when a newer one appears (next ticket, fix)."""
    current, f = None, None
    while True:
        line = f.readline() if f else ""
        if line:
            show_fn(line)
            continue
        newest = max(LOGS.glob(pattern), key=lambda p: p.stat().st_mtime, default=None)
        if newest and newest != current:
            if f:
                f.close()
            current, f = newest, open(newest, encoding="utf-8", errors="replace")
            print(f"\n=== {newest.relative_to(LOGS)} ===", flush=True)
        else:
            time.sleep(0.5)


def hermes_home() -> Path:
    home = settings().section("hermes").get("home") or os.environ.get("HERMES_HOME")
    if home:
        return Path(home).expanduser()
    return Path(os.environ["LOCALAPPDATA"]) / "hermes" if os.name == "nt" else Path.home() / ".hermes"


def show_hermes(label: str, role: str, content: str | None, calls: str | None) -> None:
    if role == "user" and content:
        print(f"{GRAY}request:{END} {short(content, 200)}")
    elif role == "tool":
        print(f"{GRAY}    < {short(content or '(empty)', 160)}{END}")
    elif role == "assistant":
        for call in json.loads(calls or "[]"):
            fn = call.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            target = (args.get("command") or args.get("query") or args.get("url") or args.get("path")
                      or json.dumps(args, ensure_ascii=False)) if isinstance(args, dict) else args
            print(f"{YELLOW}  > {fn.get('name')}{END} {GRAY}{short(target)}{END}")
        if content and content.strip():
            print(f"{GREEN}{label}:{END} {short(content, 400)}")


def follow_hermes(role: str) -> None:
    """Reads new messages from each profile's state.db (read-only), starting with the last 12 for context."""
    cursor, session = {}, {}
    while True:
        for profile in (role, f"{role}_sim"):
            path = hermes_home() / "profiles" / profile / "state.db"
            if not path.exists():
                continue
            try:
                with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)) as c:
                    if profile not in cursor:
                        cursor[profile] = c.execute("select coalesce(max(id), 0) from messages").fetchone()[0] - 12
                    new = c.execute("select id, session_id, role, content, tool_calls from messages where id > ? "
                                    "order by id", (cursor[profile],)).fetchall()
            except sqlite3.Error:
                continue
            for mid, sid, r, content, calls in new:
                cursor[profile] = mid
                if session.get(profile) != sid:
                    session[profile] = sid
                    print(f"\n=== {profile}, session {sid} ===")
                show_hermes(role.capitalize(), r, content, calls)
            sys.stdout.flush()
        time.sleep(1)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles (cp1252) can't print everything
    args = [a for a in argv if not a.startswith("--")]
    target = args[0] if args else ""
    LOGS.mkdir(exist_ok=True)
    try:
        if not target:
            follow_ticket(None, "--final" in argv)
        elif target.isdigit():
            follow_ticket(LOGS / f"ticket-{target}.jsonl", "--final" in argv)
        elif target == "claude":
            print("following the builder live (read-only, Ctrl+C to quit)", flush=True)
            follow_newest("ticket-*.jsonl", show_line)
        elif target == "codex":
            print("following the reviewer live (read-only, Ctrl+C to quit)", flush=True)
            follow_newest("ticket-*-review-*/codex.log", lambda s: print(s.rstrip(), flush=True))
        elif target in HERMES_ROLES:
            print(f"following {target} live (read-only, Ctrl+C to quit)", flush=True)
            follow_hermes(target)
        else:
            sys.exit(__doc__)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
