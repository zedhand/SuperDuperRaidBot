"""Offline tests for v1.5. Run: python -I tests/test_v15.py"""
import asyncio
import os
import sqlite3
import sys
import tempfile
import time
import types
from pathlib import Path

proj = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["DB_PATH"] = str(tmp / "t.db")
os.environ["WOW_VERSION"] = "retail"
os.chdir(proj)
sys.path.insert(0, str(proj))

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


# ---------- 1. migration from a v1.3-era database ----------
print("migration")
old = tmp / "old.db"
c = sqlite3.connect(old)
c.executescript("""
CREATE TABLE characters (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
  wow_class TEXT NOT NULL, spec TEXT NOT NULL, role TEXT NOT NULL, is_main INTEGER NOT NULL DEFAULT 0, UNIQUE(user_id,name));
CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
  message_id INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', start_ts INTEGER NOT NULL,
  creator_id INTEGER NOT NULL, cap_tank INTEGER NOT NULL DEFAULT 0, cap_healer INTEGER NOT NULL DEFAULT 0,
  cap_dps INTEGER NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0, reminded INTEGER NOT NULL DEFAULT 0,
  lock_shown INTEGER NOT NULL DEFAULT 0);
CREATE TABLE signups (event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE, user_id INTEGER NOT NULL,
  char_name TEXT, wow_class TEXT, spec TEXT, role TEXT, status TEXT NOT NULL,
  updated_ts INTEGER NOT NULL DEFAULT (strftime('%s','now')), PRIMARY KEY(event_id,user_id));
INSERT INTO characters(user_id,name,wow_class,spec,role,is_main) VALUES (1,'Thrall','Shaman','Enhancement','dps',1);
INSERT INTO events(guild_id,channel_id,message_id,title,start_ts,creator_id) VALUES (9,8,7,'Naxx',4102444800,1);
INSERT INTO signups(event_id,user_id,char_name,wow_class,spec,role,status) VALUES (1,1,'Thrall','Shaman','Enhancement','dps','in');
""")
c.commit(); c.close()
import db as dbmod
d = dbmod.DB(str(old))
cols = {r[1] for r in d.conn.execute("PRAGMA table_info(signups)")}
check("bench_from column added", "bench_from" in cols)
check("settings table exists", d._one("SELECT name FROM sqlite_master WHERE name='settings'") is not None)
check("old data kept", d.character_by_name(1, "Thrall") is not None and d.signup(1, 1)["status"] == "in")
check("old signup has no bench_from", d.signup(1, 1)["bench_from"] is None)
dbmod.DB(str(old))  # second open must not fail
check("reopening is safe", True)

# ---------- 2. import bot, command registration ----------
print("bot import / commands")
import bot as B
raid_cmds = {c.name for c in B.raid.commands}
check("raid commands", {"create", "edit", "roster", "close", "cancel", "remove", "unsigned"} <= raid_cmds, raid_cmds)
check("config commands", {"show", "channel", "timezone", "reminder", "officer", "pingrole"} <= {c.name for c in B.config.commands})
check("raid group has no default_permissions (officer role works)", B.raid.default_permissions is None)

# ---------- 3. bench promotion ----------
print("bench promotion")
dm = []


class FakeUser:
    def __init__(self, uid): self.uid = uid
    async def send(self, text): dm.append((self.uid, text))


async def fake_fetch(uid): return FakeUser(uid)
B.bot.fetch_user = fake_fetch
B.db = dbmod.DB(str(tmp / "p.db"))
db = B.db
G = 100
FUTURE = int(time.time()) + 86400
eid = db.create_event(G, 1, "Test", "", FUTURE, 1, 1, 0, 0)  # 1 tank cap


def signup(uid, cls, spec, status="in"):
    ev = db.event(eid)
    role = B.wd.role_for(cls, spec)
    st, bf = B.apply_cap(ev, uid, role, status)
    db.set_signup(eid, uid, f"U{uid}", cls, spec, role, st, bf)
    return st


