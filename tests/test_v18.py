"""Offline tests for v1.8: attendance, no-shows, /myraids, /status, unanswered nudge, audit log.

Run:  python -I tests/test_v18.py        (or python tests/run_all.py for every suite)
"""
import asyncio
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

proj = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["DB_PATH"] = str(tmp / "t.db")
os.environ["WOW_VERSION"] = "retail"
os.chdir(proj)
sys.path.insert(0, str(proj))

import discord
from discord import app_commands
import bot as B
import db as dbmod

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
DAY = 86400

print("schema")
check("signups.no_show / events.nudged exist",
      "no_show" in {r[1] for r in db.conn.execute("PRAGMA table_info(signups)")}
      and "nudged" in {r[1] for r in db.conn.execute("PRAGMA table_info(events)")})
dbmod.DB(str(tmp / "t.db"))
check("re-open is safe", True)


# ------------------------------------------------------------ fakes
def text(cid, name, cat):
    ch = MagicMock(spec=discord.TextChannel)
    ch.id, ch.name, ch.category_id, ch.mention = cid, name, cat, f"<#{cid}>"
    return ch


CH = {11: text(11, "raid-1-signup", 10), 21: text(21, "raid-2-signup", 20), 90: text(90, "raid-log", None)}
B.bot.get_channel = lambda cid: CH.get(cid)


async def _noop(): return None


def member(uid, roles=(), admin=False, bot=False):
    perms = types.SimpleNamespace(manage_events=False, administrator=admin)
    return types.SimpleNamespace(guild_permissions=perms, roles=[types.SimpleNamespace(id=r) for r in roles],
                                 id=uid, bot=bot, mention=f"<@{uid}>", display_name=f"User{uid}")


class FakeResp:
    def __init__(self): self.sent = []; self.deferred = False
    async def send_message(self, *a, **k): self.sent.append((a, k))
    async def defer(self, **k): self.deferred = True
    def is_done(self): return bool(self.sent) or self.deferred


RAIDERS = [member(100 + i) for i in range(6)] + [member(999, bot=True)]
PING_ROLE = types.SimpleNamespace(id=700, mention="<@&700>", name="raiders", mentionable=True,
                                  is_default=lambda: False, members=RAIDERS)
GUILD = types.SimpleNamespace(id=G, me=object(), get_role=lambda rid: PING_ROLE if rid == 700 else None,
                               get_member=lambda uid: types.SimpleNamespace(display_name="Dez") if uid == 10 else None)
for c in CH.values():
    c.guild = GUILD
    c.sent = []

    def mk(c):
        async def send(*a, **kw):
            c.sent.append((a, kw))
            return types.SimpleNamespace(id=1, jump_url="http://x")
        return send
    c.send = mk(c)
    c.permissions_for = lambda me: types.SimpleNamespace(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)


def inter(user, ch=None):
    ch = ch or CH[11]
    return types.SimpleNamespace(user=user, guild_id=G, guild=GUILD, channel=ch, channel_id=ch.id,
                                 response=FakeResp(), followup=types.SimpleNamespace(sent=[]), command=None)


admin = member(1, admin=True)
leader1 = member(2, roles=[500])
pleb = member(3)
db.set_setting(G, "officer_role@11", 500)

# ------------------------------------------------------------ stats
print("attendance stats")


def raid(title, days_ago, channel=11, guild=G):
    e = db.create_event(guild, channel, title, "", NOW - int(days_ago * DAY), 1, 0, 0, 0)
    db.set_message_id(e, 50 + e)
    return e


def su(e, uid, status, no_show=False):
    db.set_signup(e, uid, f"U{uid}", "Mage", "Fire", "dps", status)
    if no_show:
        db.set_no_show(e, uid, True)


