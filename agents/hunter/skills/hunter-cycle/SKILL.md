---
name: hunter-cycle
description: Use when the Hunter must run a lead-search round for {{business}}. Picks niche and city from the history, gets candidates from the lead source, verifies each one with a dossier, saves them in the CRM and drafts the outreach.
version: 1.0.0
author: agent-squad
license: MIT
required_environment_variables: [SQUAD_HOME]
metadata:
  hermes:
    tags: [lead, prospecting, hunter]
    related_skills: [lead-dossier, web-search]
---

# Hunter cycle

## Overview

One round: choose a target, search candidates, verify each one, save them in the CRM and leave an outreach draft for
{{owner}}. You never contact the lead.

## Before you start

```bash
: "${SQUAD_HOME:?set SQUAD_HOME}"; test -d "{{workspace}}" || echo "workspace missing: {{workspace}}"
```

If either fails, stop and report. Do not pick another path.

The lead source runs as `uv run --project "$SQUAD_HOME" squad leads ...` (shortened to `squad leads` below).

## Procedure

### 1. Read the history

Call `active_directives`, `list_campaigns` and `list_deals` (stage `target`). See what was searched, what advanced
and what was dropped. A directive saying the hunter is paused, or naming a target, wins.

Done when: you know which niche and city combinations already ran, when, and which produced results.

### 2. Choose the target

Choose niche, city, state and window (`--days`, default 365; use 90 to 180 to catch newly opened companies). Follow
the SOUL: reuse what worked, explore something new every 3 rounds, do not repeat city and niche within 30 days. If
{{owner}} named a target in a directive, use theirs.

Translate the niche into codes with the lead source. Never guess a code:

```bash
squad leads cnae --query "<text>"
```

Done when: you have city, state, one or more codes from the lookup and the window, and can say in one sentence why
you chose this combination.

### 3. Register the campaign

Call `create_campaign` with `source` = "cnpj" (the lead source), `criteria` = the search arguments (e.g.
`{"cnae": ["8630504"], "days": 180}`, or `"opened_since"` if you use a date), `location` = "City/ST" and `lead_cap`
(start with 5, never above the configured cap). Keep the returned `id`.

If the CRM refuses (daily cap, hunter paused), stop and tell {{owner}}. Do not retry.

### 4. Search candidates

```bash
squad leads search --state <ST> --city "<city>" --cnae <c1,c2> --days <N> --limit <L>
```

The first search in a new state builds a cache (a few minutes, reads ~3 GB). Say so before running it, then wait.
The output is JSON with `candidates`. The `has_site` field comes as "unknown": step 5 resolves it.

If there are 0 candidates, report it and suggest widening the window or changing the niche. Do not open a new round
to make up for it.

Done when: you have the candidate list, or recorded that it came back empty.

### 5. Verify each candidate

For each candidate, use the `lead-dossier` skill with `suggested_slug` as the slug. It checks online presence with
web search, extracts social proof and validates the dossier against the schema. If a candidate already exists in the
CRM (`save_lead` answers "slug already exists"), skip it.

Done when: each candidate has a valid `{{workspace}}/leads/<slug>/dossier.json` (validator exit 0) or was dropped
with a reason.

### 6. Save in the CRM

Drop candidates with `verdict.score` below 4 (good site, no buying signal) and say so in the report. For the rest,
call `save_lead` with `campaign_id`, `slug`, `origin` (the candidate's), `next_action` = "approach (manual,
{{owner}})" and `next_action_at` = today plus 2 days, as YYYY-MM-DD.

The CRM reads the dossier from disk, so do not paste the JSON. If it refuses, read the reason and fix the cause (for
example a missing dossier, or page text that looks like instructions to an assistant), never work around it.

Done when: each kept lead is saved, or you know why the CRM refused.

### 7. Draft the outreach

For each saved lead, write `{{workspace}}/leads/<slug>/outreach.md`: 4 to 6 lines in {{language}}, the tone of
someone talking to the owner of a local business, quoting ONE specific fact from the dossier (the hook) and offering
to BUILD a free preview (it does not exist yet: never write that it is ready), no pressure and no price. End with a
line for whoever does not want more messages. Send nothing.

Done when: each saved lead has `outreach.md`.

### 8. Report

Short answer: round parameters (niche, city, window), how many candidates, how many saved, how many dropped and why,
the 3 best with score and hook, and the path `leads/<slug>/`. Remind that the approach is manual.

## Common Pitfalls

1. **Guessing a code.** Use `squad leads cnae --query`.
2. **Treating `has_site: unknown` as "no site".** Only the dossier resolves it.
3. **Counting the registry email as a site.** It is usually the accountant's.
4. **Opening a new campaign to make up for an empty round.** The daily cap exists on purpose.
5. **Pasting the dossier into `save_lead`.** The CRM reads the validated file from disk.
6. **Quoting a fact that is not in the dossier** in the outreach draft. This includes "there is no site": if the web
   search failed, write "I could not find a site", never "you have no site".
7. **Messaging the lead.** Never.
8. **Stopping when Firecrawl is not configured.** `web_search` may answer "Web tools are not configured". Use the
   `web-search` skill.

## Verification Checklist

- [ ] The niche and city choice was justified by the history or a directive
- [ ] The codes came from the lookup, not from memory
- [ ] `create_campaign` was called before `squad leads search`
- [ ] Every saved lead has a validated `dossier.json` and an `outreach.md`
- [ ] No fact in the draft is outside the dossier
- [ ] You contacted nobody
- [ ] The report says what was dropped and why