check("first tank is in", signup(1, "Warrior", "Protection") == "in")
check("second tank is auto-benched", signup(2, "Paladin", "Protection") == "bench")
check("auto-bench remembers request", db.signup(eid, 2)["bench_from"] == "in")
check("third tank (late) benched, remembers late", signup(3, "Druid", "Guardian", "late") == "bench" and db.signup(eid, 3)["bench_from"] == "late")
# manual bench must NOT be promoted
st, bf = B.apply_cap(db.event(eid), 4, "dps", "bench")
db.set_signup(eid, 4, "U4", "Mage", "Fire", "dps", "bench", None)
asyncio.run(B.promote_bench(eid))
check("full role: nobody promoted", db.signup(eid, 2)["status"] == "bench")
db.set_signup(eid, 1, None, None, None, None, "absent")  # tank leaves
asyncio.run(B.promote_bench(eid))
check("first benched tank promoted (signup order)", db.signup(eid, 2)["status"] == "in")
check("only one slot -> second still benched", db.signup(eid, 3)["status"] == "bench")
check("promoted player DM'd", dm and dm[0][0] == 2, dm)
check("bench_from cleared after promotion", db.signup(eid, 2)["bench_from"] is None)
check("manually benched dps untouched", db.signup(eid, 4)["status"] == "bench")
db.update_event(eid, cap_tank=2)
asyncio.run(B.promote_bench(eid))
check("raising the cap promotes next (keeps 'late')", db.signup(eid, 3)["status"] == "late")
# user who benched themselves manually in a capped role shouldn't be promoted
db.set_signup(eid, 5, "U5", "Warrior", "Fury", "dps", "bench", None)
db.update_event(eid, cap_dps=0)
asyncio.run(B.promote_bench(eid))
check("manual bench stays benched even with room", db.signup(eid, 5)["status"] == "bench")
# locked event: no promotion
db.set_closed(eid, True)
db.set_signup(eid, 6, "U6", "Paladin", "Protection", "tank", "bench", "in")
db.set_signup(eid, 2, None, None, None, None, "absent")
asyncio.run(B.promote_bench(eid))
check("closed event: no promotion", db.signup(eid, 6)["status"] == "bench")
db.set_closed(eid, False)

# ---------- 4. edit / reminders ----------
print("edit and reminders")
db.update_event(eid, start_ts=FUTURE + 100, reminded=0, lock_shown=0, title="Renamed")
ev = db.event(eid)
check("update_event applies fields", ev["title"] == "Renamed" and ev["start_ts"] == FUTURE + 100)
db.update_event(eid, id=999, creator_id=5)  # disallowed columns must be ignored
check("update_event ignores unknown columns", db.event(eid)["creator_id"] == 1 and db.event(999) is None)
db.set_message_id(eid, 555)
check("pending_reminders lists it", any(e["id"] == eid for e in db.pending_reminders(int(time.time()))))
db.mark_reminded(eid)
check("reminded event not pending", not any(e["id"] == eid for e in db.pending_reminders(int(time.time()))))

# ---------- 5. settings ----------
print("settings")
check("default reminder from env", B.guild_reminder_minutes(G) == B.REMINDER_MINUTES)
db.set_setting(G, "reminder_minutes", 45)
check("per-guild reminder", B.guild_reminder_minutes(G) == 45 and B.guild_reminder_minutes(G + 1) == B.REMINDER_MINUTES)
db.set_setting(G, "timezone", "Europe/Paris")
check("per-guild timezone", B.guild_tz(G).key == "Europe/Paris" and B.guild_tz(G + 1) == B.TZ)
db.set_setting(G, "timezone", "Not/AZone")
check("bad stored timezone falls back", B.guild_tz(G) == B.TZ)
db.set_setting(G, "timezone", None)
check("clearing a setting works", db.setting(G, "timezone") is None)

# ---------- 6. officer check ----------
print("officer check")


def fake_inter(manage_events=False, admin=False, role_ids=()):
    perms = types.SimpleNamespace(manage_events=manage_events, administrator=admin)
    user = types.SimpleNamespace(guild_permissions=perms, roles=[types.SimpleNamespace(id=i) for i in role_ids])
    return types.SimpleNamespace(user=user, guild_id=G)


