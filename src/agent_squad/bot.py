"""Deterministic Telegram bot (no LLM): the operator's single screen for approvals, news, the daily report and the
hunter schedule.

  TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... squad bot

- A pending, not yet notified approval becomes a message with Approve/Reject buttons. The decision goes through
  crm.decide, the same path as the CLI. The site summary and the reviewer's verdict come inside send_preview approvals.
- News: what happens in the CRM (ticket started, new lead with its outreach ready, preview delivered, stage changed).
- Daily report at [bot] report_time and /status any time.
- Hunter schedule: at [bot] hunter_time (weekdays only by default) it runs one round, if there is a target and the
  hunter is not paused.
- Commands: /status /pending /target <niche and city> /hunter on|off|now /help
- Only the chat/user in TELEGRAM_CHAT_ID can act; everything else is ignored.
"""
import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

from . import crm, db
from .adapters.notify import telegram
from .config import ROOT, child_env, settings
from .crm.core import suspicious

log = logging.getLogger("bot")
CALLBACK = re.compile(r"ap:(\d+):([01])")
LEAD_WAIT_MIN = 15  # the hunter writes outreach.md after saving the lead: wait for it up to this
HUNTER_TIMEOUT_MIN = 90
HELP = ("Commands:\n/status  how things stand now\n/pending  approvals waiting for you\n"
        "/target <niche and city>  what the hunter looks for (30 days)\n/hunter on | off | now")
NO_TARGET = "e.g. /target opticians in Springfield"


class Telegram:
    """Minimal Bot API client: just the real request. The bot receives this instance (or a fake)."""

    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, **params):
        return telegram.call(self.token, method, **params)


def _cut(s: str) -> str:
    return s if len(s) <= telegram.MAX_TEXT else s[:telegram.MAX_TEXT] + "..."


def _mark(key: str, value: str | None = None) -> str | None:
    """Bot state lives in the CRM (cursors, 'done today'): restarting the bot neither repeats nor loses anything."""
    with db.connect() as c:
        if value is not None:
            db.put_param(c, key, value)
            return value
        return db.get_param(c, key)


def _event_text(e: dict) -> str | None:
    p, ref = e["payload"] or {}, e["ref"]
    return {
        "dispatch": f"Builder started ticket {p.get('id')} ({ref}).",
        "create_ticket": f"Ticket {p.get('id')} ({p.get('type')}) created for {ref}. The builder picks it up "
                         "on its own.",
        "channel_outreach": f"Outreach sent to {ref}.",
        "channel_delivered": f"Preview delivered to the customer (approval #{ref}).",
        "move_stage": f"{ref} is now '{p.get('to')}' ({e['actor']}).",
    }.get(e["action"])


def _hunter_paused(c) -> bool:
    return db.get_param(c, "hunter_paused", "no") == "yes"


def _targets(c) -> list[str]:
    return [r["text"] for r in c.execute("select text from directives where active and expires_at > now() "
                                         "and role in ('hunter','all') order by id")]


