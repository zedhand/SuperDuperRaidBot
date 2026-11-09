"""Offline tests for v1.8.7 ("rock solid"): one group per audit finding.

R1/R2 scheduler survives errors and retries instead of losing work, R3 embed limits, R4 nudge re-armed,
R5 daylight-saving times, R6 database recovery and verified backups, R7 log file, R8 edit coalescing,
R9 character cap, S2 strict mentions, S3 template permissions, S4 admin-only /config and /status.

Run:  python -I tests/test_v187.py        (or python tests/run_all.py for every suite)
"""
import asyncio
import logging
import os
import re
import sqlite3
import sys
import tempfile
import time
import types
from pathlib import Path
from zoneinfo import ZoneInfo

proj = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["DB_PATH"] = str(tmp / "t.db")
os.environ["WOW_VERSION"] = "retail"
os.chdir(proj)
sys.path.insert(0, str(proj))

import discord  # noqa: E402
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
DAY = 86400


# ------------------------------------------------------------ fakes
class FakeResp:
    def __init__(self): self.sent = []; self.deferred = False
    async def send_message(self, *a, **k): self.sent.append((a, k))
    async def defer(self, **k): self.deferred = True
    def is_done(self): return bool(self.sent) or self.deferred


class FakeFollowup:
    def __init__(self): self.sent = []
    async def send(self, *a, **k): self.sent.append((a, k))


def member(uid, roles=(), admin=False, manage_guild=False):
    perms = types.SimpleNamespace(manage_events=False, administrator=admin, manage_guild=manage_guild)
    return types.SimpleNamespace(guild_permissions=perms, roles=[types.SimpleNamespace(id=r) for r in roles],
                                 id=uid, bot=False, mention=f"<@{uid}>", display_name=f"User{uid}")


class FakeChannel:
    """A channel that records sends and edits; set fail_send to an exception to make send() raise."""
    def __init__(self, cid):
        self.id, self.category_id, self.mention, self.name = cid, None, f"<#{cid}>", f"chan{cid}"
        self.sent, self.edits, self.fail_send, self.edit_delay = [], 0, None, 0
        self.guild = types.SimpleNamespace(id=G, me=object(), get_role=lambda r: None)

    async def send(self, *a, **k):
        if self.fail_send:
            raise self.fail_send
        self.sent.append((a, k))
        return types.SimpleNamespace(id=1, jump_url="http://x")

    def permissions_for(self, me):
        return types.SimpleNamespace(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)

    def get_partial_message(self, mid):
        chan = self

        class Msg:
            async def edit(self, **kw):
                if chan.edit_delay:
                    await asyncio.sleep(chan.edit_delay)
                chan.edits += 1
        return Msg()


CH = {11: FakeChannel(11), 12: FakeChannel(12)}
B.bot.get_channel = lambda cid: CH.get(cid)


def inter(user, ch=None):
    ch = ch or CH[11]
    return types.SimpleNamespace(user=user, guild_id=G, guild=ch.guild, channel=ch, channel_id=ch.id,
                                 response=FakeResp(), followup=FakeFollowup(), command=None)


def reply(i):
    """The text of the first reply sent through the response or the followup."""
    msgs = i.response.sent or i.followup.sent
    return (msgs[0][0][0] if msgs and msgs[0][0] else (msgs[0][1].get("content", "") if msgs else ""))


def new_event(title="Raid", start=None, channel=11, **upd):
    e = db.create_event(G, channel, title, "", start or NOW + DAY, 1, 0, 0, 0)
    db.set_message_id(e, 1000 + e)
    if upd:
        db.update_event(e, **upd)
    return e


admin = member(1, admin=True)
manager = member(4, manage_guild=True)
scoped = member(2, roles=[500])
pleb = member(3)
db.set_setting(G, "officer_role@11", 500)
db.set_setting(G, "timezone", "America/New_York")

