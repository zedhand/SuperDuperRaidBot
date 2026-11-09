"""Offline tests for v1.10 ("faster for players"): one-click sign-up, calendar file, private reminders,
"always in" for repeating raids, and your place in line on the bench.

Run:  python -I tests/test_v110.py        (or python tests/run_all.py for every suite)
"""
import asyncio
import os
import re
import sys
import tempfile
import time
import types
from datetime import datetime, timezone
from pathlib import Path

proj = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["DB_PATH"] = str(tmp / "t.db")
os.environ["WOW_VERSION"] = "retail"
os.chdir(proj)
sys.path.insert(0, str(proj))

import discord  # noqa: E402
from discord import app_commands  # noqa: E402
import bot as B  # noqa: E402
import checks  # noqa: E402
import db as dbmod  # noqa: E402
import embeds  # noqa: E402

run = asyncio.run
passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


B.db = dbmod.DB(str(tmp / "t.db"))
db = B.db
G = 1
NOW = int(time.time())
HOUR = 3600
DAY = 86400
http_error = discord.HTTPException(types.SimpleNamespace(status=403, reason="x"), "Cannot send messages to this user")


# ------------------------------------------------------------ fakes
class FakeResp:
    def __init__(self): self.sent = []; self.edited = []; self.deferred = False
    async def send_message(self, *a, **k): self.sent.append((a, k))
    async def edit_message(self, **k): self.edited.append(k)
    async def defer(self, **k): self.deferred = True
    def is_done(self): return bool(self.sent) or bool(self.edited) or self.deferred


class FakeFollowup:
    def __init__(self): self.sent = []
    async def send(self, *a, **k): self.sent.append((a, k))


def member(uid, roles=(), admin=False):
    perms = types.SimpleNamespace(manage_events=False, administrator=admin, manage_guild=admin)
    return types.SimpleNamespace(guild_permissions=perms, roles=[types.SimpleNamespace(id=r) for r in roles],
                                 id=uid, bot=False, mention=f"<@{uid}>", display_name=f"User{uid}")


class FakeChannel:
    def __init__(self, cid):
        self.id, self.category_id, self.mention, self.name = cid, None, f"<#{cid}>", f"chan{cid}"
        self.sent, self.edits, self.fail_send = [], 0, None
        self.guild = types.SimpleNamespace(id=G, me=object(), get_role=lambda r: None,
                                           get_member=lambda u: types.SimpleNamespace(id=u))

    async def send(self, *a, **k):
        if self.fail_send:
            raise self.fail_send
        self.sent.append((a, k))
        return types.SimpleNamespace(id=len(self.sent) + 700, jump_url="http://x")

    def permissions_for(self, me):
        return types.SimpleNamespace(view_channel=True, send_messages=True, embed_links=True,
                                     read_message_history=True, mention_everyone=False)

    def get_partial_message(self, mid):
        chan = self

        class Msg:
            async def edit(self, **kw):
                chan.edits += 1
        return Msg()


class FakeUser:
    """What bot.fetch_user returns: records the DMs it is asked to send."""
    def __init__(self, uid, dms, closed):
        self.uid, self.dms, self.closed = uid, dms, closed

    async def send(self, text):
        if self.uid in self.closed:
            raise http_error
        self.dms.append((self.uid, text))


DMS, CLOSED = [], set()


async def fake_fetch_user(uid):
    return FakeUser(uid, DMS, CLOSED)


CH = {11: FakeChannel(11), 12: FakeChannel(12)}
B.bot.get_channel = lambda cid: CH.get(cid)
B.bot.fetch_user = fake_fetch_user


def inter(user, ch=None, message_id=None):
    ch = ch or CH[11]
    msg = types.SimpleNamespace(id=message_id, jump_url=f"https://discord.com/channels/1/{ch.id}/{message_id}") if message_id else None
    return types.SimpleNamespace(user=user, guild_id=G, guild=ch.guild, channel=ch, channel_id=ch.id,
                                 response=FakeResp(), followup=FakeFollowup(), command=None, message=msg,
                                 namespace=types.SimpleNamespace())


def reply(i):
    msgs = i.response.sent or i.followup.sent
    return msgs[0][0][0] if msgs and msgs[0][0] else ""


