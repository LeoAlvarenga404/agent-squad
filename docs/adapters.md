# Adapters

The squad has four seams where deployments differ. Each one is a plain Python module picked in `squad.toml`:

```toml
[adapters]
deploy = "vercel"              # agent_squad.adapters.deploy.vercel
review = "codex"               # agent_squad.adapters.review.codex
notify = "telegram"            # agent_squad.adapters.notify.telegram
leads  = "cnpj"                # agent_squad.adapters.leads.cnpj
```

A short name resolves to `agent_squad.adapters.<kind>.<name>`. A dotted name (`my_company.deploy_netlify`) imports
any module on the Python path, so you can keep your adapter in your own package without forking.

The exact contract of each kind is in the docstring of the bundled implementation. In short:

| Kind | Used by | Provides |
|---|---|---|
| `deploy` | builder runner (code, never the model) | `deploy(site_dir, slug) -> preview URL` (preview only, publicly viewable, raises on failure) and `public_url(site_dir)` |
| `review` | builder runner | `review(...)` returning a verdict (`APPROVED` / `CHANGES`) and the review text, or `None` when it cannot review |
| `notify` | builder runner | `send(text) -> bool`; must never raise |
| `leads` | hunter agent | a CLI (`squad leads ...`) that prints lead candidates as JSON; the hunter records the round with `create_campaign(source, criteria, location, lead_cap)` |

Adapter settings live in a section named after the adapter, e.g. `[vercel] scope = "my-team"`.

## Adding one

1. Copy the bundled module of the same kind as a starting point.
2. Keep the same function names and return shapes.
3. Point `[adapters]` at it and run `uv run pytest`.

Things that are **not** adapters on purpose: the sales pipeline (stages and approval rules are the security model),
the CRM schema, and the guard. Change those in code, with tests.
