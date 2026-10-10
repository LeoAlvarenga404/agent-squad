# Architecture

## Coordination through state, not conversation

Agents never message each other. Each one reads and writes the CRM (Postgres) through an MCP server that exposes
**only the tools of its role**. One agent changes a stage or opens a ticket; the next reacts. There are no agent
loops, and every action lands in `events` with the role that did it.

```
lead source ─▶ hunter ─▶ deals(target) ─▶ human sends outreach ─▶ deals(contacted)
                                                        │
                     sales ◀── customer replies ────────┘
                       │ request_approval(preview)  ──▶ human approves ──▶ ticket(briefing, queued)
                       │                                                       │
                       │                                   builder runner/worker ▼
                       │                       Claude Code build ─▶ reviewer ─▶ fixes (≤ N rounds)
                       │                                                       │
                       │                        request_send_preview ──▶ human approves ──▶ customer sees it
                       │ request_approval(close_deal) ──▶ human approves ──▶ move_stage(won)
                       ▼
                    account ─▶ create_ticket / start_ticket ─▶ builder ─▶ ...
```

## Trust boundaries

| Boundary | Enforcement |
|---|---|
| What an agent can do | MCP tools per role (`crm.ROLE_TOOLS`); the CRM re-checks the role on every write |
| Stage moves | `ROLE_STAGES`; `won` needs a `close_deal` approval; `contacted`/`delivered` are human only |
| Which customer a conversation touches | `SQUAD_SCOPE_SLUG` in the CRM and the guard's `pre_tool_call`, both from the channel sender |
| Customer text reaching the builder | only the ticket **id** goes in the prompt; the description is read through MCP as data |
| Production | denied tool rules (`--prod`, domains, alias, push) plus a human `send_preview` approval |
| Prompt injection | deterministic detector on input, on third-party content (dossiers, memory, web) and on writes; leak filter on output |
| Money and volume | caps in code/config: rounds per day, leads per round, concurrent builders, daily builder cost, monthly changes |
| Lead data | open data only, minimization in the dossier, retention with `squad purge` |

## Processes

| Process | Command | Role |
|---|---|---|
| CRM MCP server | `squad mcp` (spawned by each agent) | per `SQUAD_ROLE` |
| Builder runner | `squad build TICKET` (spawned by `dispatch` in `spawn` mode) | `runner` |
| Builder worker | `squad worker` (long-running, `worker` mode) | `runner` |
| Approvals bot | `squad bot` | `human` |
| Human CLI | `squad pending`, `approve`, `move`, `directive`, `dashboard`... | `human` |
| Hunter / sales / account | Hermes Agent profiles installed by `squad hermes install` | their role |
| Coordinator | Claude Code in `agents/coordinator/` | `coordinator` |

## Code map

```
src/agent_squad/
  config.py      squad.toml + .env
  db.py          connection, params, audit log
  crm/           core rules, leads, deals, tickets + queue, approvals, ops; server.py = MCP
  memory.py      per-customer hybrid RAG (pgvector + full text, RRF)
  guard/         prompt-injection detector + Hermes plugin
  builder/       runner, worker, prompts, log viewer
  adapters/      deploy, review, notify, leads
  bot.py         Telegram approvals bot
  hermes/        profile installer and model switcher
  cli.py         the `squad` command
agents/          prompt sources per role (rendered with squad.toml values)
examples/        an example catalog for a web studio
migrations/      Postgres schema
tests/           pytest against a throwaway database
sim/             simulated customers against real agent profiles (costs tokens)
evals/           memory retrieval evaluation
```
