# Instance lifecycle, the builder queue and spend control

Golden rule: **no instance keeps what matters**. Everything that must last lives in Postgres (deal record, customer
memory, tickets, directives). That is why restarting any agent is cheap and safe, and context never has to grow.

## Who lives how long

| Instance | How it starts | When it dies or resets | Context |
|---|---|---|---|
| **Builder** (Claude Code) | A new `claude -p` **per ticket** | The run ends and the process dies. Never `--resume` | Always starts from zero: reads the ticket and the site repository |
| **Sales, account** (Hermes, in the gateway) | One session per customer conversation | Resets after **120 min idle** (without telling the customer) | When it comes back it rereads the deal and the customer memory in the CRM |
| **Hunter** (Hermes) | One round = one conversation | Resets after 60 min idle; in the terminal, use `/new` between rounds | Each round rereads the campaign history in the CRM |
| **Coordinator** (Claude Code) | You open the terminal | When you run `/clear` (nothing is lost) | Disposable: directives and dashboard are in the CRM |
| Helper processes (`squad bot`, `squad worker`, `squad dashboard`) | Stay up | No AI context: they cost no tokens | None |

Every conversational agent has automatic context **compression** on (at 50% of the window), as a safety net before
the reset. `squad hermes install` writes these settings into each profile.

## The builder queue (several people asking at once)

`start_ticket` **always accepts**: the ticket goes into the queue and starts right away if there is a free slot.
- **One site at a time per customer.** A second request from the same customer waits for the first to finish (both
  would touch the same repository). Different customers run in parallel.
- **At most `[limits] max_builders` builders at once** (default 2).
- **Order:** priority (set with `squad priority ticket ID N`), then arrival.
- **The next one starts on its own** when one finishes (the runner calls the dispatcher at the end, or the worker
  pulls it in `builder_mode = worker`). You can also force it with `squad dispatch`.
- **Stuck runner** (the PC slept, the process died): a run idle for longer than `builder_timeout_min` + 15 min is
  reopened on its own, so the queue never stays blocked.
- The customer-facing agent tells the customer "it's in the queue" with the position, without promising a timeline.

## Where the money goes, and how to cap it

| Control | Default | Where to change it |
|---|---|---|
| Cost cap per run | none | `[limits] builder_max_usd` |
| **Daily builder cap** | none (set one!) | `[limits] builder_max_usd_day`: once reached, tickets **wait in the queue** until the next day |
| Max time per run | 40 min | `[limits] builder_timeout_min` |
| Attempts per ticket | 3 (then cancelled) | `[limits] builder_max_attempts` |
| Concurrent builders | 2 | `[limits] max_builders` |
| Hunter rounds per day | 3 | `[limits] max_rounds_per_day` |
| Measured spend per ticket | automatic | `tickets.machine_minutes`, `tickets.cost_usd`; the dashboard shows them |

A single-page site built with a small model took a few minutes and well under a dollar (API equivalent) in our
tests. That is an example, not an average: measure your first customers before planning costs.

The chat model behind sales, account and hunter charges per conversation token: short sessions and compression keep
that low. Set a spend cap in the provider account too, and switch providers with
`squad hermes switch-model <provider> <profile...>`.

## What you do
Nothing, day to day. Just set `builder_max_usd_day` once you know how much you accept to spend per day.