# ------------------------------------------------------------ R6 database
print("R6 database: migrations are checked, not guessed")
old = tmp / "old.db"
con = sqlite3.connect(old)
con.executescript("""
CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  start_ts INTEGER NOT NULL, creator_id INTEGER NOT NULL, cap_tank INTEGER NOT NULL DEFAULT 0,
  cap_healer INTEGER NOT NULL DEFAULT 0, cap_dps INTEGER NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0,
  reminded INTEGER NOT NULL DEFAULT 0);
CREATE TABLE signups (event_id INTEGER NOT NULL, user_id INTEGER NOT NULL, char_name TEXT, wow_class TEXT, spec TEXT,
  role TEXT, status TEXT NOT NULL, updated_ts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (event_id, user_id));
INSERT INTO events(guild_id,channel_id,title,start_ts,creator_id) VALUES (1,11,'Old raid',123,9);
""")
con.commit()
con.close()
d_old = dbmod.DB(str(old))
cols = {r[1] for r in d_old.conn.execute("PRAGMA table_info(events)")} | {r[1] for r in d_old.conn.execute("PRAGMA table_info(signups)")}
check("an old database gets every new column", {"lock_shown", "repeat_days", "nudged", "no_show", "bench_from"} <= cols)
check("old data kept", d_old.event(1)["title"] == "Old raid")
check("schema version recorded", d_old.conn.execute("PRAGMA user_version").fetchone()[0] == len(dbmod.MIGRATIONS))
d_old.conn.close()
dbmod.DB(str(old)).conn.close()
check("opening it again is safe", True)

print("R6 database: damaged files")
good_dir = tmp / "recover"
good_dir.mkdir()
live = good_dir / "raidbot.db"
d1 = dbmod.DB(str(live))
d1.create_event(G, 11, "Precious raid", "", NOW + DAY, 1, 0, 0, 0)
backup = d1.backup_daily()
d1.conn.close()
check("a daily backup is written and healthy", backup is not None and dbmod.file_is_healthy(backup))
check("no half-written .tmp file is left behind", not list((good_dir / "backups").glob("*.tmp")))
live.write_bytes(b"this is not a database at all" * 50)
check("garbage is detected as corruption", not dbmod.file_is_healthy(live))
try:
    dbmod.DB(str(live))
    raised = None
except Exception as e:
    raised = e
check("opening a damaged file raises DatabaseCorrupt (not a bare sqlite error)", isinstance(raised, dbmod.DatabaseCorrupt), repr(raised))
d2 = dbmod.open_database(str(live))
check("open_database restores the newest healthy backup", d2.recovered_from == backup.name and d2.event(1)["title"] == "Precious raid")
check("the damaged file is kept, not deleted", len(list(good_dir.glob("raidbot.db.damaged-*"))) == 1)
d2.conn.close()

lonely = tmp / "nobackup"
lonely.mkdir()
bad = lonely / "raidbot.db"
bad.write_bytes(b"junk" * 100)
try:
    dbmod.open_database(str(bad))
    msg = ""
except SystemExit as e:
    msg = str(e)
check("damaged and no backup: stops with a plain explanation", "no healthy backup" in msg, msg)
check("  and leaves the file untouched", bad.read_bytes() == b"junk" * 100)

print("R6 database: a bad backup is never kept")
d3 = dbmod.DB(str(tmp / "b3.db"))
orig = dbmod.file_is_healthy
dbmod.file_is_healthy = lambda p: False
try:
    d3.backup_daily()
    err = None
except RuntimeError as e:
    err = e
dbmod.file_is_healthy = orig
check("a backup that fails verification raises and is discarded", err is not None
      and not list((tmp / "backups").glob("raidbot-*")) and not list((tmp / "backups").glob("*.tmp")))
snap = tmp / "snap.db"
d3.snapshot_to(snap)
check("snapshot_to makes a healthy copy", dbmod.file_is_healthy(snap))

# ------------------------------------------------------------ R3 embeds
print("R3 embed limits")
check("text_problem allows normal text", embeds.text_problem("Naxx", "bring flasks") is None)
check("text_problem rejects a 300-character title", "300" in (embeds.text_problem("x" * 300, "") or ""))
check("text_problem rejects a huge description", embeds.text_problem("ok", "y" * 2001) is not None)

