# Before the first real customer

Everything in this repository was built and tested locally with **simulated** customers. This is what separates
"works with simulated customers" from "can talk to real people". Each item is a decision, a secret or an account of
yours.

## 1. Model and data retention
Customer conversations go through the LLM behind the sales and account agents. Pick a provider whose data retention
and training policy you accept for customer data, and tell customers they are talking to an assistant.
- [ ] Put the provider key in each customer-facing Hermes profile (`sales`, `account`).
- [ ] `squad hermes switch-model anthropic sales account` (presets in `[hermes.providers]`).
- [ ] Re-run the simulations (`python -m sim.run`) on the new model and compare with the previous results.
- [ ] The memory index does not need rebuilding: embeddings are local and independent of the chat model.

## 2. Messaging channel
- [ ] Use a dedicated number for the agents, never a personal one. Unofficial WhatsApp bridges can get the number restricted.
- [ ] Allow-list who can talk to the bot until you decide the policy for new contacts.
- [ ] **Bind the conversation to the customer in the gateway.** The CRM refuses cross-customer access when
      `SQUAD_SCOPE_SLUG` is set, and the guard plugin resolves the customer from the channel's `sender_id`. The value
      must come from the channel (sender number), never from the model or the message text. Without it, isolation
      between customers depends on the model.
- [ ] Separate gateway processes for the sales and account profiles.

## 3. Hosting customer sites
- [ ] Free hosting tiers are often non-commercial. Pick a paid plan or another provider for paying customers.
- [ ] Write a deploy adapter if you leave Vercel (`docs/adapters.md`); `deals.deploy_target` stores per-customer choices.

## 4. Business decisions (`examples/web-studio/catalog.md` is only an example)
- [ ] Your real catalog: prices by lead origin, discount rules, payment, plans, monthly changes included.
- [ ] Invoicing, acceptance (written summary in the conversation?), human support hours.
- [ ] Measure real cost per site with `tickets.human_hours`, `machine_minutes` and `cost_usd` from the first deliveries.

## 5. Security and privacy
- [ ] Tell customers the conversation is recorded and that they talk to a virtual assistant.
- [ ] Removal requests: `squad purge` handles expired leads; for a specific person delete the deal row and its lead folder.
- [ ] Secrets only in `.env` or a vault. The builder is denied reading `.env*`.
- [ ] Review `src/agent_squad/builder/permissions.json` after the first real build: the runner records every
      permission denial in `tickets.run_note`.
- [ ] Never expose port 5433 beyond localhost.

## 6. Isolation (docs/security.md)
- [ ] Run Hermes and the builder as an OS user that cannot read the repo's `.env`; keep `SQUAD_DSN` (admin) only
      in the operator's environment. Agents get `SQUAD_AGENT_DSN`.
- [ ] Change the database passwords: `alter role squad password '...'`, `alter role squad_agent password '...'`.
- [ ] Builder sandbox: macOS/Linux/WSL2 with `[builder] sandbox = "on"`. On native Windows run it in WSL2 or a container.
- [ ] `[hermes] admin_ids`: your own channel id, so only you can run Hermes slash commands.

## 7. Operations
- [ ] Keep `squad bot` running (approvals only reach your phone while it runs).
- [ ] Keep `squad worker` running if `builder_mode = worker`.
- [ ] Back up Postgres (`pg_dump`) before holding customer data.
- [ ] Watch the first customer closely with `squad dashboard --watch 20` open.
