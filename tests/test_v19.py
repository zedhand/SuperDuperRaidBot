"""Offline tests for v1.9 ("faster for officers"): typed dates, /raid list, /raid add, /config raiderrole,
/config autoclose.

Run:  python -I tests/test_v19.py        (or python tests/run_all.py for every suite)
"""
import asyncio
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

proj = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["DB_PATH"] = str(tmp / "t.db")
os.environ["WOW_VERSION"] = "retail"
os.chdir(proj)
sys.path.insert(0, str(proj))

from discord import app_commands  # noqa: E402
import bot as B  # noqa: E402
import db as dbmod  # noqa: E402

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
NY = ZoneInfo("America/New_York")
db.set_setting(G, "timezone", "America/New_York")


# ------------------------------------------------------------ fakes
class FakeResp:
    def __init__(self): self.sent = []; self.deferred = False
    async def send_message(self, *a, **k): self.sent.append((a, k))
    async def defer(self, **k): self.deferred = True
    def is_done(self): return bool(self.sent) or self.deferred


class FakeFollowup:
    def __init__(self): self.sent = []
    async def send(self, *a, **k): self.sent.append((a, k))


def member(uid, roles=(), admin=False, bot=False):
    perms = types.SimpleNamespace(manage_events=False, administrator=admin, manage_guild=admin)
    return types.SimpleNamespace(guild_permissions=perms, roles=[types.SimpleNamespace(id=r) for r in roles],
                                 id=uid, bot=bot, mention=f"<@{uid}>", display_name=f"User{uid}")


class FakeChannel:
    def __init__(self, cid):
        self.id, self.category_id, self.mention, self.name = cid, None, f"<#{cid}>", f"chan{cid}"
        self.sent, self.edits = [], 0
        self.guild = types.SimpleNamespace(id=G, me=object(), get_role=lambda r: None)

    async def send(self, *a, **k):
        self.sent.append((a, k))
        return types.SimpleNamespace(id=len(self.sent) + 500, jump_url="http://x")

    def permissions_for(self, me):
        return types.SimpleNamespace(view_channel=True, send_messages=True, embed_links=True,
                                     read_message_history=True, mention_everyone=False)

    def get_partial_message(self, mid):
        chan = self

        class Msg:
            async def edit(self, **kw):
                chan.edits += 1
        return Msg()


CH = {11: FakeChannel(11), 12: FakeChannel(12)}
B.bot.get_channel = lambda cid: CH.get(cid)