r1, r2, r3, r4 = raid("A", 3), raid("B", 10), raid("C", 40), raid("D", 200)
up = db.create_event(G, 11, "Upcoming", "", NOW + DAY, 1, 0, 0, 0); db.set_message_id(up, 999)
other_guild = raid("Elsewhere", 2, guild=2)
# user 10: A in, B late, C no-show, D (outside 90d) in
su(r1, 10, "in"); su(r2, 10, "late"); su(r3, 10, "in", no_show=True); su(r4, 10, "in")
# user 11: A absent, B bench, C tentative
su(r1, 11, "absent"); su(r2, 11, "bench"); su(r3, 11, "tentative")
su(up, 10, "in")          # upcoming never counts
su(other_guild, 10, "in")  # other server never counts
rows = {r["user_id"]: r for r in db.stats(G, NOW - 90 * DAY, NOW)}
a = rows[10]
check("user 10: attended 2 (A, B), late 1, no-show 1, absent 0, total 3 in window",
      (a["attended"], a["late"], a["no_show"], a["absent"], a["total"]) == (2, 1, 1, 0, 3), dict(a))
b = rows[11]
check("user 11: attended 0; absent 1; bench/tentative are just signups",
      (b["attended"], b["no_show"], b["absent"], b["total"]) == (0, 0, 1, 3), dict(b))
check("window excludes 200-day-old raid, upcoming raid and other server", a["total"] == 3)
check("wider window includes the old raid", {r["user_id"]: r for r in db.stats(G, NOW - 400 * DAY, NOW)}[10]["total"] == 4)
check("ordered by attended desc", [r["user_id"] for r in db.stats(G, NOW - 90 * DAY, NOW)] == [10, 11])
check("single-user filter", [r["user_id"] for r in db.stats(G, NOW - 90 * DAY, NOW, 11)] == [11])
check("no signups -> empty", db.stats(G, NOW - 90 * DAY, NOW, 12345) == [])
check("no-show on a late signup removes it from late/attended",
      (lambda: (su(r2, 12, "late", no_show=True), {r["user_id"]: r for r in db.stats(G, NOW - 90 * DAY, NOW)}[12])[1])()["attended"] == 0)
check("set_no_show returns False when there's no signup", db.set_no_show(r1, 31337, True) is False)

print("/stats commands")
i = inter(pleb); run(B.stats_me.callback(i))
check("/stats me with no data", "no finished raids" in i.response.sent[0][0][0])
i = inter(member(10)); run(B.stats_me.callback(i))
txt = i.response.sent[0][0][0]
check("/stats me shows own numbers and rate", "2 attended (1 late)" in txt and "1 no-show" in txt and "67%" in txt, txt)
check("/stats me is private", i.response.sent[0][1].get("ephemeral") is True)
i = inter(pleb); run(B.stats_team.callback(i))
check("/stats team refused for non-officers", "Only raid officers" in i.response.sent[0][0][0])
i = inter(admin); run(B.stats_team.callback(i))
txt = i.response.sent[0][0][0]
check("/stats team lists everyone for an officer (display name when known, mention otherwise), most attended first",
      "**Dez**" in txt and "<@11>" in txt and txt.index("**Dez**") < txt.index("<@11>"), txt)
check("  mentions suppressed", i.response.sent[0][1]["allowed_mentions"].users is False)
i = inter(leader1); run(B.stats_team.callback(i))
check("/stats team allowed for channel leaders", "**Dez**" in i.response.sent[0][0][0])
for u in range(2000, 2100):
    su(r1, u, "in")
i = inter(admin); run(B.stats_team.callback(i))
txt = i.response.sent[0][0][0]
check("/stats team stays under Discord's 2000-char limit with 100+ people", len(txt) <= 2000 and "more" in txt, len(txt))
db._run("DELETE FROM signups WHERE user_id>=2000")