def new_event(title="Raid", start=None, channel=11, **upd):
    e = db.create_event(G, channel, title, "", start or NOW + DAY, 1, 0, 0, 0)
    db.set_message_id(e, 1000 + e)
    db.update_event(e, lock_shown=1, **upd)
    return e


def wipe():
    for t in ("signups", "events", "series_signups", "characters"):
        db.conn.execute(f"DELETE FROM {t}")
    db.conn.commit()


def save_char(uid, name, cls, spec, main=False):
    db.upsert_character(uid, name, cls, spec, B.wd.role_for(cls, spec), main)


admin = member(1, admin=True)

# ------------------------------------------------------------ F7 calendar file
print("F7 calendar file (.ics)")
wipe()
ev = new_event("Naxx, \"Wing 1\"; bring flasks", start=1794700800)  # 2026-11-15 00:00 UTC
db.update_event(ev, description="Line one\nLine two, with a comma")
row = db.event(ev)
ics = B.build_ics(row, "https://discord.com/channels/1/11/1000", now=1794600000)
lines = ics.split("\r\n")
check("is a calendar file with CRLF line endings that ends with one", lines[0] == "BEGIN:VCALENDAR" and ics.endswith("END:VCALENDAR\r\n"))
unfolded = ics.replace("\r\n ", "").split("\r\n")
check("has the required calendar fields", all(x in unfolded for x in ("VERSION:2.0", "BEGIN:VEVENT", "END:VEVENT")) and any(x.startswith("PRODID:") for x in unfolded))
start = datetime.fromtimestamp(1794700800, timezone.utc)
check("starts at the raid's time in UTC", f"DTSTART:{start:%Y%m%dT%H%M%SZ}" in unfolded, [x for x in unfolded if x.startswith("DT")])
check("ends 3 hours later", "DTEND:20261115T030000Z" in unfolded)
check("uses a stable id so re-importing updates the entry", f"UID:raid-{ev}@superduperraidbot" in unfolded
      and f"UID:raid-{ev}@superduperraidbot" in B.build_ics(row, "x", now=1).replace("\r\n ", "").split("\r\n"))


def unescape(v):
    return re.sub(r"\\(.)", lambda m: "\n" if m[1] == "n" else m[1], v)


summary = next(x for x in unfolded if x.startswith("SUMMARY:"))[8:]
check("commas, semicolons and quotes in the title survive", unescape(summary) == row["title"], summary)
desc = next(x for x in unfolded if x.startswith("DESCRIPTION:"))[12:]
check("line breaks in the description survive and the link is added",
      unescape(desc) == "Line one\nLine two, with a comma\nhttps://discord.com/channels/1/11/1000", desc)
check("and they are escaped the way calendar files require (\\, \\; and \\n)",
      "\\," in summary and "\\;" in summary and "\\n" in desc and "\n" not in desc, (summary, desc))
check("a backslash in the title is escaped too", B._ics_escape("a\\b") == "a\\\\b")
db.update_event(ev, title="Ünïcödé raid ✨ " + "x" * 200, description="é" * 300)
ics = B.build_ics(db.event(ev), "https://discord.com/channels/1/11/1000")
raw_lines = ics.split("\r\n")
check("no line is over 75 bytes", all(len(x.encode("utf-8")) <= 75 for x in raw_lines), max(len(x.encode()) for x in raw_lines))
check("long and non-English text folds without breaking a character and unfolds to the original",
      unescape(next(x for x in ics.replace("\r\n ", "").split("\r\n") if x.startswith("SUMMARY:"))[8:]) == db.event(ev)["title"])
ics.encode("utf-8").decode("utf-8")

print("F7 calendar button")
ev = new_event("Calendar raid", start=NOW + DAY)
i = inter(admin, message_id=1000 + ev)
run(B.RaidView().calendar.callback(i))
sent = i.response.sent[0][1]
f = sent.get("file")
check("sends a private .ics attachment", sent.get("ephemeral") is True and f is not None and f.filename == f"raid-{ev}.ics")
body = f.fp.read().decode("utf-8")
check("with the right raid inside, including a link back to the post",
      "SUMMARY:Calendar raid" in body and f"https://discord.com/channels/1/11/{1000 + ev}" in body)
