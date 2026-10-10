# Telegram bot

`squad bot` is a deterministic bot (no LLM) that is the operator's single screen: approvals with buttons, CRM news,
a daily report and the hunter schedule.

- An agent calls `request_approval` -> the bot sends a message with **Approve** / **Reject** -> the click calls
  `crm.decide`, the same path as `squad approve|reject`.
- Tickets in `preview` get a single notice with the URL (publishing to production stays a manual step).
- News: ticket started, ticket created, new lead with its `outreach.md` ready to send, preview delivered, stage changed.
- Daily report at `report_time`; hunter round at `hunter_time` (weekdays only by default) when there is a target and
  the hunter is not paused.

## 1. Create the bot

1. In Telegram, open `@BotFather`, send `/newbot` and pick a name and username.
2. Keep the token. **Use a dedicated bot**: do not reuse the token of the Hermes gateway. Two pollers (`getUpdates`)
   on the same token fight each other and messages disappear for one of them.

## 2. Find the chat id

1. Send any message to the new bot.
2. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `message.chat.id` (a number; negative for groups).
3. This value is also the expected `from.id`: the bot only accepts clicks and commands where **both** match.

## 3. Configuration

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | token from BotFather |
| `TELEGRAM_CHAT_ID` | yes | the operator's chat id (the only one allowed to act) |
| `SQUAD_DSN` | no | Postgres DSN (default `postgresql://squad:squad_dev@127.0.0.1:5433/squad`) |

Put them in `.env` (never in a versioned file). Schedule knobs go in `squad.toml`:

```toml
[bot]
report_time = "08:30"          # daily report, HH:MM local time
hunter_time = "09:30"          # automatic hunter round, HH:MM local time
hunter_weekdays_only = true    # skip Saturday and Sunday

[hermes]
bin = "hermes"                 # optional; defaults to `hermes` on PATH
```

The same token and chat id power the `telegram` notify adapter (`[adapters] notify = "telegram"`).

## 4. Run

```
docker compose up -d
squad bot
```

The `getUpdates` offset lives in memory and the cursors live in the `params` table, so restarting is safe: nothing
is repeated or lost, and a decision only counts while the approval is still `pending`. Hunter round logs go to
`logs/hunter/`.

## Commands

| Command | What it does |
|---|---|
| `/status` | how things stand now |
| `/pending` | approvals waiting for you |
| `/target <niche and city>` | what the hunter looks for (a 30-day directive) |
| `/hunter on` / `off` | resume / pause the automatic round |
| `/hunter now` | run a round now (even when paused) |
| `/help` | list the commands |

## Test

```
uv run pytest tests/test_bot.py -q
```

Uses a throwaway database and a fake Telegram: never touches the real database or the real API.