check("random member is not officer", not B.is_officer(fake_inter()))
check("Manage Events is officer", B.is_officer(fake_inter(manage_events=True)))
check("Administrator is officer", B.is_officer(fake_inter(admin=True)))
db.set_setting(G, "officer_role", 777)
check("officer role works", B.is_officer(fake_inter(role_ids=[1, 777])))
check("other role does not", not B.is_officer(fake_inter(role_ids=[1, 778])))
db.set_setting(G, "officer_role", None)
check("cleared officer role stops working", not B.is_officer(fake_inter(role_ids=[777])))

# ---------- 7. embeds ----------
print("embeds")
import embeds as E
ev = db.event(eid)
s = db.signups(eid)
emb = E.build_embed(ev, s, False)
check("embed renders", emb.title == "Renamed")
empty = db.create_event(G, 1, "Empty", "", FUTURE, 1, 2, 2, 5)
w = E.build_embed(db.event(empty), [], False)
text = " ".join(f.value for f in w.fields)
check("warns about missing tanks/healers", "Need 2 more tanks" in text and "Need 2 more healers" in text, text)
nocap = db.create_event(G, 1, "NoCap", "", FUTURE, 1, 0, 0, 0)
w = E.build_embed(db.event(nocap), [], False)
text = " ".join(f.value for f in w.fields)
check("uncapped: 'No tank signed up yet'", "No tank signed up yet" in text and "No healer signed up yet" in text)
w = E.build_embed(db.event(nocap), [], True)
check("no warnings once locked", "⚠️" not in " ".join(f.value for f in w.fields))
db.set_signup(nocap, 1, "T", "Warrior", "Protection", "tank", "in")
db.set_signup(nocap, 2, "H", "Priest", "Holy", "healer", "in")
w = E.build_embed(db.event(nocap), db.signups(nocap), False)
check("no warning when tank+healer present", "⚠️" not in " ".join(f.value for f in w.fields))
db.set_signup(nocap, 3, "T2", "Warrior", "Protection", "tank", "bench")
one = db.create_event(G, 1, "One", "", FUTURE, 1, 2, 1, 0)
db.set_signup(one, 1, "T", "Warrior", "Protection", "tank", "in")
t1 = " ".join(f.value for f in E.build_embed(db.event(one), db.signups(one), False).fields)
check("singular/plural + count: 'Need 1 more tank (1/2)', 'Need 1 more healer (0/1)'", "Need 1 more tank (1/2)" in t1 and "Need 1 more healer (0/1)" in t1, t1)
for f in E.build_embed(db.event(empty), [], False).fields:
    assert len(f.value) <= 1024
many = [{"user_id": i, "char_name": "N" * 12, "wow_class": "Mage", "spec": "Fire", "role": "dps", "status": "in"} for i in range(1, 200)]
check("200 signups: every field <= 1024 chars", all(len(f.value) <= 1024 for f in E.build_embed(db.event(empty), many, False).fields))

# ---------- 8. backups ----------
print("backups")
p = db.backup_daily()
check("backup created", p is not None and p.exists())
check("second backup same day skipped", db.backup_daily() is None)
folder = p.parent
for i in range(1, 12):
    (folder / f"raidbot-2020-01-{i:02d}.db").write_bytes(b"x")
db2 = dbmod.DB(str(tmp / "p.db"))
(folder / p.name).unlink()
db2.backup_daily(keep=7)
check("keeps only newest 7", len(list(folder.glob("raidbot-*.db"))) == 7, list(folder.glob("raidbot-*.db")))
check("backup is a valid database", sqlite3.connect(db2.backup_daily.__self__.path.parent / "backups" / sorted(os.listdir(folder))[-1]).execute("select count(*) from events").fetchone()[0] >= 1)

# ---------- 9. role label text ----------
check("DM wording names the role and the raid", "tank slot" in dm[0][1] and "Test" in dm[0][1], dm[0][1])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