i = inter(admin, message_id=987654)
run(B.RaidView().calendar.callback(i))
check("says so if the raid no longer exists", "no longer exists" in reply(i))
db.set_closed(ev, True)
i = inter(admin, message_id=1000 + ev)
run(B.RaidView().calendar.callback(i))
check("still works when sign-ups are closed", i.response.sent[0][1].get("file") is not None)
check("the button is part of the raid post's buttons", "raid:ics" in [c.custom_id for c in B.RaidView().children])
check("a post still fits Discord's 5-buttons-per-row (it wraps to a second row)", len(B.RaidView().children) == 6)

# ------------------------------------------------------------ F6 one-click sign up
print("F6 one-click sign-up")
wipe()
ev = new_event("Quick raid", start=NOW + DAY)
view = B.RaidView()
bob = member(80)
check("nobody saved or signed up yet: no quick button", B.quick_character(80) is None)
i = inter(bob, message_id=1000 + ev)
run(view.signup.callback(i))
kids = i.response.sent[0][1]["view"].children
check("so the prompt is the usual class picker", not any(isinstance(c, B.QuickSignupButton) for c in kids))

save_char(80, "Bobmain", "Mage", "Fire", main=True)
save_char(80, "Bobalt", "Warrior", "Protection")
q = B.quick_character(80)
check("a saved main is used the first time", q and q["name"] == "Bobmain")
i = inter(bob, message_id=1000 + ev)
run(view.signup.callback(i))
kids = i.response.sent[0][1]["view"].children
check("the top button is the quick sign-up", isinstance(kids[0], B.QuickSignupButton) and "Bobmain" in kids[0].label, [c for c in kids])
check("and the saved-character and class pickers are still there", len(kids) == 3)
run(kids[0].callback(i))
signup = db.signup(ev, 80)
check("one click signs them up as that character", signup and signup["status"] == "in" and signup["char_name"] == "Bobmain")
check("the reply confirms it", "Signed up as **Bobmain**" in i.response.edited[0]["content"])

ev2 = new_event("Next raid", start=NOW + 2 * DAY)
i = inter(bob, message_id=1000 + ev2)
run(B.finish_signup(i, ev2, "Bobalt", "Warrior", "Protection", "tank"))
check("signing up as another character is remembered as 'last used'", B.quick_character(80)["name"] == "Bobalt")
i = inter(bob, message_id=1000 + ev)
run(view.signup.callback(i))
check("so next time the quick button offers it (even for an older raid)", "Bobalt" in i.response.sent[0][1]["view"].children[0].label)
check("the prompt says what they are currently signed up as", "currently **in** as **Bobmain**" in i.response.sent[0][0][0], i.response.sent[0][0][0])
db.set_signup(ev, 80, None, None, None, None, "absent")
check("an absence on an older raid doesn't change who is suggested", B.quick_character(80)["name"] == "Bobalt")
db.set_signup(ev2, 80, None, None, None, None, "absent")
check("marking the most recent sign-up absent falls back to the character before it, never to nothing",
      B.quick_character(80) is not None and B.quick_character(80)["name"] == "Bobmain")
db.set_signup(ev, 81, "User81", "Rogue", "Subtlety", "dps", "in")
check("a walk-in who picked a class and spec is remembered too", B.quick_character(81)["spec"] == "Subtlety")

# ------------------------------------------------------------ F10 place in line
print("F10 bench queue position")
wipe()
ev = new_event("Queue raid", start=NOW + DAY)
db.update_event(ev, cap_tank=1, cap_dps=1)
db.set_signup(ev, 1, "Tanky", "Warrior", "Protection", "tank", "in")
db.set_signup(ev, 2, "TankB", "Warrior", "Protection", "tank", "bench", "in")
db.set_signup(ev, 3, "TankC", "Paladin", "Protection", "tank", "bench", "late")
db.set_signup(ev, 4, "Volunteer", "Mage", "Fire", "dps", "bench")           # chose the bench themselves
db.set_signup(ev, 5, "DpsA", "Mage", "Fire", "dps", "in")
db.set_signup(ev, 6, "DpsB", "Rogue", "Subtlety", "dps", "bench", "in")
check("first benched tank is #1", db.bench_position(ev, 2) == 1)
check("second benched tank is #2 (even though they asked for 'late')", db.bench_position(ev, 3) == 2)
check("the line is per role: first benched DPS is #1", db.bench_position(ev, 6) == 1)
check("someone who chose the bench has no place in line", db.bench_position(ev, 4) is None)
check("someone who is in has none either", db.bench_position(ev, 1) is None and db.bench_position(ev, 99) is None)
check("queue_note words it for a player", B.queue_note(ev, 3) == " You're #2 in line for a tank slot.", B.queue_note(ev, 3))
check("and says DPS properly", B.queue_note(ev, 6) == " You're #1 in line for a dps slot.", B.queue_note(ev, 6))
check("no note for people not in line", B.queue_note(ev, 4) == "" and B.queue_note(ev, 1) == "")
emb = embeds.build_embed(db.event(ev), db.signups(ev), False)
bench_field = next(f.value for f in emb.fields if "Bench" in f.value)
check("the post shows each person's place", "TankB** (#1 tank)" in bench_field and "TankC** (#2 tank)" in bench_field
      and "DpsB** (#1 dps)" in bench_field, bench_field)