# ------------------------------------------------------------ /raid noshow + audit log
print("/raid noshow and the audit log")
live = db.create_event(G, 11, "Tonight", "", NOW - 600, 1, 0, 0, 0); db.set_message_id(live, 7)
su(live, 20, "in"); su(live, 21, "bench"); su(live, 22, "late")
db.set_setting(G, "log_channel", 90)
i = inter(admin); run(B.raid_noshow.callback(i, live, member(20)))
check("marks a no-show", db.signup(live, 20)["no_show"] == 1 and "no-show" in i.response.sent[0][0][0])
check("audit line posted to the log channel", len(CH[90].sent) == 1 and "no-show" in CH[90].sent[0][0][0] and "Tonight" in CH[90].sent[0][0][0])
check("audit mentions don't ping", CH[90].sent[0][1]["allowed_mentions"].users is False)
i = inter(admin); run(B.raid_noshow.callback(i, live, member(20), undo=True))
check("undo clears it", db.signup(live, 20)["no_show"] == 0)
i = inter(admin); run(B.raid_noshow.callback(i, live, member(21)))
check("benched player can't be marked", db.signup(live, 21)["no_show"] == 0 and "nothing to mark" in i.response.sent[0][0][0])
i = inter(admin); run(B.raid_noshow.callback(i, live, member(555)))
check("someone who never signed up can't be marked", "nothing to mark" in i.response.sent[0][0][0])
elsewhere = db.create_event(G, 21, "Raid 2", "", NOW - 600, 1, 0, 0, 0); db.set_message_id(elsewhere, 8); su(elsewhere, 30, "in")
i = inter(leader1)
try:
    run(B.raid_noshow.callback(i, elsewhere, member(30))); denied = False
except app_commands.CheckFailure:
    denied = True
check("channel leader can't mark no-shows in another group's raid", denied and db.signup(elsewhere, 30)["no_show"] == 0)
CH[90].sent.clear()
db.set_setting(G, "log_channel", None)
i = inter(admin); run(B.raid_noshow.callback(i, live, member(22)))
check("no log channel set -> nothing logged", CH[90].sent == [])
i = inter(admin); run(B.raid_roster.callback(i, live))
roster = i.response.sent[0][0][0]
check("/raid roster leaves out a marked no-show but keeps everyone else attending",
      "U22" not in roster and "U20" in roster and "1 attending" in roster, roster)


async def boom(*a, **k): raise discord.Forbidden(MagicMock(status=403, reason="x"), "no")
db.set_setting(G, "log_channel", 90)
old_send = CH[90].send; CH[90].send = boom
run(B.audit(G, "hello"))
check("audit swallows send failures", True)
CH[90].send = old_send
db.set_setting(G, "log_channel", 12345)
run(B.audit(G, "hello"))
check("audit with a deleted log channel is harmless", True)
db.set_setting(G, "log_channel", 90)

print("audit hooks on raid actions")
CH[90].sent.clear()
i = inter(admin)
i.channel = CH[11]
ev = db.create_event(G, 11, "Hook test", "", NOW + 9999, 1, 0, 0, 0); db.set_message_id(ev, 70)
CH[11].get_partial_message = MagicMock(side_effect=lambda mid: types.SimpleNamespace(delete=MagicMock(side_effect=lambda: _noop()), edit=MagicMock(side_effect=lambda **kw: _noop())))
run(B.raid_close.callback(inter(admin), ev))
run(B.raid_edit.callback(inter(admin), ev, title="Hook renamed"))
run(B.raid_remove.callback(inter(admin), ev, member(20)))
run(B.raid_cancel.callback(inter(admin), ev))
lines = [c[0][0] for c in CH[90].sent]
check("close, edit, remove, cancel each logged", len(lines) == 4 and "closed" in lines[0] and "edited" in lines[1] and "removed" in lines[2] and "cancelled" in lines[3], lines)