big = db.create_event(G, 11, "T" * 256, "D" * 2000, NOW + DAY, 1, 0, 0, 0)
classes = list(B.wd.DATA)
for i in range(240):
    cls = classes[i % len(classes)]
    spec = list(B.wd.DATA[cls])[i % len(B.wd.DATA[cls])]
    role = B.wd.role_for(cls, spec)
    status = ("in", "in", "in", "late", "tentative", "bench", "absent")[i % 7]
    name = None if status == "absent" else "N" * 30 + f"{i % 100:02d}"
    db.set_signup(big, 100000000000000000 + i, name, cls if name else None, spec if name else None,
                  role if name else None, status)
emb = embeds.build_embed(db.event(big), db.signups(big), False)
raw = embeds._build(db.event(big), db.signups(big), False, 1024)
check("(the stress roster really would overflow without the budget)", len(raw) > 6000, len(raw))
check("worst-case post stays under Discord's 6000-character limit", len(emb) <= 6000, len(emb))
check("  every field is under 1024 characters", all(len(f.value) <= 1024 for f in emb.fields))
check("  title <= 256, description <= 4096", len(emb.title) <= 256 and len(emb.description) <= 4096)
check("  no @mention is ever cut in half", all(len(re.findall(r"<@", f.value)) == len(re.findall(r"<@\d+>", f.value)) for f in emb.fields))

small = db.create_event(G, 11, "Small raid", "hello", NOW + DAY, 1, 0, 0, 0)
db.set_signup(small, 7, "Thrall", "Shaman", "Enhancement", "dps", "in")
emb2 = embeds.build_embed(db.event(small), db.signups(small), False)
check("a normal post is not shortened", "…" not in "".join(f.value for f in emb2.fields) and emb2.title == "Small raid")

i = inter(admin)
run(B.raid_create.callback(i, "2030-01-15", "20:00", title="x" * 300))
check("/raid create refuses a too-long title with a real reason", "300 characters" in reply(i), reply(i))
i = inter(admin)
ev = new_event("Edit me")
run(B.raid_edit.callback(i, ev, description="z" * 2500))
check("/raid edit refuses a too-long description and changes nothing", "2500" in reply(i) and db.event(ev)["description"] == "")

# ------------------------------------------------------------ R5 DST
print("R5 daylight-saving times")
ny = ZoneInfo("America/New_York")
ts, why = B.local_to_ts(__import__("datetime").datetime(2026, 3, 8, 2, 30), ny)
check("a time that doesn't exist (spring forward) is refused", ts is None and "doesn't exist" in why, why)
ts, why = B.local_to_ts(__import__("datetime").datetime(2026, 11, 1, 1, 30), ny)
check("a time that happens twice (fall back) is refused", ts is None and "twice" in why, why)
ts, why = B.local_to_ts(__import__("datetime").datetime(2026, 11, 14, 20, 0), ny)
check("an ordinary time still works", why is None and isinstance(ts, int), (ts, why))
for h, m in ((1, 59), (3, 0)):
    ts, why = B.local_to_ts(__import__("datetime").datetime(2026, 3, 8, h, m), ny)
    check(f"{h:02d}:{m:02d} either side of the spring change is fine", ts is not None, why)
ts, why = B.local_to_ts(__import__("datetime").datetime(2026, 11, 1, 2, 0), ny)
check("02:00 after the autumn change is fine", ts is not None, why)
check("London's spring gap is caught too", B.local_to_ts(__import__("datetime").datetime(2026, 3, 29, 1, 30), ZoneInfo("Europe/London"))[0] is None)
ts, problem = B.parse_start_checked(G, "2027-03-14", "02:30")
check("parse_start_checked reports the reason", ts is None and "doesn't exist" in problem)
check("parse_start_checked keeps the old 'couldn't read' message", B.parse_start_checked(G, "banana", "20:00")[1] == B.BAD_TIME)
check("parse_start (old signature) returns None for a bad time", B.parse_start(G, "2027-03-14", "02:30") is None)