check("but not for someone who volunteered", "Volunteer**" in bench_field and "Volunteer** (#" not in bench_field, bench_field)
db.remove_signup(ev, 1)
run(B.promote_bench(ev))
check("when the tank leaves, the first in line is promoted", db.signup(ev, 2)["status"] == "in")
check("and the next person moves up to #1", db.bench_position(ev, 3) == 1)
i = inter(member(7), message_id=1000 + ev)
db.update_event(ev, cap_tank=1)
run(B.finish_signup(i, ev, "Latecomer", "Warrior", "Protection", "tank"))
text = i.response.edited[0]["content"]
check("a new sign-up into a full role is told their place", "Tank slots are full" in text and "#2 in line for a tank slot" in text, text)
db.update_event(ev, cap_dps=1)
i = inter(member(8), message_id=1000 + ev)
run(B.finish_signup(i, ev, "Dpsguy", "Hunter", "Marksmanship", "dps"))
check("the DPS message says DPS (not 'DP')", "DPS slots are full" in i.response.edited[0]["content"] and "DP slots" not in i.response.edited[0]["content"], i.response.edited[0]["content"])

# ------------------------------------------------------------ F8 private reminders
print("F8 /remindme")
wipe()
for c in CH.values():
    c.sent.clear()
    c.fail_send = None
B._tries.clear()
B._dm_sent.clear()
DMS.clear()
CLOSED.clear()
i = inter(member(90))
run(B.remindme.callback(i, True))
check("turning it on is saved per person", B.wants_dm(G, 90) and not B.wants_dm(G, 91) and "privately" in reply(i), reply(i))
i = inter(member(90))
run(B.remindme.callback(i, False))
check("turning it off removes it", not B.wants_dm(G, 90) and "pinged in the channel" in reply(i))
db.set_setting(G, "dm_remind@90", "1")
db.set_setting(G, "dm_remind@92", "1")


def tick():
    run(B.reminder_loop.coro())


rem = new_event("Reminder raid", start=NOW + 10 * 60)
for uid, name in ((90, "Dmed"), (91, "Pinged"), (92, "ClosedDms")):
    db.set_signup(rem, uid, name, "Mage", "Fire", "dps", "in")
CLOSED.add(92)
tick()
check("an opted-in person gets a DM with the raid and a link", any(u == 90 and "Reminder raid" in t and f"/channels/1/11/{1000 + rem}" in t for u, t in DMS), DMS)
chan_text = CH[11].sent[0][0][0] if CH[11].sent else ""
check("and is not pinged in the channel", "<@90>" not in chan_text and "<@91>" in chan_text, chan_text)
check("someone whose DMs are closed is pinged in the channel instead", "<@92>" in chan_text)
check("the reminder is marked sent", db.event(rem)["reminded"] == 1)
tick()
check("and never repeats", len(DMS) == 1 and len(CH[11].sent) == 1)

print("F8 retries don't send the DM twice")
DMS.clear()
B._dm_sent.clear()
CH[11].sent.clear()
rem2 = new_event("Retry raid", start=NOW + 10 * 60)
db.set_signup(rem2, 90, "Dmed", "Mage", "Fire", "dps", "in")
db.set_signup(rem2, 91, "Pinged", "Mage", "Fire", "dps", "in")
CH[11].fail_send = RuntimeError("channel hiccup")
tick()
check("the DM went out even though the channel message failed", len(DMS) == 1 and db.event(rem2)["reminded"] == 0)
CH[11].fail_send = None
tick()
check("the retry posts the channel message but does not DM again", len(DMS) == 1 and len(CH[11].sent) == 1 and db.event(rem2)["reminded"] == 1, (DMS, CH[11].sent))

