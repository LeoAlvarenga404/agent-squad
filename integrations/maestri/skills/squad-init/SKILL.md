---
name: squad-init
description: Builds from scratch (or checks, without duplicating) the squad's Maestri canvas from the CLI: the builder worker, the approvals bot, the dashboard, the read-only agent views, the live portal and the connection for builder questions. Use when the user says "set up the squad in Maestri", "build the canvas", "squad-init" or asks to create the squad's canvas.
---

# Set up the squad's Maestri canvas (CLI)

You are the **root** (Claude Code in Maestro Mode, in `agents/coordinator`). The flow runs by itself in code: Maestri
is the window, not the engine. There are no routines and no interactive build or review agents: the builder worker
(`squad worker`) pulls the queue, runs each ticket in a fresh Claude Code session, calls the reviewer and asks for the
approval through the bot. Your only job here is to build the window. **Idempotent:** run `maestri list` first and
reuse what exists.

`$SQUAD` below is the squad repository root (two levels above this terminal's directory).

## 0. Preconditions (stop and tell the user if one fails)
1. `pwd` must be `$SQUAD/agents/coordinator`. If not: "open the Maestro Claude Code in `<repo>/agents/coordinator`"
   and stop.
2. `maestri debug` is healthy and `maestri list` works. If it answers "This terminal is not the Maestro": "turn on
   Maestro Mode in this terminal (Details tab) and call me again". Stop.
3. This terminal must be named `root`. If not, ask the user to rename it in the UI (there is no command) and go on.
4. Confirm the `Shell` preset exists (`maestri preset list`).

## 1. Terminals (Shell preset, directory `$SQUAD`). Recruit only the missing ones
```
maestri recruit "builder"   --preset "Shell" --dir "$SQUAD" --command "uv run squad worker"
maestri recruit "telegram"  --preset "Shell" --dir "$SQUAD" --command "uv run squad bot"
maestri recruit "dashboard" --preset "Shell" --dir "$SQUAD" --command "uv run squad dashboard --watch 20"
maestri recruit "claude"    --preset "Shell" --dir "$SQUAD" --command "uv run squad logs claude"
maestri recruit "codex"     --preset "Shell" --dir "$SQUAD" --command "uv run squad logs codex"
maestri recruit "hunter"    --preset "Shell" --dir "$SQUAD" --command "uv run squad logs hunter"
maestri recruit "sales"     --preset "Shell" --dir "$SQUAD" --command "uv run squad logs sales"
maestri recruit "account"   --preset "Shell" --dir "$SQUAD" --command "uv run squad logs account"
```
- `builder`: the worker. Shows live what Claude Code does on each ticket and the reviewer's verdict. It must print
  "builder worker up".
- `telegram`: the user's screen (approvals, news, daily report, hunter schedule). It must log "bot up". If it asks
  for a token, `.env` lacks `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`: say so in the final report.
- `claude`, `codex`, `hunter`, `sales`, `account`: **read-only** views (`squad logs`). They show live what the ticket's
  Claude Code does, what the reviewer sees, and what each Hermes profile searches, which tool it uses and what it
  answers. They control nothing. Never type in them.
- Optionally a `client` terminal where the user plays the customer on a test channel. Never type in it and never
  connect it to anything.
- One already exists with another `--command`? Replace it in place:
  `maestri recruit "<name>" --preset "Shell" --dir ... --command ... --replace "<name>"`. Before replacing `client`,
  run `maestri check "client"`: if a conversation is in progress, ask the user.

## 2. Live portal (the site being built)
```
maestri portal create "about:blank" "live" --size 1280x800
maestri connect "live" "builder"
```
The worker starts the dev server of the site under construction (`[builder] live_port`, default 3400) and points
`live` at it; at the end, at the preview.

## 3. Connection for questions
```
maestri connect "builder" "root"
maestri connect "builder" "claude"
maestri connect "claude" "codex"
```
Each ticket's Claude Code runs inside the `builder` terminal. It never messages other terminals: open questions come
back as pending items in the `send_preview` approval. `builder` -> `claude` -> `codex` only draws the chain (worker,
build, review). **Never** connect `client`, `sales` or `account` to anything: they carry customer text.

## 4. Builder mode
`crm` MCP tool: `set_param("builder_mode", "worker")` (the worker pulls the queue). Confirm it in the result.

## 5. What NOT to create
No routines (the schedule and the reports belong to the bot), no builder, reviewer or QA Claude Code (the worker
creates a fresh one per ticket), no notes. If the canvas has leftovers from an older setup, **do not delete them**:
list them in the report so the user removes them in the UI.

## 6. Report
A short table: what was created, what already existed, leftovers from an older setup and **what only the user does**
(rename this terminal to `root`, turn on Maestro Mode, the Telegram token in `.env`, `codex login` for the reviewer,
any API keys the build needs). Say how to tell it is alive: `builder` with "builder worker up", the views with
"following ... live", `telegram` with "bot up", and `/status` answering in Telegram.

Never run `dismiss`, `portal close`, `note delete`, `routine delete`, `floor land` or `floor delete`.
To operate after setup, use the `squad-ops` skill.
