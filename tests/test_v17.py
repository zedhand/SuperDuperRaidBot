"""Offline tests for v1.7. Run: python -I tests/test_v17.py"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import time
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

proj = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["DB_PATH"] = str(tmp / "t.db")
os.environ["WOW_VERSION"] = "retail"
os.chdir(proj)
sys.path.insert(0, str(proj))

import discord
from discord import app_commands
import bot as B
import checks
import db as dbmod
import embeds as E

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


# ---------------------------------------------------------------- migration from v1.6 db
print("migration")
old = tmp / "v16.db"
c = sqlite3.connect(old)
c.executescript("""
CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', start_ts INTEGER NOT NULL,
  creator_id INTEGER NOT NULL, cap_tank INTEGER NOT NULL DEFAULT 0, cap_healer INTEGER NOT NULL DEFAULT 0,
  cap_dps INTEGER NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0, reminded INTEGER NOT NULL DEFAULT 0,
  lock_shown INTEGER NOT NULL DEFAULT 0);
CREATE TABLE signups (event_id INTEGER NOT NULL, user_id INTEGER NOT NULL, char_name TEXT, wow_class TEXT, spec TEXT,
  role TEXT, status TEXT NOT NULL, updated_ts INTEGER NOT NULL DEFAULT 0, bench_from TEXT, PRIMARY KEY(event_id,user_id));