print("F8 everyone opted in: no channel message at all")
DMS.clear()
CH[11].sent.clear()
rem3 = new_event("All DM raid", start=NOW + 10 * 60)
db.set_signup(rem3, 90, "Dmed", "Mage", "Fire", "dps", "in")
tick()
check("only a DM, and the reminder is done", len(DMS) == 1 and CH[11].sent == [] and db.event(rem3)["reminded"] == 1)

# ------------------------------------------------------------ F9 always in
print("F9 series: repeating raids carry a series id")
wipe()
for c in CH.values():
    c.sent.clear()
    c.edits = 0
guild = CH[11].guild
eid, msg, notes = run(B.post_event(CH[11], guild, 1, "Weekly run", "", NOW + 3600 * 30, 0, 0, 0, repeat_days=7))
check("a repeating raid starts its own series", db.event(eid)["series_id"] == eid)
eid2, msg, notes = run(B.post_event(CH[11], guild, 1, "One off", "", NOW + 3600 * 30, 0, 0, 0))
check("a one-off raid has no series", db.event(eid2)["series_id"] == 0)
i = inter(admin)
run(B.raid_repeat.callback(i, eid2, app_commands.Choice(name="Every week", value=7)))
check("turning repeat on later starts a series", db.event(eid2)["series_id"] == eid2)

print("F9 /always join")
alice = member(60)
save_char(60, "Alicemain", "Mage", "Fire", main=True)
save_char(60, "Alicetank", "Warrior", "Protection")
db.update_event(eid, message_id=1500, lock_shown=1)
i = inter(alice)
run(B.always_join.callback(i, eid))
m = db.series_members(eid)
check("joins the series with their main", len(m) == 1 and m[0]["char_name"] == "Alicemain", reply(i))
check("and is signed up to this one straight away", db.signup(eid, 60)["status"] == "in")
check("the reply explains it", "every week" in reply(i) and "/always leave" in reply(i), reply(i))
i = inter(alice)
run(B.always_join.callback(i, eid, "Alicetank"))
check("joining again with another character switches it", db.series_members(eid)[0]["char_name"] == "Alicetank")
check("it doesn't disturb their existing sign-up", db.signup(eid, 60)["char_name"] == "Alicemain")
i = inter(alice)
run(B.always_join.callback(i, eid, "Nobody"))
check("an unknown character is refused", "saved character with that name" in reply(i))
i = inter(alice)
run(B.always_join.callback(i, eid2 + 999))
check("an unknown raid is refused", "isn't a repeating raid" in reply(i))
one_off = db.create_event(G, 11, "One off 2", "", NOW + DAY, 1, 0, 0, 0)
db.set_message_id(one_off, 1600)
i = inter(alice)
run(B.always_join.callback(i, one_off))
check("a one-off raid is refused", "isn't a repeating raid" in reply(i))
nobody = member(61)
i = inter(nobody)
run(B.always_join.callback(i, eid))
check("someone with no saved character is told what to do", "/character add" in reply(i), reply(i))
db.set_setting(G, "raider_role", 700)
i = inter(member(62, roles=[]))
run(B.always_join.callback(i, eid))
check("the raider role gate applies here too", "members with" in reply(i) and db.series_members(eid) and len(db.series_members(eid)) == 1)
db.set_setting(G, "raider_role", None)

