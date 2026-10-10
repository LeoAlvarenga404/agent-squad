"""The Telegram bot against a FAKE Telegram and the throwaway database. One sequential scenario, like the original."""
from datetime import datetime

import pytest

from agent_squad import bot as b
from agent_squad import crm, db

pytestmark = pytest.mark.usefixtures("squad_db")
CHAT, OTHER = 111, 222


class FakeTelegram:
    def __init__(self):
        self.calls, self.queue, self.failures = [], [], 0

    def call(self, method, **p):
        if self.failures:
            self.failures -= 1
            raise OSError("network down (simulated)")
        self.calls.append((method, p))
        if method == "getUpdates":
            ups, self.queue = self.queue, []
            return [u for u in ups if u["update_id"] >= p["offset"]]
        return {}

    def sent(self, method="sendMessage"):
        return [p for m, p in self.calls if m == method]


def click(uid, data, sender=CHAT, chat=CHAT):
    return {"update_id": uid, "callback_query": {
        "id": f"cb{uid}", "from": {"id": sender}, "data": data,
        "message": {"message_id": 7, "chat": {"id": chat}, "text": "Approval #x"}}}


def text(uid, t, sender=CHAT, chat=CHAT):
    return {"update_id": uid, "message": {"from": {"id": sender}, "chat": {"id": chat}, "text": t}}


def status(id_):
    with db.connect() as c:
        return c.execute("select status from approvals where id=%s", (id_,)).fetchone()["status"]


def deal(slug, stage):
    with db.connect() as c:
        c.execute("insert into deals(slug, origin, next_action, next_action_at, stage) "
                  "values (%s, 'test', 'x', '2099-01-01', %s)", (slug, stage))


