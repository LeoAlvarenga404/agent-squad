"""FAKE Claude Code for the runner tests. Behaves according to FAKE_MODE:
ok (reports done), error (exits with an error), failed (reports 'failed: ...'), slow (sleeps),
deny (ok + permission denials)."""
import json
import os
import re
import sys
import time
from pathlib import Path

os.environ["SQUAD_ROLE"] = "builder"
from agent_squad import crm  # noqa: E402

args = sys.argv[1:]
if os.environ.get("FAKE_ARGS_OUT"):
    Path(os.environ["FAKE_ARGS_OUT"]).write_text(json.dumps(args), encoding="utf-8")
    Path(os.environ["FAKE_ARGS_OUT"]).with_name("child_env.json").write_text(json.dumps(dict(os.environ)), encoding="utf-8")
prompt = args[args.index("-p") + 1]
tid = int(re.search(r"ticket (\d+)", prompt).group(1))
mode = os.environ.get("FAKE_MODE", "ok")

if mode == "slow":
    time.sleep(8)
os.environ["SQUAD_TICKET_ID"] = str(tid)
if mode in ("ok", "deny"):
    crm.report("done by the fake")
if mode == "failed":
    crm.report("failed: could not build")
denials = [{"tool_name": "Bash(vercel deploy --prod)"}] if mode == "deny" else []
if mode == "error":
    print(json.dumps([{"type": "system"}, {"type": "result", "is_error": True, "result": "broke"}]))
    sys.exit(1)
# same shape as the real Claude Code: a LIST of events, the last one of type 'result'
print(json.dumps([{"type": "system"}, {"type": "assistant"},
                  {"type": "result", "is_error": False, "result": "ok", "permission_denials": denials,
                   "total_cost_usd": 0.5}]))