print("F9 the next occurrence is seeded")
bobby = member(63)
save_char(63, "Bobbytank", "Warrior", "Protection", main=True)
i = inter(bobby)
run(B.always_join.callback(i, eid))
db.update_event(eid, cap_tank=1, start_ts=NOW - 60)   # the raid has started: time to post the next one
db.set_signup(eid, 63, "Bobbytank", "Warrior", "Protection", "tank", "in")
ev_row = db.event(eid)
check("a repeating raid's next occurrence is posted", run(B.spawn_next(ev_row)) is True)
nxt = [e for e in db.upcoming_events(G, NOW) if e["id"] != eid and e["series_id"] == eid and e["title"] == "Weekly run"]
check("with the same series id", len(nxt) == 1, [dict(e) for e in db.upcoming_events(G, NOW)])
new_id = nxt[0]["id"]
rows = {s["user_id"]: s for s in db.signups(new_id)}
check("everyone in the series is signed up to it", set(rows) == {60, 63} and rows[60]["char_name"] == "Alicetank")
db.update_event(eid, cap_tank=1)
check("the post was redrawn with them on it", CH[11].edits >= 1)
db.update_event(new_id, cap_tank=1)
db.conn.execute("DELETE FROM signups WHERE event_id=?", (new_id,))
db.conn.commit()
run(B.seed_series(new_id, eid))
rows = {s["user_id"]: s for s in db.signups(new_id)}
check("role limits still apply: the second tank is benched, in join order", rows[60]["status"] == "in" and rows[63]["status"] == "bench" and rows[63]["bench_from"] == "in", {k: dict(v) for k, v in rows.items()})

print("F9 a seeding error never causes a second post")
before = len(db.upcoming_events(G, 0))
db.update_event(new_id, start_ts=NOW - 60)
real_seed = B.seed_series


async def broken(*a):
    raise RuntimeError("seed exploded")


B.seed_series = broken
result = run(B.spawn_next(db.event(new_id)))
B.seed_series = real_seed
check("spawn_next still reports success (a retry would post twice)", result is True)
check("and exactly one new raid was posted", len(db.upcoming_events(G, 0)) == before + 1)

print("F9 a legacy repeating raid (no series id yet)")
legacy = db.create_event(G, 11, "Legacy weekly", "", NOW - 60, 1, 0, 0, 0, repeat_days=7)
db.set_message_id(legacy, 1700)
db.update_event(legacy, lock_shown=1)
check("it has no series id", db.event(legacy)["series_id"] == 0)
carol = member(64)
save_char(64, "Carolmain", "Priest", "Holy", main=True)
i = inter(carol)
run(B.always_join.callback(i, legacy))
check("joining uses the raid's own id as the series", db.series_members(legacy)[0]["user_id"] == 64)
run(B.spawn_next(db.event(legacy)))
made = [e for e in db.upcoming_events(G, NOW) if e["title"] == "Legacy weekly"]
check("its next occurrence joins the series and gets the member", made and made[0]["series_id"] == legacy
      and db.signup(made[0]["id"], 64)["char_name"] == "Carolmain")

print("F9 /always leave and list, and the pickers")
i = inter(carol)
run(B.always_list.callback(i))
check("list shows what they're in", "Legacy weekly" in reply(i) and "Carolmain" in reply(i) and "every week" in reply(i), reply(i))
choices = run(B.my_series_autocomplete(inter(carol), ""))
check("the leave picker offers only their own series", [c.value for c in choices] == [legacy])
rec = run(B.recurring_autocomplete(inter(carol), "legacy"))
check("the join picker offers repeating raids, filtered by what's typed", rec and all("Legacy" in c.name for c in rec))
check("and not one-off raids", all(c.value != one_off for c in run(B.recurring_autocomplete(inter(carol), ""))))
i = inter(carol)
run(B.always_leave.callback(i, legacy))
check("leaving removes them", db.series_members(legacy) == [] and "Done" in reply(i))
i = inter(carol)
run(B.always_leave.callback(i, legacy))
check("leaving twice says so", "weren't signed up" in reply(i))
i = inter(carol)
run(B.always_list.callback(i))
check("list is empty afterwards", "aren't signed up automatically" in reply(i))
rows_before = db.signup(made[0]["id"], 64)
check("dates they're already on are left alone", rows_before is not None)

print("housekeeping")
check("version string agrees everywhere", f"{B.VERSION})" in checks.USER_AGENT )
check("the migration added series_id and the series table",
      "series_id" in {r[1] for r in db.conn.execute("PRAGMA table_info(events)")}
      and db.conn.execute("SELECT name FROM sqlite_master WHERE name='series_signups'").fetchone() is not None)
old = dbmod.DB(str(tmp / "t.db"))
check("re-opening the database is safe", old.event(eid) is not None)
help_i = inter(admin)
run(B.help_command.callback(help_i))
help_text = " ".join(f.value for f in help_i.response.sent[0][1]["embed"].fields)
check("/help mentions /remindme, /always and the Calendar button", all(w in help_text for w in ("/remindme", "/always join", "Calendar")))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