ev = new_event("DST edit", start=NOW + 40 * DAY)
before = db.event(ev)["start_ts"]
i = inter(admin)
run(B.raid_edit.callback(i, ev, date="2027-03-14", at="02:30"))
check("/raid edit refuses a non-existent time and leaves the raid alone", "doesn't exist" in reply(i) and db.event(ev)["start_ts"] == before, reply(i))

# ------------------------------------------------------------ R4 nudge re-armed
print("R4 moving a raid re-arms the 'who hasn't answered' ping")
ev = new_event("Moved", start=NOW + 3 * DAY, nudged=1, reminded=1, lock_shown=0)
i = inter(admin)
run(B.raid_edit.callback(i, ev, date="2030-06-15", at="20:00", notify=False))
e = db.event(ev)
check("nudged, reminded and lock are all re-armed", (e["nudged"], e["reminded"], e["lock_shown"]) == (0, 0, 0), dict(e))

# ------------------------------------------------------------ R1/R2 scheduler
print("R1/R2 scheduler: one failure doesn't stop the rest")
for t in ("signups", "events"):
    db.conn.execute(f"DELETE FROM {t}")
db.conn.commit()
for c in CH.values():
    c.sent.clear()
    c.fail_send = None
B._tries.clear()


def tick():
    run(B.reminder_loop.coro())


a = db.create_event(G, 11, "Alpha", "", NOW + 600, 1, 0, 0, 0)
b = db.create_event(G, 12, "Beta", "", NOW + 600, 1, 0, 0, 0)
for ev_id in (a, b):
    db.set_message_id(ev_id, 2000 + ev_id)
    db.update_event(ev_id, lock_shown=1)
    db.set_signup(ev_id, 50, "Thrall", "Shaman", "Enhancement", "dps", "in")
CH[11].fail_send = RuntimeError("boom (not a Discord error)")
tick()
check("the healthy raid still got its reminder", len(CH[12].sent) == 1 and db.event(b)["reminded"] == 1)
check("the failing raid is not marked reminded (it will be retried)", db.event(a)["reminded"] == 0)
CH[11].fail_send = None
tick()
check("next minute the retry succeeds and the reminder goes out once", len(CH[11].sent) == 1 and db.event(a)["reminded"] == 1)
tick()
check("and is not sent again", len(CH[11].sent) == 1)

CH[12].sent.clear()
c = db.create_event(G, 12, "Gamma", "", NOW + 600, 1, 0, 0, 0)
db.set_message_id(c, 3000 + c)
db.update_event(c, lock_shown=1)
db.set_signup(c, 51, "Jaina", "Mage", "Frost", "dps", "in")
CH[12].fail_send = discord.HTTPException(types.SimpleNamespace(status=500, reason="x"), "server error")
for _ in range(B.MAX_TRIES):
    tick()
check("a reminder that always fails is given up after MAX_TRIES (no endless retries)", db.event(c)["reminded"] == 1)
check("  and /status can say what went wrong", B.LAST_ERROR is not None and f"#{c}" in B.LAST_ERROR[1], B.LAST_ERROR)
CH[12].fail_send = None

print("R1 scheduler: a crash in one step doesn't skip the others")
d = db.create_event(G, 11, "Delta", "", NOW + 600, 1, 0, 0, 0)
db.set_message_id(d, 4000 + d)
db.update_event(d, lock_shown=1)
db.set_signup(d, 52, "Rexxar", "Hunter", "Marksmanship", "dps", "in")
CH[11].sent.clear()
db.due_lock = lambda now: (_ for _ in ()).throw(sqlite3.OperationalError("simulated database error"))
tick()
del db.due_lock
check("reminders still go out when the lock step blows up", len(CH[11].sent) == 1 and db.event(d)["reminded"] == 1)

print("R1 scheduler: the loop restarts itself if it ever does die")
started = []
real_loop = B.reminder_loop
B.reminder_loop = types.SimpleNamespace(is_running=lambda: False, start=lambda: started.append(1))
B._revive_scheduler()
B.reminder_loop = types.SimpleNamespace(is_running=lambda: True, start=lambda: started.append(2))
B._revive_scheduler()
B.reminder_loop = real_loop
check("restarts a stopped loop, leaves a running one alone", started == [1])
check("an error handler is registered on the loop", real_loop._error is B._scheduler_crashed if hasattr(real_loop, "_error") else True)

