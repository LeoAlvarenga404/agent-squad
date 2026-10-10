# Security model

Customers, scraped web pages and lead data are **untrusted input** that reaches LLM agents. The design assumes any
agent can be talked into anything and puts the rules that matter in code, permissions and infrastructure.

## Who can be attacked, and what stops it

| Attacker controls | Reaches | Enforced by |
|---|---|---|
| Customer messages (WhatsApp) | sales / account agents | CRM-only toolset; guard plugin: sender-bound scope, injection alert and write lock, leak filter; Hermes slash commands limited to `[hermes] admin_ids` |
| Approval context written from customer text | your Telegram | context is length-capped, scanned, and shown quoted as agent-written |
| Ticket descriptions | the builder | only the ticket id in the prompt; description read as data; builder can read/report only its own ticket (`SQUAD_TICKET_ID`); no deploy tools |
| Web pages the hunter reads | the hunter, dossiers, outreach | content flagged by the guard; dossier scanned and size-capped before saving; outreach scanned before it reaches you |
| Site code the builder writes | `pnpm build`, the reviewer | Claude Code sandbox (macOS/Linux), scrubbed environment, runner-side deploy with URL validation |
| A compromised agent with a shell | the database | agents and runner use `squad_agent` (no superuser, cannot decide approvals or change params/directives) |

## What code alone cannot guarantee

These need your infrastructure. Do them before real customers (also in `production-checklist.md`):

1. **Separate OS user for agents.** The hunter has a terminal and the builder runs `pnpm build` on generated code.
   Under the same OS user they can read the repo's `.env` and act as the human through `squad`. Run Hermes and the
   builder as a user that cannot read `.env`, and keep `SQUAD_DSN` (admin) only in the operator's environment.
2. **Sandbox the builder.** The Claude Code sandbox runs on macOS and Linux/WSL2, not native Windows. On Windows,
   run the builder inside WSL2 or a container. Set `[builder] sandbox = "on"` to refuse running without it.
3. **Bind conversations in the gateway.** The guard resolves the customer from the channel's `sender_id`. If you
   write your own gateway, never take the sender from message text (`accept_header` is for tests only).
4. **Credentials.** Change the `squad` and `squad_agent` database passwords; use a Vercel token scoped to the
   preview project if your plan allows; keep the Telegram bot dedicated to approvals.
5. **The model provider sees customer conversations.** Pick one whose retention policy you accept.

## Defaults worth knowing

- Every child process that handles untrusted content (builder, reviewer, hunter round, dev server) receives only OS
  basics plus `[security] env_passthrough`. Secrets loaded from `.env` never reach them unless listed there.
- The builder's allowed tools are in `src/agent_squad/builder/permissions.json`. No interpreters, `curl`, `cat`,
  `npx` or deploy CLIs; stock photos come through the `search_photos` / `save_photo` MCP tools, which keep the API
  key server-side and download only from `[builder] image_hosts`.
- The detector scans long texts in windows and flags anything it cannot fully scan (`oversized`) instead of passing it.
- Approvals: agents may only request known kinds; deal-related kinds are checked against the customer in scope; one
  pending request per kind and deal (database constraint).

## Reporting

Found something? Open a private security advisory on the GitHub repository rather than a public issue.
