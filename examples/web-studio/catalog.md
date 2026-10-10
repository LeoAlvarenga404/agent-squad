# {{business}} catalog: websites for local businesses (EXAMPLE)

> This is an example catalog for a small web studio. Every price and rule below is illustrative: replace them with
> your own before talking to real customers, and point `[agents] catalog` in `squad.toml` at your file.

Extract of the playbook for the agents that talk to customers. It holds only what the customer may know. Margin,
hourly cost, acquisition cost and internal accounts never go here and must never be mentioned.

## What it is
A ready-made website for a local business (salon, clinic, garage, restaurant, studio). The customer sees a **ready
preview before paying**. Guarantee: "Don't like the preview? You pay nothing."

## Prices to quote (in BRL, example values)

The price depends on the lead's origin (the CRM `origin` field). Quote only after you know the origin.

| Deliverable | Origin `outbound-*`, `referral`, `organic` | Origin `paid-ads` |
|---|---|---|
| Single page (landing) | R$ 600 to R$ 900; up to R$ 1.800 with extra content | from R$ 1.200, up to R$ 1.800 |
| Site with 2 to 4 pages | R$ 1.200 to R$ 3.000 | R$ 1.200 to R$ 3.000 |
| Ongoing care (optional) | R$ 80 to R$ 250 per month | R$ 80 to R$ 250 per month |

- Start at the floor of the origin's range and only go up if the scope asks for more (more sections, a special form,
  a lot of content to handle). Scope is discussed before price.
- **Outside the range, or any amount you do not have here: do not quote.** Ask for human approval
  (`request_approval`, kind `off_range_price`) and tell the customer you will confirm.
- Ongoing care includes managed hosting, monitoring, a monthly report and up to the deal's
  `monthly_changes_included` small changes per month (copy, photo, price, opening hours). Beyond that it becomes a
  quote. Cancel any time, no lock-in. The site stays the customer's.

## Discounts
There is no discount without something in return. Only if the customer asks (never offer it first), and only as a
trade:

| In return | Maximum discount |
|---|---:|
| Less scope (fewer pages or sections) | the value of what was removed |
| 100% paid upfront | 5% |
| Written permission to use the project as a case study (name, screenshots, testimonial) | 5% |
| Flexible timeline (goes into the queue, no hard date) | 10% |

Never below the floor of the origin's range. Never "just this once". Never invent a "was X, now Y" price.
Line: "I can, but a discount always comes with something in return, otherwise it's unfair to people who paid full
price. I can lower it if we drop [X from the scope], if you pay upfront, or if I can use your site as a case study
later. Which one works for you?"

## Payment
- 50% down payment before starting; 50% after the customer approves the site and before go-live.
- You never confirm a payment or give payment details: {{owner}} does that. A deal is only won once the down
  payment clears, and {{owner}} confirms it (that is why closing needs human approval).
- Third-party costs are paid by the customer directly (e.g. the domain, around 15 per year).

## What is included
A complete page, copy in the owner's voice, local SEO, a button to the customer's chat, a contact form that reaches
their email, domain and accounts in the customer's name, deployment, 1 revision round at approval, a 30-day warranty
for bugs in what was delivered (fixed at no cost).

## What is NOT included (say it clearly, no fine print)
Logo (refer someone), social media management, ads, a promise of a Google ranking (nobody honest guarantees one),
custom systems or apps (a different line, after an assessment), changes of mind after approval (quoted separately).

## Timeline
A few days after receiving the material and copy. Content is what causes delays. Ask the customer for: 3 good photos
of the place or the work, the list of services (with prices, if they want to show them), a paragraph about the
business, logo if they have one, opening hours and their chat link.

## Access (non-negotiable rule)
Never ask for a password. Access by invitation, with minimal permissions. The domain and the accounts stay in the
customer's name. "I don't need your password, actually I'd rather not have it. You add me as a user on your account,
with only what I need, and remove me whenever you want."

## Points {{owner}} still has to define (while empty, do not answer: say you will confirm and escalate)
- Invoices: [TO DEFINE: what to tell the customer]
- Contract/acceptance format: [TO DEFINE: e.g. written summary in the conversation]
- Real monthly change limit of the plan: the deal's `monthly_changes_included` field in the CRM (default 3)
- Human support hours: [TO DEFINE]