print("R2 recurring raids: a failed post is retried, not lost")
for t in ("signups", "events"):
    db.conn.execute(f"DELETE FROM {t}")
db.conn.commit()
B._tries.clear()
r = db.create_event(G, 11, "Weekly", "", NOW - 60, 1, 0, 0, 0, repeat_days=7)
db.set_message_id(r, 5000 + r)
db.update_event(r, lock_shown=1)
outcomes = [False, False, True]
calls = []


async def fake_spawn(event):
    calls.append(event["id"])
    return outcomes.pop(0)


real_spawn, B.spawn_next = B.spawn_next, fake_spawn
tick()
check("first failure: still scheduled to retry", db.event(r)["repeated"] == 0)
B._not_before.clear()  # the backoff wait has passed
tick()
check("second failure: still scheduled", db.event(r)["repeated"] == 0)
B._not_before.clear()
tick()
check("success: marked posted", db.event(r)["repeated"] == 1 and len(calls) == 3)
tick()
check("and never posted again", len(calls) == 3)
B.spawn_next = real_spawn

r2 = db.create_event(G, 11, "Weekly2", "", NOW - 60, 1, 0, 0, 0, repeat_days=7)
db.set_message_id(r2, 5100 + r2)
db.update_event(r2, lock_shown=1)
chan = CH[11]
CH[11] = None
B.bot.get_channel = lambda cid: CH.get(cid)


async def gone(cid):
    raise discord.NotFound(types.SimpleNamespace(status=404, reason="x"), "unknown channel")


real_fetch, B.bot.fetch_channel = B.bot.fetch_channel, gone
tick()
check("a deleted channel is final: no retries", db.event(r2)["repeated"] == 1)
B.bot.fetch_channel = real_fetch
CH[11] = chan

print("R2 locking: the post is only marked closed once the redraw worked")
for t in ("signups", "events"):
    db.conn.execute(f"DELETE FROM {t}")
db.conn.commit()
B._tries.clear()
lk = db.create_event(G, 11, "Locker", "", NOW - 5, 1, 0, 0, 0)
db.set_message_id(lk, 6000 + lk)
results = [False, True]


async def fake_refresh(event_id):
    return results.pop(0)


real_refresh, B.refresh_message = B.refresh_message, fake_refresh
tick()
check("redraw failed: lock not recorded, will retry", db.event(lk)["lock_shown"] == 0)
tick()
check("redraw worked: recorded", db.event(lk)["lock_shown"] == 1)
B.refresh_message = real_refresh

# ------------------------------------------------------------ R8 coalescing
print("R8 a rush of clicks is coalesced into a few edits")
ev = new_event("Rush")
CH[11].edits = 0
CH[11].edit_delay = 0.05


async def rush():
    await asyncio.gather(*(B.refresh_message(ev) for _ in range(30)))


run(rush())
check("30 simultaneous refreshes cost at most 2 edits", 1 <= CH[11].edits <= 2, CH[11].edits)
CH[11].edit_delay = 0
CH[11].edits = 0
run(B.refresh_message(ev))
run(B.refresh_message(ev))
check("sequential refreshes still each draw", CH[11].edits == 2)

# ------------------------------------------------------------ S2 mentions
print("S2 mentions")
check("USERS_ONLY blocks @everyone and roles", B.USERS_ONLY.everyone is False and B.USERS_ONLY.roles is False and B.USERS_ONLY.users is True)
mentions = B.mention_list("head ", list(range(100000000000000000, 100000000000000500)))
check("mention_list fits in a message and never cuts a mention",
      len(mentions) <= 2000 and mentions.endswith("more") and len(re.findall(r"<@", mentions)) == len(re.findall(r"<@\d+>", mentions)))
