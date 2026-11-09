"""Reminder behaviour: who gets pinged, when, and that it is sent once.

Run:  python -I tests/test_reminders.py
"""
import asyncio
import os
import sys
import tempfile
import time
import types
from pathlib import Path

proj = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["DB_PATH"] = str(tmp / "t.db")
os.environ["WOW_VERSION"] = "retail"
os.chdir(proj)
sys.path.insert(0, str(proj))

import bot as B
import db as dbmod

run = asyncio.run
failed = 0


def check(name, cond, extra=""):
    global failed
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if not cond else ""))
    failed += (not cond)


B.db = dbmod.DB(str(tmp / "t.db"))
db = B.db
G = 1
NOW = int(time.time())
sent = []


async def send(content=None, **kw):
    sent.append(content)
    return types.SimpleNamespace(id=1)


chan = types.SimpleNamespace(id=11, send=send, guild=None)
B.bot.get_channel = lambda cid: chan if cid == 11 else None


def event(title, minutes_away, channel=11):
    e = db.create_event(G, channel, title, "", NOW + minutes_away * 60, 1, 0, 0, 0)
    db.set_message_id(e, 100 + e)
    db.update_event(e, lock_shown=1)  # keep the lock logic out of these tests
    return e


def sign(e, uid, status):
    db.set_signup(e, uid, f"U{uid}", "Mage", "Fire", "dps", status)


def tick():
    sent.clear()
    run(B.reminder_loop.coro())
    return list(sent)


print("default lead time is 30 minutes")
far = event("Far away", 120)
sign(far, 1, "in")
check("raid 2 hours away: no reminder yet", tick() == [] and db.event(far)["reminded"] == 0)
db.update_event(far, start_ts=NOW + 20 * 60)
out = tick()
check("inside the window: reminder sent to the signed-up player", len(out) == 1 and "<@1>" in out[0] and "Far away" in out[0], out)
check("  and marked as sent", db.event(far)["reminded"] == 1)
check("second tick doesn't repeat it", tick() == [])

print("nobody signed up when the reminder time arrives (the edge case)")
e = event("Late sign-ups", 20)
check("empty raid inside the window: nothing sent", tick() == [])
check("  and the reminder is NOT used up", db.event(e)["reminded"] == 0)
sign(e, 2, "bench")
sign(e, 3, "absent")
check("only a bench and an absent signup: still nothing, still not used up", tick() == [] and db.event(e)["reminded"] == 0)
sign(e, 4, "in")
out = tick()
check("first real signup gets the reminder on the next tick", len(out) == 1 and "<@4>" in out[0], out)
check("  benched/absent people aren't pinged", "<@2>" not in out[0] and "<@3>" not in out[0])
check("  then it's marked as sent", db.event(e)["reminded"] == 1)
sign(e, 5, "in")
check("someone who signs up afterwards doesn't trigger a second reminder", tick() == [])

print("which statuses are reminded")
e2 = event("Mixed", 10)
for uid, st in ((10, "in"), (11, "late"), (12, "tentative"), (13, "bench"), (14, "absent")):
    sign(e2, uid, st)
out = tick()
body = out[0] if out else ""
check("in, late and tentative are pinged", all(f"<@{u}>" in body for u in (10, 11, 12)), body)
check("bench and absent are not", "<@13>" not in body and "<@14>" not in body)

print("per-server lead time")
db.set_setting(G, "reminder_minutes", 10)
e3 = event("Short lead", 25)
sign(e3, 20, "in")
check("25 min away with a 10 min lead: not due", tick() == [] and db.event(e3)["reminded"] == 0)
db.update_event(e3, start_ts=NOW + 5 * 60)
check("5 min away with a 10 min lead: due", len(tick()) == 1)
db.set_setting(G, "reminder_minutes", None)

print("edge cases")
started = event("Already started", -5)
sign(started, 30, "in")
check("a raid that has already started gets no reminder", tick() == [] and db.event(started)["reminded"] == 0)
closed = event("Closed", 10)
sign(closed, 31, "in")
db.set_closed(closed, True)
check("a raid closed for sign-ups still reminds the people already in it", len(tick()) == 1 and db.event(closed)["reminded"] == 1)
gone = event("Channel deleted", 10, channel=999)
sign(gone, 32, "in")
check("channel missing: no crash, marked so it doesn't retry every minute",
      tick() == [] and db.event(gone)["reminded"] == 1)
# moving the time re-arms the reminder
db.update_event(far, start_ts=NOW + 15 * 60, reminded=0)
sign(far, 40, "in")
out = tick()
check("after /raid edit moves the time (reminded reset) the reminder fires again", len(out) == 1 and "<@40>" in out[0], out)

print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
sys.exit(1 if failed else 0)