def test_bot(as_role, workspace):
    with db.connect() as c:
        db.put_param(c, "builder_mode", "worker")  # approving a preview queues a ticket; nothing gets spawned
    as_role("sales")
    deal("shop-a", "preview")
    deal("shop-c", "negotiation")
    deal("shop-d", "negotiation")
    ap = crm.request_approval("close_deal", "shop-a", "customer accepted 790")["id"]
    ap2 = crm.request_approval("off_range_price", "shop-a", "10% discount")["id"]
    as_role("human")  # the bot process always runs as the human

    today = datetime.now().date().isoformat()
    b._mark("bot_report_on", today)  # the schedule has its own check at the end: here nothing goes out on its own
    b._mark("bot_hunter_on", today)
    tg = FakeTelegram()
    rounds = []
    bot = b.Bot(tg, CHAT, hunter=lambda: rounds.append(1))

    # (i) notified ONCE, with both buttons
    bot.cycle(timeout=0)
    bot.cycle(timeout=0)
    env = tg.sent()
    assert len(env) == 2, env
    buttons = env[0]["reply_markup"]["inline_keyboard"][0]
    assert [x["callback_data"] for x in buttons] == [f"ap:{ap}:1", f"ap:{ap}:0"], buttons
    assert [x["text"] for x in buttons] == ["Approve", "Reject"]
    assert "close_deal" in env[0]["text"] and "shop-a" in env[0]["text"] and "790" in env[0]["text"]
    assert all(m["chat_id"] == CHAT for m in env)

    # (iii) wrong chat/user: ignored, nothing changes
    tg.queue = [click(1, f"ap:{ap}:1", sender=OTHER, chat=OTHER), click(2, f"ap:{ap}:1", sender=OTHER),
                click(3, f"ap:{ap}:1", chat=OTHER)]
    bot.cycle(timeout=0)
    assert status(ap) == "pending"
    assert len(tg.sent("answerCallbackQuery")) == 3
    assert all("text" not in p for p in tg.sent("answerCallbackQuery"))
    assert not tg.sent("editMessageText")

    # (ii) the right chat approves; an approved close_deal unlocks sales' move_stage
    with pytest.raises(ValueError):
        crm.move_stage("shop-a", "won", "briefing", "2099-01-01")
    tg.queue = [click(4, f"ap:{ap}:1")]
    bot.cycle(timeout=0)
    assert status(ap) == "approved"
    ed = tg.sent("editMessageText")
    assert len(ed) == 1 and "APPROVED" in ed[0]["text"] and ed[0]["reply_markup"] == {"inline_keyboard": []}, ed
    crm.move_stage("shop-a", "won", "collect briefing", "2099-01-01")

    # (iv) a second click (same or opposite) changes nothing and says so
    tg.queue = [click(5, f"ap:{ap}:1"), click(6, f"ap:{ap}:0")]
    bot.cycle(timeout=0)
    assert status(ap) == "approved"
    assert [p.get("text") for p in tg.sent("answerCallbackQuery")[-2:]] == ["Already decided"] * 2
    # rejecting another approval works
    tg.queue = [click(7, f"ap:{ap2}:0")]
    bot.cycle(timeout=0)
    assert status(ap2) == "rejected"

    # (v) preview: ONE notice and the flag turns true
    with db.connect() as c:
        c.execute("insert into tickets(slug, type, description, status, preview_url) "
                  "values ('shop-a', 'bug', 'x', 'preview', 'https://p.example/a')")
        c.execute("insert into tickets(slug, type, description, status) values ('shop-a', 'bug', 'no url', 'preview')")
    n0 = len(tg.sent())
    bot.cycle(timeout=0)
    bot.cycle(timeout=0)
    new = tg.sent()[n0:]
    assert len(new) == 1 and "Preview ready" in new[0]["text"] and "https://p.example/a" in new[0]["text"], new
    assert "reply_markup" not in new[0]
    with db.connect() as c:
        flags = [r["preview_notified"] for r in c.execute("select preview_notified from tickets order by id")]
    assert flags == [True, False]

    # (vi) /pending: the right chat gets an answer; another chat is ignored
    deal("shop-b", "negotiation")
    ap3 = crm.request_approval("off_range_price", "shop-b", "pending for the test")["id"]
    bot.cycle(timeout=0)  # notifies ap3
    n0 = len(tg.sent())
    tg.queue = [text(8, "/pending", sender=OTHER, chat=OTHER), text(9, "/pending")]
    bot.cycle(timeout=0)
    resp = tg.sent()[n0:]
    assert len(resp) == 1 and f"#{ap3}" in resp[0]["text"] and f"#{ap}" not in resp[0]["text"], resp

    # (vii) a network error does not kill the loop: one iteration fails, the next one works
    ap4 = crm.request_approval("off_range_price", "shop-c", "after the outage")["id"]
    tg.failures = 1
    n0 = len(tg.sent())
    b.log.disabled = True  # the expected traceback only clutters the output
    bot.run(max_cycles=2, error_wait=0, timeout=0)
    b.log.disabled = False
    assert len(tg.sent()[n0:]) == 1 and f"#{ap4}" in tg.sent()[n0]["text"]

    # no notified_at when sending fails: resent on the next cycle (nothing lost)
    ap5 = crm.request_approval("off_range_price", "shop-d", "resend")["id"]
    tg.failures = 1
    with pytest.raises(OSError):
        bot.notify_approvals()
    with db.connect() as c:
        assert c.execute("select notified_at from approvals where id=%s", (ap5,)).fetchone()["notified_at"] is None
    bot.notify_approvals()
    with db.connect() as c:
        assert c.execute("select notified_at from approvals where id=%s", (ap5,)).fetchone()["notified_at"]

    # (viii) news: the OK on a preview becomes a ticket and the news arrives only once
    deal("shop-e", "contacted")
    ap6 = crm.request_approval("preview", "shop-e", "optician, wants to see the site")["id"]
    tg.queue = [click(10, f"ap:{ap6}:1")]
    bot.cycle(timeout=0)
    n0 = len(tg.sent())
    bot.cycle(timeout=0)
    bot.cycle(timeout=0)
    new = [m["text"] for m in tg.sent()[n0:]]
    assert len(new) == 1 and "(briefing) created for shop-e" in new[0], new

    # (ix) new lead: waits for outreach.md and sends the ready text
    with db.connect() as c:
        db.log_event(c, "hunter", "save_lead", "shop-f", {"score": 7})
    n0 = len(tg.sent())
    bot.cycle(timeout=0)
    assert len(tg.sent()) == n0, "no outreach.md yet: waits"
    (workspace / "leads" / "shop-f").mkdir(parents=True)
    (workspace / "leads" / "shop-f" / "outreach.md").write_text("Oi! Vi a loja de vocês.", encoding="utf-8")
    bot.cycle(timeout=0)
    bot.cycle(timeout=0)
    new = [m["text"] for m in tg.sent()[n0:]]
    assert len(new) == 1 and "shop-f (score 7)" in new[0] and "Oi! Vi a loja" in new[0], new

    # (x) commands: /status, /target, /hunter off|on|now; another chat is ignored
    n0 = len(tg.sent())
    tg.queue = [text(11, "/status"), text(12, "/target opticians in Springfield"), text(13, "/hunter off"),
                text(14, "/target nothing", sender=OTHER, chat=OTHER)]
    bot.cycle(timeout=0)
    new = [m["text"] for m in tg.sent()[n0:]]
    assert len(new) == 3 and new[0].startswith("Squad at") and "Waiting for you" in new[0], new
    assert "Target saved" in new[1] and new[2] == "Hunter paused.", new
    with db.connect() as c:
        assert c.execute("select count(*) n from directives where role='hunter'").fetchone()["n"] == 1
    assert "paused" in bot.report() and "opticians in Springfield" in bot.report()

    # (xi) schedule: report once a day; a paused hunter does not run on its own, but runs with /hunter now
    monday = datetime(2026, 10, 12, 9, 45)  # after both times
    b._mark("bot_report_on", "x")
    b._mark("bot_hunter_on", "x")
    n0 = len(tg.sent())
    bot.cycle(timeout=0, now=monday)
    bot.cycle(timeout=0, now=monday)
    new = [m["text"] for m in tg.sent()[n0:]]
    assert len(new) == 1 and new[0].startswith("Good morning. Squad at") and rounds == [], (new, rounds)
    tg.queue = [text(15, "/hunter now")]
    bot.cycle(timeout=0, now=monday)
    assert rounds == [1] and "went out for a round" in tg.sent()[-1]["text"]
    crm.set_param("hunter_paused", "no")
    b._mark("bot_hunter_on", "x")
    bot.cycle(timeout=0, now=datetime(2026, 10, 17, 10, 0))  # Saturday: no hunting
    assert rounds == [1]
    bot.cycle(timeout=0, now=monday)  # weekday, active, with a target: hunts on its own
    assert rounds == [1, 1]
    with db.connect() as c:
        c.execute("update directives set active=false")
    b._mark("bot_hunter_on", "x")
    bot.cycle(timeout=0, now=monday)  # no target: does not hunt and asks for one
    assert rounds == [1, 1] and "no target" in tg.sent()[-1]["text"]