CH[11].sent.clear()
tick_ev = db.create_event(G, 11, "@everyone raid", "", NOW + 300, 1, 0, 0, 0)
db.set_message_id(tick_ev, 7000)
db.update_event(tick_ev, lock_shown=1)
db.set_signup(tick_ev, 60, "Anduin", "Priest", "Holy", "healer", "in")
tick()
sent_kw = CH[11].sent[-1][1] if CH[11].sent else {}
am = sent_kw.get("allowed_mentions")
check("the reminder can't ping @everyone even if the title says so", am is not None and am.everyone is False and am.roles is False)

# ------------------------------------------------------------ S3/S4 permissions
print("S3 templates are server-wide")
ev = new_event("Template source", channel=11)
i = inter(admin)
run(B.template_save.callback(i, "Naxx", ev))
check("an admin can save a template", db.template(G, "Naxx") is not None)
i = inter(scoped)
run(B.template_save.callback(i, "Naxx", ev))
check("a channel raid leader cannot overwrite an existing template", "already exists" in reply(i), reply(i))
i = inter(scoped)
run(B.template_delete.callback(i, "Naxx"))
check("a channel raid leader cannot delete one", db.template(G, "Naxx") is not None and "server-wide" in reply(i), reply(i))
i = inter(scoped)
run(B.template_save.callback(i, "Mine", ev))
check("but can still add their own new template", db.template(G, "Mine") is not None)
i = inter(admin)
run(B.template_delete.callback(i, "Naxx"))
check("an admin can delete", db.template(G, "Naxx") is None)

print("S4 /config and /status check permissions themselves")
i = inter(pleb)
check("/config refuses a regular member", run(B.config.interaction_check(i)) is False and "admins" in reply(i))
i = inter(manager)
check("/config allows Manage Server", run(B.config.interaction_check(i)) is True)
i = inter(admin)
check("/config allows Administrator", run(B.config.interaction_check(i)) is True)
i = inter(pleb)
run(B.status.callback(i))
check("/status refuses a regular member", "admins" in reply(i))
i = inter(admin)
B.note_error("something odd happened")
run(B.status.callback(i))
check("/status shows the last problem", "something odd happened" in reply(i), reply(i))

print("R9 character cap")
uid = 777
for n in range(B.MAX_CHARACTERS):
    db.upsert_character(uid, f"Char{n}", "Mage", "Fire", "dps", False)
i = inter(member(uid))
run(B.character_add.callback(i, "OneTooMany", "Mage", "Frost", False))
check("a 26th character is refused with an explanation", db.character_by_name(uid, "OneTooMany") is None and "25" in reply(i), reply(i))
i = inter(member(uid))
run(B.character_add.callback(i, "Char3", "Mage", "Frost", False))
check("changing an existing character still works at the cap", db.character_by_name(uid, "Char3")["spec"] == "Frost")

print("/config backup")
i = inter(admin)
run(B.config_backup.callback(i))
sent = i.followup.sent[0][1] if i.followup.sent else {}
f = sent.get("file")
check("sends the database as a private attachment", i.response.deferred and f is not None and f.filename.endswith(".db")
      and f.fp.read(16) == b"SQLite format 3\x00", sent)

# ------------------------------------------------------------ R7 logging, misc
print("R7 log file")
B.BASE_DIR = tmp
B.setup_logging()
B.log.warning("hello from the test")
for h in logging.getLogger().handlers:
    h.flush()
logfile = tmp / "raidbot.log"
check("setup_logging writes raidbot.log next to the bot", logfile.exists() and "hello from the test" in logfile.read_text(encoding="utf-8"))
for h in list(logging.getLogger().handlers):
    h.close()
    logging.getLogger().removeHandler(h)

print("housekeeping")
check("version string agrees everywhere", f"{B.VERSION})" in checks.USER_AGENT, checks.USER_AGENT)
bat = (proj / "start.bat").read_text(encoding="utf-8")
check("start.bat restarts only after a crash (exit code 3)", "errorlevel%==3" in bat.replace(" ", "") and B.CRASH_EXIT_CODE == 3)
gi = (proj / ".gitignore").read_text(encoding="utf-8")
check(".gitignore covers the log and damaged-database files", "raidbot.log" in gi and "damaged" in gi)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
