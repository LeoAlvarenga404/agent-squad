"""REAL test (spends a little Claude Code usage): confirms the runner's `claude -p`
  1. connects to the CRM MCP server with the builder role and reads a ticket (right tools, only the role's);
  2. honors the permission rules, including a wildcard in the middle (`Bash(echo hello*--forbidden*)`), the same shape
     used to block `vercel deploy*--prod*`.
Builds no site and deploys nothing: a throwaway database and `echo` commands.

  uv run pytest -m real tests/test_builder_real.py -s
"""
import json
import os
import subprocess

import pytest

from agent_squad import db
from agent_squad.builder import runner

pytestmark = [pytest.mark.real, pytest.mark.usefixtures("squad_db")]


def test_claude_reads_ticket_and_honors_wildcard_deny(tmp_path):
    with db.connect() as c:
        c.execute("insert into deals(slug, origin, next_action, next_action_at, stage) values "
                  "('cli-smoke','test','x','2099-01-01','won')")
        tid = c.execute("insert into tickets(slug, type, description, status) values "
                        "('cli-smoke','change','change opening hours to 8am','running') returning id").fetchone()["id"]
    mcp = runner.write_mcp_config(tmp_path / "mcp.json")
    prompt = (f"Connection test, change nothing in the CRM or in files. 1) Call get_ticket with id={tid} and tell me "
              "the ticket type. 2) Try to run the exact Bash command: echo hello --forbidden (if denied, that is fine, "
              "just report it). 3) Run the exact Bash command: echo hello --allowed. 4) List the names of the "
              "mcp__crm tools you can use. Answer briefly.")
    cmd = [os.environ.get("CLAUDE_BIN", "claude"), "-p", prompt, "--output-format", "json",
           "--mcp-config", str(mcp), "--strict-mcp-config", "--permission-mode", "acceptEdits",
           "--no-session-persistence", "--allowedTools", "Bash(echo:*)", "mcp__crm",
           "--disallowedTools", "Bash(echo hello*--forbidden*)"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert r.stdout.strip(), f"no output (exit {r.returncode}). stderr:\n{r.stderr[:800]}"
    j = runner.parse_result(r.stdout)
    denials = [d.get("tool_name") + " " + json.dumps(d.get("tool_input"), ensure_ascii=False)[:80]
               for d in j.get("permission_denials", [])]
    print("cost (API-equivalent): US$", j.get("total_cost_usd"), "\nresult:", (j.get("result") or "")[:700],
          "\ndenials:", denials)
    assert "change" in (j.get("result") or "").lower(), "did not read the ticket through MCP"
    assert any("--forbidden" in d for d in denials), "the mid-pattern wildcard rule did NOT block: do not trust it"
    assert not any("--allowed" in d for d in denials), "blocked the command that should be allowed"