# ------------------------------------------------------------ /myraids
print("/myraids")
mine1 = db.create_event(G, 11, "Soon", "", NOW + DAY, 1, 0, 0, 0); db.set_message_id(mine1, 801)
mine2 = db.create_event(G, 21, "Later", "", NOW + 3 * DAY, 1, 0, 0, 0); db.set_message_id(mine2, 802)
past = db.create_event(G, 11, "Past", "", NOW - DAY, 1, 0, 0, 0); db.set_message_id(past, 803)
unanswered = db.create_event(G, 11, "Not answered", "", NOW + 2 * DAY, 1, 0, 0, 0); db.set_message_id(unanswered, 804)
su(mine1, 40, "in"); su(mine2, 40, "tentative"); su(past, 40, "in")
i = inter(member(40)); run(B.myraids.callback(i))
txt = i.response.sent[0][0][0]
check("lists upcoming answered raids in time order with links",
      txt.index("Soon") < txt.index("Later") and f"channels/{G}/11/801" in txt and f"channels/{G}/21/802" in txt, txt)
check("excludes past and unanswered", "Past" not in txt and "Not answered" not in txt)
check("shows status labels", "✅ in" in txt and "tentative" in txt)
i = inter(member(41)); run(B.myraids.callback(i))
check("nothing answered -> friendly message", "haven't answered" in i.response.sent[0][0][0])

# ------------------------------------------------------------ /status
print("/status")
i = inter(admin); run(B.status.callback(i))
txt = i.response.sent[0][0][0]
check("shows version, uptime, upcoming count", f"v{B.VERSION}" in txt and "up " in txt and "Upcoming raids:**" in txt, txt)
check("all-good permissions line", "all good" in txt)
CH[21].permissions_for = lambda me: types.SimpleNamespace(view_channel=True, send_messages=False, embed_links=False, read_message_history=True)
i = inter(admin); run(B.status.callback(i))
txt = i.response.sent[0][0][0]
check("flags a channel missing permissions", "<#21>" in txt and "send messages" in txt and "embed links" in txt, txt)
old_get = B.bot.get_channel
B.bot.get_channel = lambda cid: None if cid == 21 else CH.get(cid)
i = inter(admin); run(B.status.callback(i))
check("flags a channel the bot can't see", "can't see this channel" in i.response.sent[0][0][0])
B.bot.get_channel = old_get

# ------------------------------------------------------------ nudge
print("unanswered nudge")
db._run("DELETE FROM events")
n1 = db.create_event(G, 11, "Nudge me", "", NOW + 3 * 3600, 1, 0, 0, 0); db.set_message_id(n1, 900)
db.update_event(n1, lock_shown=1, reminded=1)
su(n1, 100, "in"); su(n1, 101, "absent")           # answered; 102..105 haven't
db.set_setting(G, "ping_role@11", 700)
CH[11].sent.clear()
run(B.reminder_loop.coro())
check("setting off (default): no nudge", CH[11].sent == [] and db.event(n1)["nudged"] == 0)
db.set_setting(G, "nudge_hours", 2)
run(B.reminder_loop.coro())
check("not yet inside the window (3h away, nudge at 2h): no nudge", CH[11].sent == [] and db.event(n1)["nudged"] == 0)
db.set_setting(G, "nudge_hours", 6)
run(B.reminder_loop.coro())
msgs = [c for c in CH[11].sent if "Still waiting" in c[0][0]]
check("inside the window: one nudge", len(msgs) == 1, [c[0][0] for c in CH[11].sent])
body = msgs[0][0][0]
check("pings only people who haven't answered (not signed-up, not declined, not bots)",
      all(f"<@{u}>" in body for u in (102, 103, 104, 105)) and not any(f"<@{u}>" in body for u in (100, 101, 999)), body)
check("marked nudged; second tick doesn't repeat", db.event(n1)["nudged"] == 1 and (run(B.reminder_loop.coro()) or True)
      and len([c for c in CH[11].sent if "Still waiting" in c[0][0]]) == 1)
n2 = db.create_event(G, 21, "No role", "", NOW + 3600, 1, 0, 0, 0); db.set_message_id(n2, 901)
db.update_event(n2, lock_shown=1, reminded=1)
CH[21].sent.clear()
db.set_setting(G, "ping_role", None)
run(B.reminder_loop.coro())
check("no ping role for the channel -> silently skipped (and marked)", CH[21].sent == [] and db.event(n2)["nudged"] == 1)
db.set_setting(G, "ping_role@11", 700)
n3 = db.create_event(G, 11, "Everyone answered", "", NOW + 3600, 1, 0, 0, 0); db.set_message_id(n3, 902); db.update_event(n3, lock_shown=1, reminded=1)
for m in RAIDERS:
    su(n3, m.id, "in")