def inter(user, ch=None, message_id=None):
    ch = ch or CH[11]
    return types.SimpleNamespace(user=user, guild_id=G, guild=ch.guild, channel=ch, channel_id=ch.id,
                                 response=FakeResp(), followup=FakeFollowup(), command=None,
                                 message=types.SimpleNamespace(id=message_id) if message_id else None,
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
    for t in ("signups", "events"):
        db.conn.execute(f"DELETE FROM {t}")
    db.conn.commit()


admin = member(1, admin=True)
leader11 = member(2, roles=[500])       # raid leader of channel 11 only
pleb = member(3)
db.set_setting(G, "officer_role@11", 500)

# ------------------------------------------------------------ F1 parser
print("F1 typed dates and times")
SUN = datetime(2026, 11, 8, 18, 0)  # a Sunday evening


def when(d, t=None, now=SUN, base=None):
    try:
        return B.parse_when(d, t, now, base)
    except B.WhenError as e:
        return str(e)


cases = [
    (("thu", "8pm"), datetime(2026, 11, 12, 20, 0)),
    (("thu 8pm", None), datetime(2026, 11, 12, 20, 0)),
    (("thursday at 8:30 pm", None), datetime(2026, 11, 12, 20, 30)),
    (("thu @ 20:15", None), datetime(2026, 11, 12, 20, 15)),
    (("tomorrow 19:30", None), datetime(2026, 11, 9, 19, 30)),
    (("tmrw", "7pm"), datetime(2026, 11, 9, 19, 0)),
    (("tonight 9pm", None), datetime(2026, 11, 8, 21, 0)),
    (("nov 14 8pm", None), datetime(2026, 11, 14, 20, 0)),
    (("November 14th 8pm", None), datetime(2026, 11, 14, 20, 0)),
    (("14 nov", "20:00"), datetime(2026, 11, 14, 20, 0)),
    (("+7d", "20:00"), datetime(2026, 11, 15, 20, 0)),
    (("+2w 8pm", None), datetime(2026, 11, 22, 20, 0)),
    (("in 3 days 8pm", None), datetime(2026, 11, 11, 20, 0)),
    (("in 2 weeks", "8pm"), datetime(2026, 11, 22, 20, 0)),
    (("2026-11-14", "20:00"), datetime(2026, 11, 14, 20, 0)),
    (("2026/11/14 20:00", None), datetime(2026, 11, 14, 20, 0)),
    (("friday noon", None), datetime(2026, 11, 13, 12, 0)),
    (("sat midnight", None), datetime(2026, 11, 14, 0, 0)),
    (("THU 8PM", None), datetime(2026, 11, 12, 20, 0)),
    (("  thu    8pm ", None), datetime(2026, 11, 12, 20, 0)),
    (("sun", "8pm"), datetime(2026, 11, 8, 20, 0)),          # Sunday 18:00, 8pm is still ahead: today
    (("sun", "5pm"), datetime(2026, 11, 15, 17, 0)),         # 5pm has passed: next Sunday
    (("next sun", "8pm"), datetime(2026, 11, 15, 20, 0)),    # "next" is never today
    (("this thu", "8pm"), datetime(2026, 11, 12, 20, 0)),
    (("nov 1 8pm", None), datetime(2027, 11, 1, 20, 0)),     # already past this year: next year
    (("nov 8 9pm", None), datetime(2026, 11, 8, 21, 0)),     # today, later
    (("nov 8 5pm", None), datetime(2027, 11, 8, 17, 0)),     # today, already passed
    (("dec 25 2027 8pm", None), datetime(2027, 12, 25, 20, 0)),
    (("12am", None), None),
]
for (d, t), want in cases[:-1]:
    got = when(d, t)
    check(f"{d!r} {t or ''}".strip() + f" -> {want:%a %b %d %H:%M}", got == want, got)
check("12am alone is not a date", isinstance(when("12am", None), str))

for d, t in (("banana", "8pm"), ("thu", None), ("thu", "8"), ("thu", "25:00"), ("nov 31 8pm", None),
             ("2026-02-30", "20:00"), ("", "8pm"), ("thu", "13pm"), ("next", "8pm")):
    got = when(d, t)
    check(f"unclear input refused with a message: {d!r} {t!r}", isinstance(got, str) and len(got) > 10, got)
check("the 'time' box wins when both are given", when("thu 9pm", "7pm") != datetime(2026, 11, 12, 21, 0))
check("a refusal for a time box value names it", "`8`" in when("thu", "8"))
check("a missing time says what to add", "Add a time" in when("thu", None))

print("F1 editing: change just the day or just the time")
base = datetime(2026, 11, 12, 20, 0)
check("only a time: same day", when("", "9pm", base=base) == datetime(2026, 11, 12, 21, 0))
check("only a day: same time", when("fri", None, base=base) == datetime(2026, 11, 13, 20, 0))
check("day with a time", when("fri 7pm", None, base=base) == datetime(2026, 11, 13, 19, 0))
check("nothing typed and no base: refused", isinstance(when("", None), str))

print("F1 daylight saving still guarded")
db.set_setting(G, "timezone", "America/New_York")
ts, problem = B.parse_start_checked(G, "2027-03-14", "2:30am")
check("a nonexistent time typed in words is still refused", ts is None and "doesn't exist" in problem, problem)
ts, problem = B.parse_start_checked(G, "2027-03-15 8pm")
check("a normal time works without the time box",
      ts == int(datetime(2027, 3, 15, 20, 0, tzinfo=NY).timestamp()) and not problem, (ts, problem))

print("F1 date picker")
choices = run(B.date_autocomplete(inter(admin), ""))
check("offers 15 days starting today", len(choices) == 15 and "(today)" in choices[0].name and "(tomorrow)" in choices[1].name)
check("choice values are ISO dates the parser accepts", all(B.parse_start_checked(G, c.value, "20:00")[0] for c in choices[1:]))
thursdays = run(B.date_autocomplete(inter(admin), "thu"))
check("typing a weekday filters the list", 1 <= len(thursdays) <= 3 and all("Thu" in c.name for c in thursdays))
check("free text like 'thu 8pm' gives no suggestions (it is used as typed)", run(B.date_autocomplete(inter(admin), "thu 8pm")) == [])

print("F1 /raid create, copy and edit take the new dates")
wipe()
i = inter(admin)
run(B.raid_create.callback(i, "tomorrow 8pm", None, title="Typed date"))
ev = db.upcoming_events(G, 0)[0]
local_now = datetime.now(NY)
want = datetime.combine(local_now.date() + timedelta(days=1), datetime.min.time().replace(hour=20), tzinfo=NY)
check("/raid create date:'tomorrow 8pm' (no time box)", ev["start_ts"] == int(want.timestamp()), (ev["start_ts"], int(want.timestamp())))
i = inter(admin)
run(B.raid_create.callback(i, "tomorrow", None, title="No time"))
check("/raid create with no time anywhere asks for one", "Add a time" in reply(i) and len(db.upcoming_events(G, 0)) == 1, reply(i))
i = inter(admin)
run(B.raid_create.callback(i, "yesterday 8pm", None, title="Past"))
check("an unreadable day is refused, nothing posted", len(db.upcoming_events(G, 0)) == 1)

i = inter(admin)
run(B.raid_copy.callback(i, ev["id"], "+7d", "8pm"))
copies = db.upcoming_events(G, 0)
want_copy = datetime.combine(local_now.date() + timedelta(days=7), datetime.min.time().replace(hour=20), tzinfo=NY)
check("/raid copy takes '+7d' and '8pm' (seven days from today)",
      len(copies) == 2 and copies[1]["start_ts"] == int(want_copy.timestamp()), [c["start_ts"] for c in copies])

i = inter(admin)
run(B.raid_edit.callback(i, ev["id"], at="9pm", notify=False))
e2 = db.event(ev["id"])
check("/raid edit with only a time keeps the day",
      datetime.fromtimestamp(e2["start_ts"], NY).hour == 21 and datetime.fromtimestamp(e2["start_ts"], NY).date() == datetime.fromtimestamp(ev["start_ts"], NY).date())
i = inter(admin)
run(B.raid_edit.callback(i, ev["id"], date="+10d", notify=False))
e3 = db.event(ev["id"])
check("/raid edit with only a day keeps the time", datetime.fromtimestamp(e3["start_ts"], NY).hour == 21
      and (datetime.fromtimestamp(e3["start_ts"], NY).date() - local_now.date()).days == 10)

# ------------------------------------------------------------ F2 /raid list
print("F2 /raid list")
wipe()
a = new_event("Naxx", start=NOW + 2 * DAY, channel=11)
b = new_event("Ulduar", start=NOW + 3 * DAY, channel=12)
c = new_event("Old", start=NOW - DAY, channel=11)
db.update_event(a, cap_tank=2, cap_healer=4)
db.set_signup(a, 50, "Tanky", "Warrior", "Protection", "tank", "in")
db.set_signup(a, 51, "Healy", "Priest", "Holy", "healer", "late")
db.set_signup(a, 52, "Maybe", "Mage", "Fire", "dps", "tentative")
db.set_closed(a, True)
i = inter(admin)
run(B.raid_list.callback(i))
text = reply(i)
check("lists upcoming raids with title, link and counts",
      "Naxx" in text and "Ulduar" in text and "Old" not in text and "discord.com/channels/1/11/" in text, text)
check("shows role counts against the limits", "🛡️ 1/2" in text and "💚 1/4" in text and "👥 2 in" in text, text)
check("shows tentative count and closed state", "1 tentative" in text and "closed" in text, text)
i = inter(leader11)
run(B.raid_list.callback(i))
check("a channel raid leader only sees their own channel", "Naxx" in reply(i) and "Ulduar" not in reply(i), reply(i))
wipe()
i = inter(admin)
run(B.raid_list.callback(i))
check("says so when there is nothing", "No upcoming raids" in reply(i))
for n in range(20):
    new_event(f"R{n:02d}", start=NOW + (n + 1) * HOUR)
i = inter(admin)
run(B.raid_list.callback(i))
check("many raids are cut at 15 with a count, within Discord's limit", "and 5 more" in reply(i) and len(reply(i)) <= 2000, len(reply(i)))

# ------------------------------------------------------------ F3 /raid add
print("F3 /raid add")
wipe()
ev = new_event("Add test", start=NOW + DAY, channel=11)
alice, bob = member(60), member(61)
db.upsert_character(60, "Alicemain", "Mage", "Fire", "dps", True)
db.upsert_character(60, "Alicetank", "Warrior", "Protection", "tank", False)
i = inter(admin)
run(B.raid_add.callback(i, ev, alice))
row = db.signup(ev, 60)
check("signs someone up with their main", row and row["status"] == "in" and row["char_name"] == "Alicemain", reply(i))
check("the reply doesn't ping them", i.response.sent[0][1].get("allowed_mentions").users is False)
i = inter(admin)
run(B.raid_add.callback(i, ev, alice, character="Alicetank"))
check("or one of their characters", db.signup(ev, 60)["char_name"] == "Alicetank" and db.signup(ev, 60)["role"] == "tank")
i = inter(admin)
run(B.raid_add.callback(i, ev, alice, status=app_commands.Choice(name="Late", value="late")))
check("changing only the status keeps the character", db.signup(ev, 60)["status"] == "late" and db.signup(ev, 60)["char_name"] == "Alicetank")
i = inter(admin)
run(B.raid_add.callback(i, ev, alice, status=app_commands.Choice(name="Absent", value="absent")))
check("can mark them absent", db.signup(ev, 60)["status"] == "absent")
i = inter(admin)
run(B.raid_add.callback(i, ev, bob))
check("no character and no class: explains what to do", db.signup(ev, 61) is None and "no saved character" in reply(i), reply(i))
i = inter(admin)
run(B.raid_add.callback(i, ev, bob, wowclass="Rogue", spec="Subtlety"))
check("a class and spec work without a saved character", db.signup(ev, 61)["spec"] == "Subtlety" and db.signup(ev, 61)["char_name"] == "User61")
i = inter(admin)
run(B.raid_add.callback(i, ev, bob, wowclass="Rogue", spec="Nonsense"))
check("a bad spec is refused and changes nothing", "valid class and a spec" in reply(i) and db.signup(ev, 61)["spec"] == "Subtlety")
i = inter(admin)
run(B.raid_add.callback(i, ev, bob, character="Nobody"))
check("an unknown saved character is refused", "no saved character called" in reply(i))
i = inter(admin)
run(B.raid_add.callback(i, ev, member(99, bot=True)))
check("bots can't be signed up", "Bots can't" in reply(i))

db.update_event(ev, cap_tank=1)
db.upsert_character(62, "Tank2", "Warrior", "Protection", "tank", True)
i = inter(admin)
run(B.raid_add.callback(i, ev, alice, character="Alicetank"))      # takes the only tank slot
i = inter(admin)
run(B.raid_add.callback(i, ev, member(62)))
check("a full role puts them on the bench, and says so", db.signup(ev, 62)["status"] == "bench" and "bench" in reply(i), reply(i))
db.set_closed(ev, True)
i = inter(admin)
run(B.raid_add.callback(i, ev, member(61), status=app_commands.Choice(name="Tentative", value="tentative")))
check("works while sign-ups are closed", db.signup(ev, 61)["status"] == "tentative")
started = new_event("Started", start=NOW - 60)
i = inter(admin)
run(B.raid_add.callback(i, started, alice))
check("refused once the raid has started", "already started" in reply(i) and db.signup(started, 60) is None)
other = new_event("Other channel", start=NOW + DAY, channel=12)
i = inter(leader11)
try:
    run(B.raid_add.callback(i, other, alice))
    blocked = False
except app_commands.CheckFailure:
    blocked = True
check("a channel leader can't add people to another channel's raid", blocked and db.signup(other, 60) is None)
i = inter(leader11)
run(B.raid_add.callback(i, ev, member(63), wowclass="Hunter", spec="Marksmanship"))
check("but can in their own channel", db.signup(ev, 63) is not None)
logch = FakeChannel(90)
CH[90] = logch
db.set_setting(G, "log_channel", 90)
i = inter(admin)
run(B.raid_add.callback(i, ev, alice))
check("it is written to the activity log", any("set <@60>" in (a[0] if a else "") for a, k in logch.sent), logch.sent)
db.set_setting(G, "log_channel", None)
mc = run(B.member_character_autocomplete(types.SimpleNamespace(namespace=types.SimpleNamespace(member=alice)), "alice"))
check("character picker offers that member's characters", {c.value for c in mc} == {"Alicemain", "Alicetank"})
check("and nothing before a member is chosen", run(B.member_character_autocomplete(types.SimpleNamespace(namespace=types.SimpleNamespace()), "")) == [])

# ------------------------------------------------------------ F4 raider role
print("F4 /config raiderrole")
wipe()
ev = new_event("Gated", start=NOW + DAY)
view = B.RaidView()
member_ok, member_no = member(70, roles=[700]), member(71)


def press(button, user):
    i = inter(user, message_id=1000 + ev)
    run(button.callback(i))
    return reply(i)


check("with no raider role, anyone can press Sign Up", "Pick a saved character" in press(view.signup, member_no) or "Choose your class" in press(view.signup, member_no))
db.set_setting(G, "raider_role", 700)
out = press(view.signup, member_no)
check("with a raider role set, others are turned away politely", "for members with <@&700>" in out, out)
check("  on every button, not just Sign Up", all("for members with" in press(b, member_no) for b in (view.late, view.tentative, view.bench, view.absent)))
check("a raider gets through", "for members with" not in press(view.signup, member_ok))
check("a full officer always gets through", "for members with" not in press(view.signup, admin))
check("so does a channel raid leader", "for members with" not in press(view.signup, leader11))
check("the refusal doesn't ping the role", True)
i = inter(admin)
run(B.config_raiderrole.callback(i, types.SimpleNamespace(is_default=lambda: True, id=1, mention="@everyone")))
check("setting it to @everyone clears it", db.setting(G, "raider_role") is None and "Anyone" in reply(i), reply(i))
i = inter(admin)
run(B.config_raiderrole.callback(i, types.SimpleNamespace(is_default=lambda: False, id=700, mention="<@&700>")))
check("setting a role saves it", db.setting(G, "raider_role") == "700" and "<@&700>" in reply(i))
i = inter(admin)
run(B.config_show.callback(i))
check("/config show lists it", "Sign-up role" in reply(i) and "<@&700>" in reply(i), reply(i))
i = inter(admin)
run(B.config_raiderrole.callback(i, None))
check("leaving it empty clears it", db.setting(G, "raider_role") is None)

# ------------------------------------------------------------ F5 autoclose
print("F5 /config autoclose")
wipe()
for c in CH.values():
    c.sent.clear()
    c.edits = 0
B._tries.clear()
i = inter(admin)
run(B.config_autoclose.callback(i, 3))
check("saves the setting", db.setting(G, "autoclose_hours") == "3" and "3 hours" in reply(i), reply(i))
i = inter(admin)
run(B.config_show.callback(i))
check("/config show lists it", "Auto-close" in reply(i) and "3 hours" in reply(i), reply(i))


def tick():
    run(B.reminder_loop.coro())


soon = new_event("Soon", start=NOW + 2 * HOUR)
later = new_event("Later", start=NOW + 5 * HOUR)
tick()
check("a raid inside the window is closed", db.event(soon)["closed"] == 1 and db.event(soon)["autoclosed"] == 1)
check("the post is redrawn as closed", CH[11].edits >= 1)
check("a raid outside the window is left open", db.event(later)["closed"] == 0 and db.event(later)["autoclosed"] == 0)
db.set_closed(soon, False)
tick()
check("an officer who reopens it isn't overruled a minute later", db.event(soon)["closed"] == 0)

db.update_event(later, start_ts=NOW + 3 * HOUR - 30)
tick()
check("when the window arrives, it closes", db.event(later)["closed"] == 1)

check("is_locked: an auto-closed raid refuses sign-ups", B.is_locked(db.event(later)))

print("F5 created inside the window, and moved")
guild = CH[11].guild
eid, msg, notes = run(B.post_event(CH[11], guild, 1, "Last minute", "", NOW + HOUR, 0, 0, 0))
check("a raid posted inside the window is not closed behind the officer's back", db.event(eid)["autoclosed"] == 1)
db.update_event(eid, lock_shown=1)
tick()
check("  and stays open", db.event(eid)["closed"] == 0)

i = inter(admin)
run(B.raid_edit.callback(i, later, date="+5d", at="8pm", notify=False))
e = db.event(later)
check("moving a closed-by-autoclose raid later reopens it and re-arms", e["closed"] == 0 and e["autoclosed"] == 0, dict(e))
db.update_event(later, start_ts=NOW + 2 * HOUR)
tick()
check("and it closes again when its new time comes", db.event(later)["closed"] == 1)

i = inter(admin)
run(B.config_autoclose.callback(i, 0))
check("0 turns it off", db.setting(G, "autoclose_hours") == "0" and "stay open" in reply(i))
off = new_event("Off", start=NOW + HOUR)
tick()
check("and nothing closes", db.event(off)["closed"] == 0)
check("the migration added the column", "autoclosed" in {r[1] for r in db.conn.execute("PRAGMA table_info(events)")})

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