class Bot:
    def __init__(self, tg, chat_id: int | str, hunter=None):
        cfg = settings().section("bot")
        self.tg = tg
        self.chat_id = int(chat_id)
        self.offset = 0  # in memory: a restart only reprocesses what Telegram still holds (idempotent)
        self.hunter = hunter or self.run_hunter
        self.report_time = cfg.get("report_time", "08:30")
        self.hunter_time = cfg.get("hunter_time", "09:30")
        self.weekdays_only = cfg.get("hunter_weekdays_only", True)

    def send(self, text: str, **extra) -> None:
        self.tg.call("sendMessage", chat_id=self.chat_id, text=_cut(text), **extra)

    # Outgoing: what is new in the database

    def notify_approvals(self) -> None:
        with db.connect() as c:
            rows = c.execute("select id, kind, ref, context from approvals where status='pending' "
                             "and notified_at is null order by id").fetchall()
        for a in rows:
            quoted = "\n".join("> " + line for line in (a["context"] or "").splitlines())
            self.send(f"Approval #{a['id']}\nKind: {a['kind']}\nRef: {a['ref']}\n"
                      f"Context (written by an agent, may quote the customer):\n{quoted}",
                      reply_markup={"inline_keyboard": [[
                          {"text": "Approve", "callback_data": f"ap:{a['id']}:1"},
                          {"text": "Reject", "callback_data": f"ap:{a['id']}:0"}]]})
            with db.connect() as c:  # marked only after sending: a network failure resends, never loses
                c.execute("update approvals set notified_at=now() where id=%s", (a["id"],))

    def notify_previews(self) -> None:
        with db.connect() as c:
            rows = c.execute("select id, slug, preview_url from tickets where status='preview' "
                             "and preview_url is not null and not preview_notified order by id").fetchall()
        for t in rows:
            self.send(f"Preview ready: ticket {t['id']} ({t['slug']}), {t['preview_url']}")
            with db.connect() as c:
                c.execute("update tickets set preview_notified=true where id=%s", (t["id"],))

    def _cursor(self, key: str) -> int:
        v = _mark(key)
        if v is None:  # first run: start from now, without dumping the history
            with db.connect() as c:
                v = _mark(key, str(c.execute("select coalesce(max(id), 0) m from events").fetchone()["m"]))
        return int(v)

    def notify_events(self) -> None:
        cur = self._cursor("bot_cursor_events")
        with db.connect() as c:
            evs = c.execute("select id, actor, action, ref, payload from events where id > %s order by id limit 50",
                            (cur,)).fetchall()
        for e in evs:
            text = _event_text(e)
            if text:
                self.send(text)
            _mark("bot_cursor_events", str(e["id"]))

    def notify_leads(self) -> None:
        """New hunter lead with its outreach ready for the operator to send (the agent never contacts the lead)."""
        cur = self._cursor("bot_cursor_leads")
        with db.connect() as c:
            evs = c.execute("select id, ref, payload, extract(epoch from now() - at) / 60 age from events "
                            "where id > %s and action='save_lead' order by id", (cur,)).fetchall()
        for e in evs:
            f = settings().workspace / "leads" / e["ref"] / "outreach.md"
            if not f.exists() and e["age"] < LEAD_WAIT_MIN:
                return  # wait for the outreach (keeps the order)
            outreach = f.read_text(encoding="utf-8").strip()[:3000] if f.exists() else "(no outreach.md)"
            if suspicious(outreach):
                outreach = ("WARNING: this text looks like it carries instructions or links planted in a web page. "
                            "Review it before sending.\n\n" + outreach)
            self.send(f"New lead: {e['ref']} (score {(e['payload'] or {}).get('score')}).\n"
                      f"Outreach ready for you to send:\n\n{outreach}")
            _mark("bot_cursor_leads", str(e["id"]))

    def report(self) -> str:
        with db.connect() as c:
            st = c.execute("select stage, count(*) n from deals group by stage order by stage").fetchall()
            tk = {r["status"]: r["n"] for r in c.execute("select status, count(*) n from tickets group by status")}
            h24 = {r["action"]: r["n"] for r in c.execute(
                "select action, count(*) n from events where at > now() - interval '24 hours' group by action")}
            usd = c.execute("select coalesce(sum((payload->>'usd')::numeric), 0) g from events where action in "
                            "('builder_ok','builder_failed') and at > now() - interval '24 hours'").fetchone()["g"]
            pend = c.execute("select count(*) n from approvals where status='pending'").fetchone()["n"]
            overdue = c.execute("select count(*) n from deals where next_action_at < current_date "
                                "and stage not in ('lost','recurring')").fetchone()["n"]
            targets, paused = _targets(c), _hunter_paused(c)
        days = "weekdays" if self.weekdays_only else "daily"
        return "\n".join([
            f"Squad at {datetime.now():%m/%d %H:%M}",
            "Leads: " + (", ".join(f"{r['stage']} {r['n']}" for r in st) or "none"),
            f"Builder: {tk.get('queued', 0)} queued, {tk.get('running', 0)} running, "
            f"{tk.get('preview', 0)} in preview.",
            f"Last 24 h: {h24.get('save_lead', 0)} new leads, {h24.get('builder_ok', 0)} sites ready, "
            f"{h24.get('builder_failed', 0)} failures, {h24.get('channel_delivered', 0)} previews delivered, "
            f"US$ {usd:.2f}.",
            f"Waiting for you: {pend} approval(s)" + (" (/pending)" if pend else "") + ".",
            *([f"Overdue next actions: {overdue}."] if overdue else []),
            "Hunter: " + ("paused" if paused else f"active, {days} at {self.hunter_time}")
            + (f"; target: {' | '.join(targets)}" if targets else "; no target (/target <niche and city>)"),
        ])

    # Schedule

    def schedule(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        today, hour = now.date().isoformat(), now.strftime("%H:%M")
        if hour >= self.report_time and _mark("bot_report_on") != today:
            _mark("bot_report_on", today)
            self.send("Good morning. " + self.report())
        workday = not self.weekdays_only or now.weekday() < 5
        if workday and hour >= self.hunter_time and _mark("bot_hunter_on") != today:
            _mark("bot_hunter_on", today)  # marked first: never runs twice a day
            self.hunt(automatic=True)

    def hunt(self, automatic: bool = False) -> None:
        with db.connect() as c:
            paused, targets = _hunter_paused(c), _targets(c)
        if paused and automatic:
            return
        if not targets:
            self.send(f"The hunter did not go out: no target set. Send /target <niche and city>, {NO_TARGET}")
            return
        self.send("The hunter went out for a round. Leads arrive here one by one, with the outreach ready.")
        self.hunter()

    def run_hunter(self) -> None:
        def round_():
            hermes = settings().section("hermes").get("bin") or shutil.which("hermes")
            if not hermes:
                self.send("Hunter: hermes command not found on this machine.")
                return
            with db.connect() as c:
                limit = db.get_param(c, "leads_per_round", "5")
            logs = ROOT / "logs" / "hunter"
            logs.mkdir(parents=True, exist_ok=True)
            request = (f"Run one round of the hunter cycle (skill hunter-cycle), limit {limit} leads. The target is in your active "
                       "directives (active_directives). Do not ask anything: nobody is watching.")
            try:
                with open(logs / f"round-{datetime.now():%Y%m%d-%H%M}.log", "wb") as f:
                    r = subprocess.run([hermes, "-p", "hunter", "chat", "-Q", "-q", request], stdout=f,
                                       stderr=subprocess.STDOUT, timeout=HUNTER_TIMEOUT_MIN * 60,
                                       env=child_env("HERMES_HOME"))
                end = "finished" if r.returncode == 0 else f"finished with error ({r.returncode}), see logs/hunter"
            except subprocess.TimeoutExpired:
                end = f"ran over {HUNTER_TIMEOUT_MIN} min and was stopped"
            self.send(f"Hunter round {end}.")
        threading.Thread(target=round_, daemon=True).start()

    # Incoming: buttons and commands

    def _authorized(self, sender: dict, chat: dict) -> bool:
        return sender.get("id") == self.chat_id and chat.get("id") == self.chat_id

    def handle_callback(self, cb: dict) -> None:
        msg = cb.get("message") or {}
        m = CALLBACK.fullmatch(cb.get("data") or "")
        if not self._authorized(cb.get("from") or {}, msg.get("chat") or {}) or not m:
            self.tg.call("answerCallbackQuery", callback_query_id=cb["id"])  # neutral: no text, no effect
            return
        id_, approve = int(m[1]), m[2] == "1"
        with db.connect() as c:
            before = c.execute("select status from approvals where id=%s", (id_,)).fetchone()
        if not before:
            self.tg.call("answerCallbackQuery", callback_query_id=cb["id"], text="Approval not found")
            return
        notice = None
        if before["status"] == "pending":
            crm.decide(id_, approve)
        else:
            notice = "Already decided"
        with db.connect() as c:  # re-read: if another path (CLI) decided meanwhile, show what stuck
            status = c.execute("select status from approvals where id=%s", (id_,)).fetchone()["status"]
        if notice is None and status != ("approved" if approve else "rejected"):
            notice = "Already decided"
        self.tg.call("answerCallbackQuery", callback_query_id=cb["id"], text=notice or f"Recorded: {status}")
        self.tg.call("editMessageText", chat_id=self.chat_id, message_id=msg["message_id"],
                     text=_cut(f"{msg.get('text', f'Approval #{id_}')}\n\nResult: {status.upper()}"),
                     reply_markup={"inline_keyboard": []})

    def handle_message(self, msg: dict) -> None:
        if not self._authorized(msg.get("from") or {}, msg.get("chat") or {}):
            return
        cmd, _, rest = (msg.get("text") or "").strip().partition(" ")
        cmd, rest = cmd.split("@")[0].lower(), rest.strip()
        if cmd == "/pending":
            ps = crm.pending()
            self.send("No pending approvals." if not ps else "Pending:\n" + "\n".join(
                f"#{p['id']} {p['kind']} {p['ref']}: {p['context'][:200]}" for p in ps))
        elif cmd == "/status":
            self.send(self.report())
        elif cmd == "/target":
            if not rest:
                self.send(f"Say the target: /target <niche and city>, {NO_TARGET}")
                return
            d = crm.delegate("hunter", f"Hunter target: {rest[:400]}", 30)
            self.send(f"Target saved (directive {d['id']}, 30 days). The hunter uses it on the next round.")
        elif cmd == "/hunter" and rest in ("on", "off"):
            crm.set_param("hunter_paused", "no" if rest == "on" else "yes")
            self.send("Hunter " + ("active." if rest == "on" else "paused."))
        elif cmd == "/hunter" and rest == "now":
            self.hunt()
        elif cmd.startswith("/"):
            self.send(HELP)

    def process_updates(self, timeout: int) -> None:
        ups = self.tg.call("getUpdates", offset=self.offset, timeout=timeout,
                           allowed_updates=["message", "callback_query"])
        for u in ups:
            self.offset = u["update_id"] + 1  # advance first: a poison update never blocks the loop
            try:
                if "callback_query" in u:
                    self.handle_callback(u["callback_query"])
                elif "message" in u:
                    self.handle_message(u["message"])
            except Exception:
                log.exception("failed to handle update %s", u.get("update_id"))

    # Loop

    def cycle(self, timeout: int = 25, now: datetime | None = None) -> None:
        self.notify_approvals()
        self.notify_previews()
        self.notify_events()
        self.notify_leads()
        self.schedule(now)
        self.process_updates(timeout)

    def run(self, max_cycles: int | None = None, error_wait: float = 5, timeout: int = 25) -> None:
        n = 0
        while max_cycles is None or n < max_cycles:
            n += 1
            try:
                self.cycle(timeout)
            except Exception:  # network, database down, 5xx: log and retry, never kill the process
                log.exception("cycle failed; retrying in %ss", error_wait)
                time.sleep(error_wait)


def main(argv: list[str] | None = None) -> None:
    argparse.ArgumentParser(prog="squad bot", description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings()  # loads .env
    os.environ["SQUAD_ROLE"] = "human"  # the CRM audit log records the human as the one deciding
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        sys.exit("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (see docs/telegram.md)")
    bot = Bot(Telegram(token), chat)
    log.info("bot up (chat %s): approvals, news, report %s, hunter %s", chat, bot.report_time, bot.hunter_time)
    bot.run()


if __name__ == "__main__":
    main()