CREATE TABLE settings (guild_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(guild_id,key));
INSERT INTO events(guild_id,channel_id,message_id,title,start_ts,creator_id) VALUES (9,8,7,'Old raid',4102444800,1);
""")
c.commit(); c.close()
d = dbmod.DB(str(old))
cols = {r[1] for r in d.conn.execute("PRAGMA table_info(events)")}
check("new event columns added", {"repeat_days", "repeated", "thread_id", "archived"} <= cols)
check("templates table created", d._one("SELECT name FROM sqlite_master WHERE name='templates'") is not None)
ev = d.event(1)
check("old event keeps data, defaults are 0", ev["title"] == "Old raid" and ev["repeat_days"] == 0 and ev["archived"] == 0)
dbmod.DB(str(old))
check("re-open is safe", True)

# ---------------------------------------------------------------- fakes
B.db = dbmod.DB(str(tmp / "t.db"))
db = B.db
G = 1


def text(cid, name, cat):
    ch = MagicMock(spec=discord.TextChannel)
    ch.id, ch.name, ch.category_id, ch.mention = cid, name, cat, f"<#{cid}>"
    return ch


CH = {11: text(11, "raid-1-signup", 10), 21: text(21, "raid-2-signup", 20)}
B.bot.get_channel = lambda cid: CH.get(cid)
posted_msgs = []


class FakeMsg:
    def __init__(self, ch, fail_thread=False):
        self.id = 5000 + len(posted_msgs)
        self.jump_url = f"http://msg/{self.id}"
        self.fail_thread = fail_thread

    async def create_thread(self, name, auto_archive_duration):
        if self.fail_thread:
            raise discord.Forbidden(MagicMock(status=403, reason="no"), "missing perms")
        t = MagicMock(spec=discord.Thread)
        t.id = 9000 + self.id
        t.name = name
        t.delete = MagicMock(side_effect=lambda: _noop())
        CH[t.id] = t
        return t


async def _noop(): return None


def wire(ch, fail_send=False, fail_thread=False):
    sent = []

    async def send(**kw):
        if fail_send:
            raise discord.Forbidden(MagicMock(status=403, reason="no"), "can't post")
        sent.append(kw)
        m = FakeMsg(ch, fail_thread)
        posted_msgs.append(m)
        return m
    ch.send = send
    ch.get_partial_message = MagicMock(side_effect=lambda mid: types.SimpleNamespace(delete=MagicMock(side_effect=lambda: _noop()), edit=MagicMock(side_effect=lambda **kw: _noop())))
    ch.guild = GUILD
    return sent


class FakeResp:
    def __init__(self): self.sent = []; self.deferred = False
    async def send_message(self, *a, **k): self.sent.append((a, k))
    async def defer(self, **k): self.deferred = True
    async def edit_message(self, **k): self.sent.append(((k.get('content'),), k))
    def is_done(self): return bool(self.sent) or self.deferred


def role(rid, mentionable=True):
    return types.SimpleNamespace(id=rid, mention=f"<@&{rid}>", name=f"role{rid}", mentionable=mentionable,
                                 is_default=lambda: False)


GUILD = types.SimpleNamespace(id=G, me=object(), get_role=lambda rid: role(rid))
for ch in CH.values():
    ch.guild = GUILD


def member(roles=(), admin=False):
    perms = types.SimpleNamespace(manage_events=False, administrator=admin)
    return types.SimpleNamespace(guild_permissions=perms, roles=[types.SimpleNamespace(id=r) for r in roles], id=77, mention="<@77>")


def inter(user, ch=None, **wire_kw):
    ch = ch or CH[11]
    sent = wire(ch, **wire_kw)
    ch.guild = GUILD
    fu = types.SimpleNamespace(sent=[])

    async def fsend(*a, **k): fu.sent.append((a, k))
    fu.send = fsend
    return types.SimpleNamespace(user=user, guild_id=G, guild=GUILD, channel=ch, channel_id=ch.id,
                                 response=FakeResp(), followup=fu, sent=sent, command=None)



admin = member(admin=True)
pleb = member()
leader1 = member([500])
db.set_setting(G, "officer_role@11", 500)
FUT = "2099-01-01"

# ---------------------------------------------------------------- templates (db)
print("templates (db)")
db.save_template(G, "Naxx", "Naxxramas", "bring flasks", 2, 6, 17, 7)
t = db.template(G, "naxx")
check("lookup is case-insensitive", t is not None and t["title"] == "Naxxramas" and t["repeat_days"] == 7)
db.save_template(G, "NAXX", "Naxx 2", "", 1, 1, 1, 0)
check("saving the same name updates it", len(db.templates(G)) == 1 and db.template(G, "Naxx")["title"] == "Naxx 2")
db.save_template(G, "Naxx", "Naxxramas", "bring flasks", 2, 6, 17, 7)
db.save_template(2, "Naxx", "Other server", "", 0, 0, 0, 0)
check("templates are per server", db.template(2, "Naxx")["title"] == "Other server" and db.template(G, "Naxx")["title"] == "Naxxramas")
check("delete works / reports missing", db.delete_template(2, "Naxx") and not db.delete_template(2, "Naxx"))

# ---------------------------------------------------------------- create
print("/raid create")
cb = B.raid_create.callback
i = inter(admin)
run(cb(i, FUT, "20:00", template="naxx"))
ev = db.event(i.sent and max(r["id"] for r in db._all("select id from events where guild_id=?", (G,))))
check("template fills title, description, limits, repeat",
      (ev["title"], ev["description"], ev["cap_tank"], ev["cap_healer"], ev["cap_dps"], ev["repeat_days"]) == ("Naxxramas", "bring flasks", 2, 6, 17, 7), dict(ev))
check("post sent with the embed", len(i.sent) == 1 and "embed" in i.sent[0])
check("footer shows the repeat", "weekly" in i.sent[0]["embed"].footer.text, i.sent[0]["embed"].footer.text)
i = inter(admin)
run(cb(i, FUT, "20:00", title="Custom", template="naxx", tanks=0, healers=3, description="", repeat=B.REPEAT_CHOICES[0]))
ev = db.event(max(r["id"] for r in db._all("select id from events")))
check("explicit options beat the template (including 0 and empty)",
      (ev["title"], ev["description"], ev["cap_tank"], ev["cap_healer"], ev["cap_dps"], ev["repeat_days"]) == ("Custom", "", 0, 3, 17, 0), dict(ev))
i = inter(admin); run(cb(i, FUT, "20:00", template="nope"))
check("unknown template -> clear message", "No template" in i.response.sent[0][0][0] and not i.sent)
i = inter(admin); run(cb(i, FUT, "20:00"))
check("no title and no template -> asks for one", "title" in i.response.sent[0][0][0].lower() and not i.sent)
i = inter(admin); run(cb(i, "not-a-date", "20:00", title="X"))
check("bad date -> explanation", "Couldn't read" in i.response.sent[0][0][0] and not i.sent)
i = inter(admin); run(cb(i, "2001-01-01", "20:00", title="X"))
check("past date -> refused", "past" in i.response.sent[0][0][0] and not i.sent)
i = inter(admin); run(cb(i, FUT, "20:00", title="Weekly", repeat=B.REPEAT_CHOICES[2]))
check("repeat option (every 2 weeks)", db.event(max(r["id"] for r in db._all("select id from events")))["repeat_days"] == 14)

before = db._one("select count(*) c from events")["c"]
i = inter(admin, fail_send=True)
run(cb(i, FUT, "20:00", title="Can't post"))
check("send failure: friendly message", i.followup.sent and "couldn't post" in i.followup.sent[0][0][0])
check("send failure: no orphan event row left behind", db._one("select count(*) c from events")["c"] == before)

# ---------------------------------------------------------------- threads
print("threads")
i = inter(admin)
run(cb(i, FUT, "20:00", title="No thread"))
check("threads off by default", db.event(max(r["id"] for r in db._all("select id from events")))["thread_id"] == 0)
i = inter(admin)
run(cb(i, FUT, "20:00", title="With thread", thread=True))
ev = db.event(max(r["id"] for r in db._all("select id from events")))
check("thread=True creates and stores a thread", ev["thread_id"] != 0 and CH[ev["thread_id"]].name.startswith("With thread · "))
db.set_setting(G, "threads", "1")
i = inter(admin)
run(cb(i, FUT, "20:00", title="Default thread"))
check("/config threads on makes it the default", db.event(max(r["id"] for r in db._all("select id from events")))["thread_id"] != 0)
i = inter(admin)
run(cb(i, FUT, "20:00", title="Opt out", thread=False))
check("thread=False overrides the default", db.event(max(r["id"] for r in db._all("select id from events")))["thread_id"] == 0)
i = inter(admin, fail_thread=True)
run(cb(i, FUT, "20:00", title="No perms"))
ev = db.event(max(r["id"] for r in db._all("select id from events")))
check("thread failure: raid still posted, with an explanation",
      ev["title"] == "No perms" and ev["thread_id"] == 0 and "Create Public Threads" in i.followup.sent[0][0][0])
db.set_setting(G, "threads", "0")

# ---------------------------------------------------------------- copy
print("/raid copy")
src = db.create_event(G, 11, "Source", "desc", int(time.time()) + 5000, 1, 2, 3, 4)
db.set_message_id(src, 123)
db.set_signup(src, 5, "Bob", "Mage", "Fire", "dps", "in")
i = inter(admin)
run(B.raid_copy.callback(i, src, FUT, "19:30"))
new = db.event(max(r["id"] for r in db._all("select id from events")))
check("copy: same limits/description/channel, new time, no signups, new title kept",
      (new["id"] != src, new["title"], new["description"], new["cap_tank"], new["cap_healer"], new["cap_dps"], new["channel_id"], db.signups(new["id"])) == (True, "Source", "desc", 2, 3, 4, 11, []))
check("copy: time is 2099-01-01 19:30 server time", datetime.fromtimestamp(new["start_ts"], B.guild_tz(G)).strftime("%Y-%m-%d %H:%M") == "2099-01-01 19:30")
i = inter(admin); run(B.raid_copy.callback(i, src, FUT, "19:30", title="Renamed"))
check("copy: title override", db.event(max(r["id"] for r in db._all("select id from events")))["title"] == "Renamed")
other = db.create_event(G, 21, "Raid 2 thing", "", int(time.time()) + 5000, 1, 0, 0, 0)
i = inter(leader1)
try:
    run(B.raid_copy.callback(i, other, FUT, "19:30")); denied = False
except app_commands.CheckFailure:
    denied = True
check("copy: channel leader can't copy another group's raid", denied)
i = inter(admin); run(B.raid_copy.callback(i, src, "garbage", "19:30"))
check("copy: bad date handled", "Couldn't read" in i.response.sent[0][0][0])
i = inter(admin); run(B.raid_copy.callback(i, 99999, FUT, "19:30"))
check("copy: unknown event handled", "not found" in i.response.sent[0][0][0])

# ---------------------------------------------------------------- repeat command
print("/raid repeat")
i = inter(admin); run(B.raid_repeat.callback(i, src, B.REPEAT_CHOICES[1]))
check("repeat weekly sets it", db.event(src)["repeat_days"] == 7 and "repeat every week" in i.response.sent[0][0][0])
i = inter(admin); run(B.raid_repeat.callback(i, src, B.REPEAT_CHOICES[0]))
check("repeat off clears it", db.event(src)["repeat_days"] == 0 and "no longer" in i.response.sent[0][0][0])
db.update_event(src, repeat_days=7, repeated=1)
i = inter(admin); run(B.raid_repeat.callback(i, src, B.REPEAT_CHOICES[0]))
check("already-spawned raid: told to change the upcoming one", db.event(src)["repeat_days"] == 7 and "already posted" in i.response.sent[0][0][0])

# ---------------------------------------------------------------- recurrence engine
print("recurrence")
LA = ZoneInfo("America/Los_Angeles")
db.set_setting(G, "timezone", "America/Los_Angeles")
sat = int(datetime(2026, 3, 7, 20, 0, tzinfo=LA).timestamp())          # the Saturday before DST starts
e = db.create_event(G, 11, "Saturday raid", "d", sat, 42, 1, 2, 3, 7)
db.set_message_id(e, 1); db.update_event(e, lock_shown=1)
sent = wire(CH[11])
real_time = time.time
time.time = lambda: sat + 120                                              # two minutes after it started
db.set_setting(G, "ping_role@11", 701)
run(B.spawn_next(db.event(e)))
time.time = real_time
nxt = [r for r in db._all("select * from events where title='Saturday raid' and id!=?", (e,))]
check("next occurrence created", len(nxt) == 1)
n = nxt[0]
check("same wall-clock time across DST (20:00 -> 20:00, 1h less in UTC)",
      datetime.fromtimestamp(n["start_ts"], LA).strftime("%Y-%m-%d %H:%M") == "2026-03-14 20:00" and n["start_ts"] - sat == 7 * 86400 - 3600,
      datetime.fromtimestamp(n["start_ts"], LA).strftime("%Y-%m-%d %H:%M"))
check("copies caps, creator, repeat; pings the channel role", (n["cap_tank"], n["cap_healer"], n["cap_dps"], n["creator_id"], n["repeat_days"]) == (1, 2, 3, 42, 7) and sent[0]["content"] == "<@&701>")
db.set_setting(G, "timezone", None)

e2 = db.create_event(G, 11, "Long gap", "", 1_000_000, 1, 0, 0, 0, 7)       # bot was off for years
db.set_message_id(e2, 1); db.update_event(e2, lock_shown=1)
run(B.spawn_next(db.event(e2)))
g = [r for r in db._all("select * from events where title='Long gap' and id!=?", (e2,))]
check("after a long outage it skips to the next FUTURE occurrence (no flood)", len(g) == 1 and g[0]["start_ts"] > time.time())

# loop: due_repeat -> exactly once
now = int(time.time())
db._run("DELETE FROM events")
r = db.create_event(G, 11, "Loop raid", "", now - 60, 1, 0, 0, 0, 7)
db.set_message_id(r, 1); db.update_event(r, lock_shown=1, reminded=1)
wire(CH[11])
run(B.reminder_loop.coro())
check("loop posts the next occurrence", db._one("select count(*) c from events where title='Loop raid'")["c"] == 2)
check("original is marked repeated", db.event(r)["repeated"] == 1)
run(B.reminder_loop.coro())
check("second loop tick doesn't duplicate", db._one("select count(*) c from events where title='Loop raid'")["c"] == 2)
nr = db._one("select * from events where title='Loop raid' and id!=?", (r,))
check("the new one chains (repeat kept, not yet repeated)", nr["repeat_days"] == 7 and nr["repeated"] == 0)
db.update_event(nr["id"], repeat_days=0)
check("turning repeat off stops the chain", not any(x["id"] == nr["id"] for x in db.due_repeat(now + 99999999)))
CH.pop(11).send = None
CH[11] = text(11, "raid-1-signup", 10); CH[11].guild = GUILD
orig = B.bot.fetch_channel
async def gone(cid): raise discord.NotFound(MagicMock(status=404, reason="x"), "gone")
B.bot.fetch_channel = gone
B.bot.get_channel = lambda cid: CH.get(cid) if cid != 555 else None
dead = db.create_event(G, 555, "Dead channel", "", now - 60, 1, 0, 0, 0, 7)
db.set_message_id(dead, 1); db.update_event(dead, lock_shown=1, reminded=1)
run(B.reminder_loop.coro())
check("deleted channel: no crash, marked so it won't retry every minute", db.event(dead)["repeated"] == 1 and db._one("select count(*) c from events where title='Dead channel'")["c"] == 1)
B.bot.fetch_channel = orig
B.bot.get_channel = lambda cid: CH.get(cid)

# ---------------------------------------------------------------- archive
print("cleanup of old posts")
db._run("DELETE FROM events")
wire(CH[11])
old_e = db.create_event(G, 11, "Ten days ago", "", now - 10 * 86400, 1, 0, 0, 0)
new_e = db.create_event(G, 11, "Three days ago", "", now - 3 * 86400, 1, 0, 0, 0)
for x in (old_e, new_e):
    db.set_message_id(x, 77); db.update_event(x, lock_shown=1, reminded=1)
th = MagicMock(spec=discord.Thread); th.id = 888; deleted = []
th.delete = MagicMock(side_effect=lambda: (deleted.append("thread"), _noop())[1])
CH[888] = th
db.update_event(old_e, thread_id=888)
run(B.reminder_loop.coro())
check("setting off (default): nothing deleted", db.event(old_e)["archived"] == 0)
db.set_setting(G, "archive_days", 7)
run(B.reminder_loop.coro())
check("older than N days: archived and its post deleted", db.event(old_e)["archived"] == 1 and CH[11].get_partial_message.called)
check("its thread deleted too", deleted == ["thread"])
check("newer than N days untouched", db.event(new_e)["archived"] == 0)
check("database record kept after cleanup", db.event(old_e) is not None)
CH[11].get_partial_message.reset_mock()
run(B.reminder_loop.coro())
check("archived posts aren't processed again", not CH[11].get_partial_message.called)

print("/raid cancel removes the thread")
c1 = db.create_event(G, 11, "Cancel me", "", now + 9999, 1, 0, 0, 0)
db.set_message_id(c1, 78); db.update_event(c1, thread_id=888)
deleted.clear()
i = inter(admin); run(B.raid_cancel.callback(i, c1))
check("event gone, thread deleted", db.event(c1) is None and deleted == ["thread"])

# ---------------------------------------------------------------- config + template commands
print("config and template commands")
i = inter(admin); run(B.config_threads.callback(i, True))
check("/config threads on", db.setting(G, "threads") == "1")
i = inter(admin); run(B.config_archive.callback(i, 14))
check("/config archive 14", db.setting(G, "archive_days") == "14")
i = inter(admin); run(B.config_show.callback(i))
txt = i.response.sent[0][0][0]
check("/config show lists threads + archive", "Discussion threads:** on" in txt and "14 days" in txt, txt)
i = inter(admin); run(B.config_archive.callback(i, 0))
check("/config archive 0 turns it off", db.setting(G, "archive_days") == "0")

s1 = db.create_event(G, 11, "Template source", "dd", now + 9999, 1, 1, 2, 3, 14)
i = inter(admin); run(B.template_save.callback(i, "  Weekly Run  ", s1))
tt = db.template(G, "weekly run")
check("template save from a raid", tt and (tt["title"], tt["cap_dps"], tt["repeat_days"]) == ("Template source", 3, 14))
i = inter(admin); run(B.template_list.callback(i))
check("template list shows it", "Weekly Run" in i.response.sent[0][0][0] and "weekly" not in "" and "every 2 weeks" in i.response.sent[0][0][0])
other = db.create_event(G, 21, "Raid2", "", now + 9999, 1, 0, 0, 0)
i = inter(leader1)
try:
    run(B.template_save.callback(i, "Steal", other)); denied = False
except app_commands.CheckFailure:
    denied = True
check("leader can't save a template from another group's raid", denied and db.template(G, "Steal") is None)
i = inter(admin); run(B.template_delete.callback(i, "weekly run"))
check("template delete", db.template(G, "weekly run") is None)
i = inter(admin); run(B.template_list.callback(i))
check("empty list handled", "No templates" in i.response.sent[0][0][0] or "Naxx" in i.response.sent[0][0][0])
check("template group gate: pleb blocked", run(B.template.interaction_check(inter(pleb))) is False)
check("template group gate: leader allowed", run(B.template.interaction_check(inter(leader1))) is True)

ac = run(B.template_autocomplete(inter(admin), "nax"))
check("template autocomplete", [c.value for c in ac] == ["Naxx"])

# ---------------------------------------------------------------- misc
print("misc")
check("invite link asks for thread permissions", checks.PERMISSIONS & (1 << 35) and checks.PERMISSIONS & (1 << 38))
check("embed footer: no repeat marker when off", "🔁" not in E.build_embed(db.event(s1) if False else db.event(new_e), [], False).footer.text)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
