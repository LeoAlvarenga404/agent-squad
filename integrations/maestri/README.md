# Maestri integration (optional)

[Maestri](https://maestri.app) is a canvas of terminals, portals and notes. The squad does not need it: every piece is
a plain process (`squad worker`, `squad bot`, `squad dashboard --watch 20`, `squad logs ...`) and the ticket flow is
code. Maestri is only a convenient window over those processes. The builder never talks to other terminals: a question
it cannot answer becomes a pending item in its report, which reaches you inside the `send_preview` approval.

```
 hunter (Hermes) ── bot schedule, weekdays ──► lead + outreach.md ──► Telegram: "new lead, outreach ready"
        │                                                              you send the outreach
        ▼
 sales (Hermes) ◄── the lead answers ──► asks for the preview (request_approval preview) ──► Telegram: approve?
        │                                                                                         │ ok
        ▼                                                                                         ▼
 CRM (Postgres): queued ticket ──► WORKER (squad worker, terminal "builder")
                                    1. a NEW Claude Code session builds and deploys a preview ("live" portal shows it)
                                    2. the reviewer (read-only) checks screenshots + code ──► changes?
                                    3. a NEW Claude Code session fixes the findings (bounded rounds)
                                    4. the CRM asks for "send_preview" with summary + verdict ──► Telegram: approve?
```

## Canvas
| Terminal | Command | What it is |
|---|---|---|
| `root` | Claude Code in `agents/coordinator`, Maestro Mode | the coordinator; answers builder questions |
| `builder` | `uv run squad worker` | pulls the queue (`builder_mode = worker`) |
| `telegram` | `uv run squad bot` | approvals, news, daily report, hunter schedule |
| `dashboard` | `uv run squad dashboard --watch 20` | CRM summary |
| `claude`, `codex` | `uv run squad logs claude` / `codex` | read-only: builder and reviewer, live |
| `hunter`, `sales`, `account` | `uv run squad logs <role>` | read-only: each Hermes profile, live |

Plus a `live` portal connected to `builder`, and connections `builder` -> `root` (questions) and
`builder` -> `claude` -> `codex` (drawing of the chain). No routines, no notes, no interactive agents. Never connect
anything that carries customer text (`sales`, `account`, a test `client`).

## Setup
1. Copy the skills into the coordinator's Claude Code skills folder:
   `agents/coordinator/.claude/skills/squad-init` and `.../squad-ops` (from `integrations/maestri/skills`).
2. Allow the Maestri CLI for the coordinator and deny the destructive commands, in
   `agents/coordinator/.claude/settings.json`:
   ```json
   "allow": ["Bash(maestri:*)"],
   "deny": ["Bash(maestri floor land:*)", "Bash(maestri floor delete:*)", "Bash(maestri note delete:*)",
            "Bash(maestri routine delete:*)", "Bash(maestri portal close:*)", "Bash(maestri dismiss:*)"]
   ```
3. In Maestri: a Claude Code terminal in `<repo>/agents/coordinator`, named `root`, with **Maestro Mode** on.
4. In it: `/squad-init`. Then, in Telegram: `/target <niche and city>` and `/status`.

| Skill | Use |
|---|---|
| `squad-init` | builds (or checks, idempotently) the canvas above and sets `builder_mode = worker` |
| `squad-ops` | day-to-day: "how is it going?", builder questions, delegation, hunter on demand |
