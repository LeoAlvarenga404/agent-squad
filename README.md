# agent-squad

A squad of narrowly scoped AI agents that runs the sales and delivery of websites for local businesses: it finds the
lead, qualifies it, talks to the owner, builds a free preview, closes the deal (with human approval) and handles
after-sales changes. A human decides everything that costs money or reputation.

It started as a study of how far well-bounded agents can go in a real small-studio workflow. Everything is configurable
(business, limits, prompts, catalog) and the integrations that differ between setups (deploy, review, notifications,
lead source) are swappable adapters.

> Status: built and tested locally with **simulated** customers. Read
> [`docs/production-checklist.md`](docs/production-checklist.md) before putting a real customer in front of it.

## How it works

```
                        you (Telegram bot + CLI)
         approve / reject · directives · priorities · hours · dashboard
                                  │
   ┌──────────────────────────────┼──────────────────────────────┐
   │          Postgres: state, per-customer memory, audit         │
   │   deals · tickets · approvals · directives · events · memories (pgvector)
   └──────▲────────────▲─────────────▲─────────────▲──────────────┘
          │ MCP "crm" exposes DIFFERENT tools to each role        │
   ┌──────┴─────┐ ┌────┴──────┐ ┌────┴──────┐ ┌────┴─────────┐ ┌──────────────┐
   │  Hunter    │ │  Sales    │ │  Account  │ │  Builder     │ │ Coordinator  │
   │  (Hermes)  │ │  (Hermes) │ │  (Hermes) │ │ (Claude Code)│ │ (Claude Code)│
   │ finds leads│ │ talks and │ │ after-    │ │ gets only a  │ │ turns your   │
   │ in open    │ │ negotiates│ │ sales,    │ │ ticket ID,   │ │ requests into│
   │ data       │ │           │ │ tickets   │ │ ships PREVIEW│ │ directives   │
   └────────────┘ └───────────┘ └───────────┘ └──────┬───────┘ └──────────────┘
                                                     │ reviewed by another model family (Codex, read-only)
```

Agents **never talk to each other**. They coordinate through state: one moves a stage or opens a ticket, the next
reacts. No agent loops, and every action is audited in `events`. Details in [`docs/architecture.md`](docs/architecture.md).

## Design decisions

| Decision | Why |
|---|---|
| Tools per role, not per prompt | An agent cannot do what it has no tool for. The CRM also re-checks the role on every write. |
| Closing, off-range prices and anything customer-visible need **human approval** | A sales mistake costs money; a publishing mistake costs reputation. |
| The builder receives **only the ticket ID** | The description comes from a customer (untrusted). It is read through MCP as data, never as a shell instruction. |
| The builder builds; code deploys | Claude runs in the site folder with a minimal tool list, a scrubbed environment and the OS sandbox. The runner deploys the preview and validates the URL. |
| A second model reviews every preview | Screenshots + code go to a read-only reviewer from another model family; up to N fix rounds. |
| Per-customer memory with a **mandatory slug filter** and session scope | Isolation between customers is a security requirement. 324 cross-customer queries: 0 leaks. |
| Customer-facing agents only have the CRM | No terminal, files, web, skills or native memory: an agent exposed to customer text cannot rewrite its own rules. |
| Deterministic guard around the LLM | Injection detection on input and third-party content, sender-bound tool scope, leak filter on output. Code, not prompts. |
| Caps are **code** | Rounds per day, leads per round, concurrent builders, daily builder cost, monthly changes: rules that matter cannot depend on the model remembering. |
| Machine time, cost and **human hours** per ticket | The business margin depends on how much human time each site takes. |
| Open data and minimization | Leads come from open company registries; untouched leads expire with their folder. |

## Quick start

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Docker. For the agents: [Hermes Agent](https://github.com/NousResearch/hermes-agent),
[Claude Code](https://claude.com/claude-code) and, optionally, the [Codex CLI](https://github.com/openai/codex) as reviewer.

```bash
git clone https://github.com/LeoAlvarenga404/agent-squad && cd agent-squad
cp .env.example .env                 # secrets
cp squad.example.toml squad.toml     # your business, limits and adapters
docker compose up -d                 # Postgres + pgvector on 127.0.0.1:5433
uv sync
uv run pytest                        # everything against a throwaway database

uv run squad hermes install          # hunter, sales and account profiles from agents/
uv run squad bot                     # approvals on your phone (docs/telegram.md)
uv run squad dashboard --watch 20
```

Before real customers, read [`docs/security.md`](docs/security.md): some guarantees (OS user separation,
sandboxing on Windows) depend on how you deploy.

Simulated customers against the real agent profiles (costs tokens): `uv run python -m sim.run`.
Talk to the sales agent yourself: `uv run python -m sim.chat sales new`.

## The `squad` command

| Command | What it does |
|---|---|
| `squad pending` / `approve ID` / `reject ID` | human approvals (the bot uses the same path) |
| `squad move SLUG STAGE "next action" YYYY-MM-DD` | move a deal |
| `squad directive add ROLE "text" [days]` / `ls` / `rm ID` | standing instructions for a role; every directive expires |
| `squad priority lead\|ticket REF N` | higher goes first |
| `squad param KEY VALUE` | soft parameters: `leads_per_round`, `hunter_paused`, `builder_mode`, `preview_revisions` |
| `squad hours TICKET H` | log your own hours on a ticket |
| `squad start TICKET` / `dispatch` | queue a ticket / start what fits |
| `squad build TICKET` / `worker` / `logs` | run the builder once, run the queue worker, follow live logs |
| `squad bot` | Telegram approvals bot |
| `squad leads search ...` | lead candidates from the configured source |
| `squad hermes install` / `switch-model` | build Hermes profiles from `agents/`, change their model |
| `squad purge` | delete expired leads and their folders |
| `squad mcp` | the CRM MCP server (`SQUAD_ROLE` picks the tools) |

## Make it yours

- **Business**: `squad.toml` (`[business]` name, owner, customer language, phone format; `[limits]`; adapter sections).
- **Prompts**: `agents/<role>/` (rendered with `{{business}}`, `{{owner}}`, `{{language}}`, `{{workspace}}`).
- **Catalog**: point `[agents] catalog` to your own file; `examples/web-studio/` shows the format.
- **Integrations**: write an adapter ([`docs/adapters.md`](docs/adapters.md)).
- **Maestri canvas**: optional skills `squad-init` and `squad-ops` in [`integrations/maestri`](integrations/maestri).

## What was verified, and what was not

Verified: CRM and role rules; lead search on real open data; the builder runner with a fake Claude and with real
Claude Code for connection and permissions; the approvals bot end to end with real Telegram; per-customer memory;
real hunter cycles; simulated customer scenarios, including prompt-injection attacks ([`docs/results.md`](docs/results.md)).

**Not verified:** a real WhatsApp channel, a real customer. Simulated results use an inexpensive model and run once
per scenario: models are not deterministic.

## Known limitations

- Full-text search uses the `squad` text search configuration (a copy of `portuguese`; change it in `migrations/`). Its
  stemmer does not link every word variation; vector search covers part of that.
- The embedding model truncates long chunks; memory numbers come from 25 synthetic questions.
- No Google/Maps scraping by design (terms of service): dossiers lack ratings unless the owner shares the listing.
- The bundled lead source (Brazilian CNPJ open data) is country-specific; other countries need a leads adapter.

## License

MIT