CH[11].sent.clear()
run(B.reminder_loop.coro())
check("everyone answered -> no message", not [c for c in CH[11].sent if "Still waiting" in c[0][0]])
big = [member(5000 + i) for i in range(400)]
PING_ROLE.members = big
n4 = db.create_event(G, 11, "Huge role", "", NOW + 3600, 1, 0, 0, 0); db.set_message_id(n4, 903); db.update_event(n4, lock_shown=1, reminded=1)
CH[11].sent.clear()
run(B.reminder_loop.coro())
body = [c for c in CH[11].sent if "Still waiting" in c[0][0]][0][0][0]
check("400 missing people: message fits in 2000 chars and says how many more", len(body) <= 2000 and "more" in body, len(body))
PING_ROLE.members = RAIDERS

print("config commands")
i = inter(admin); run(B.config_nudge.callback(i, 12))
check("/config nudge 12", db.setting(G, "nudge_hours") == "12")
i = inter(admin); run(B.config_nudge.callback(i, 0))
check("/config nudge 0 turns it off", db.setting(G, "nudge_hours") == "0" and "No reminders" in i.response.sent[0][0][0])
i = inter(admin); run(B.config_logchannel.callback(i, CH[90]))
check("/config logchannel sets it", db.setting(G, "log_channel") == "90")
i = inter(admin); run(B.config_logchannel.callback(i, None))
check("/config logchannel (empty) clears it", db.setting(G, "log_channel") is None)
db.set_setting(G, "nudge_hours", 24); db.set_setting(G, "log_channel", 90)
i = inter(admin); run(B.config_show.callback(i))
txt = i.response.sent[0][0][0]
check("/config show lists nudge and log channel", "24 hours before" in txt and "<#90>" in txt, txt)

print("character menu on the Sign Up prompt")
chars = [{"id": 1, "name": "Main", "spec": "Fire", "wow_class": "Mage", "is_main": 1},
         {"id": 2, "name": "Alt", "spec": "Holy", "wow_class": "Priest", "is_main": 0}]
menu = B.CharacterSelect(1, chars)
check("no option is preselected (re-selecting a preselected option sends nothing, which blocked one-character players)",
      not any(o.default for o in menu.options), [o.default for o in menu.options])
check("the main is starred, others are not", menu.options[0].label.startswith("⭐ Main") and menu.options[1].label == "Alt")
check("option values still identify the character", [o.value for o in menu.options] == ["1", "2"])

print("singular/plural wording")
check("plural helper", B.plural(1, "day") == "1 day" and B.plural(2, "day") == "2 days" and B.plural(0, "hour") == "0 hours")
i = inter(admin); run(B.config_archive.callback(i, 1))
check("/config archive 1 says '1 day', not '1 days'", "1 day after" in i.response.sent[0][0][0], i.response.sent[0][0][0])
i = inter(admin); run(B.config_nudge.callback(i, 1))
check("/config nudge 1 says '1 hour'", i.response.sent[0][0][0].startswith("1 hour before"), i.response.sent[0][0][0])
i = inter(admin); run(B.config_reminder.callback(i, 1))
check("/config reminder 1 says '1 minute'", "**1 minute**" in i.response.sent[0][0][0], i.response.sent[0][0][0])
db.set_setting(G, "archive_days", 1); db.set_setting(G, "nudge_hours", 1); db.set_setting(G, "reminder_minutes", 1)
i = inter(admin); run(B.config_show.callback(i))
txt = i.response.sent[0][0][0]
check("/config show uses singular too", "1 minute before" in txt and "1 day after" in txt and "1 hour before" in txt, txt)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
