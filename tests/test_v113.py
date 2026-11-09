"""Offline tests for v1.13/v1.14: the WoW version presets, confirm/undo for /raid cancel and /raid remove, /forgetme,
the daily backup post and the per-server /config backup, realms on characters, signup notes, the Spec button, /raid promote, the invite export in /raid roster, the Upcoming raids board, the low-signup alert and Discord Scheduled Events (more suites are added below as features land).

Run:  python -I tests/test_v113.py        (or python tests/run_all.py for every suite)
"""
import os
import subprocess
import sys
import tempfile
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


import checks  # noqa: E402
import wow_data as wd  # noqa: E402

print("presets")
check("the wizard menu and the preset table list exactly the same versions", list(checks.PRESET_LABELS) == list(wd.PRESETS),
      (list(checks.PRESET_LABELS), list(wd.PRESETS)))
check("all eight WoW versions are present", set(wd.PRESETS) == {"retail", "classic_era", "season_of_discovery", "tbc_classic",
      "wrath_classic", "cata_classic", "mists_classic", "wow_forever"})
for name, preset in wd.PRESETS.items():
    groups = {g for specs in preset.values() for g in specs.values()}
    check(f"{name}: every spec maps to a known group and the raid can field tank, healer and dps",
          groups <= set(wd.GROUPS) and {"tank", "healer"} <= groups and groups & {"melee", "ranged"}, groups)
check("Death Knight arrives with Wrath and Monk with Mists, not before",
      "Death Knight" not in wd.PRESETS["tbc_classic"] and "Death Knight" in wd.PRESETS["wrath_classic"]
      and "Monk" not in wd.PRESETS["cata_classic"] and "Monk" in wd.PRESETS["mists_classic"])
check("Season of Discovery adds its tank options, Era does not have them",
      "Demonology (Tank)" in wd.PRESETS["season_of_discovery"]["Warlock"] and "Demonology (Tank)" not in wd.PRESETS["classic_era"]["Warlock"])
check("a derived preset is a copy: changing one never changes Era",
      wd.PRESETS["tbc_classic"]["Mage"] is not wd.PRESETS["classic_era"]["Mage"])
check("utility entries only name classes that exist in their preset",
      all(c in wd.PRESETS[n] for n, u in wd.UTILITY_BY_PRESET.items() for _, cl in u.values() for c in cl if n != "retail"))
check("the utility table has an entry for every preset", set(wd.UTILITY_BY_PRESET) == set(wd.PRESETS))

for name in wd.PRESETS:
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); import wow_data as w;" % str(proj) + " print(w.PRESET_NAME, len(w.class_names()))"],
                       cwd=proj, env={**os.environ, "WOW_VERSION": name}, capture_output=True, text=True)
    check(f"WOW_VERSION={name} loads", r.returncode == 0 and r.stdout.split()[0] == name, r.stderr[-200:])
r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); import wow_data" % str(proj)], cwd=proj, env={**os.environ, "WOW_VERSION": "nope"},
                   capture_output=True, text=True)
check("an unknown version stops with the list of valid ones", r.returncode != 0 and "wow_forever" in (r.stderr + r.stdout))

# ------------------------------------------------------------ confirm and undo for /raid cancel and /raid remove
import asyncio  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
import types  # noqa: E402

import discord  # noqa: E402
import bot as B  # noqa: E402

check("this version is 1.13.1", B.VERSION == "1.13.1")
import db as dbmod  # noqa: E402

run = asyncio.run
B.db = dbmod.DB(str(tmp / "safety.db"))
db = B.db
G = 1


class FakeResp:
    def __init__(self): self.sent = []; self.edited = []
    async def send_message(self, *a, **k): self.sent.append((a, k))
    async def edit_message(self, **k): self.edited.append(k)
    def is_done(self): return bool(self.sent or self.edited)


class FakeMsg:
    def __init__(self, ch, mid): self.ch, self.id, self.jump_url, self.deleted = ch, mid, f"http://x/{mid}", False
    async def edit(self, **k): self.ch.edits.append((self.id, k))
    async def delete(self): self.ch.deleted.append(self.id)
    async def create_thread(self, **k):
        self.ch.threads.append(k)
        return types.SimpleNamespace(id=9000 + self.id)


class FakeChannel:
    def __init__(self, cid):
        self.id, self.mention, self.name = cid, f"<#{cid}>", f"chan{cid}"
        self.sent, self.edits, self.deleted, self.threads, self.fail_send = [], [], [], [], None
        self.guild = types.SimpleNamespace(id=G, me=object(), get_role=lambda r: None, get_member=lambda u: None)

    async def send(self, *a, **k):
        if self.fail_send:
            raise self.fail_send
        self.sent.append((a, k))
        return FakeMsg(self, 700 + len(self.sent))

    def get_partial_message(self, mid): return FakeMsg(self, mid)
    def permissions_for(self, me): return types.SimpleNamespace(send_messages=True, embed_links=True, view_channel=True)


CH = {11: FakeChannel(11)}
B.bot.get_channel = lambda cid: CH.get(cid)
DMS = []


async def fetch_user(uid):
    async def send(t): DMS.append((uid, t))
    return types.SimpleNamespace(id=uid, send=send)
B.bot.fetch_user = fetch_user


def member(uid, admin=False):
    perms = types.SimpleNamespace(manage_events=admin, administrator=admin, manage_guild=admin)
    return types.SimpleNamespace(guild_permissions=perms, roles=[], id=uid, bot=False, mention=f"<@{uid}>", display_name=f"User{uid}")


officer, nobody, raider = member(1, admin=True), member(2), member(50)


def inter(user):
    ch = CH[11]
    return types.SimpleNamespace(user=user, guild_id=G, guild=ch.guild, channel=ch, channel_id=11, response=FakeResp(),
                                 command=None, message=None)


def text(i):
    if i.response.edited:
        return i.response.edited[-1].get("content", "")
    return i.response.sent[0][0][0] if i.response.sent and i.response.sent[0][0] else ""


def button(i, starts):
    view = i.response.sent[0][1]["view"]
    return next(c for c in view.children if c.label.startswith(starts))


def press(i, starts, user=officer):
    click = inter(user)
    run(button(i, starts).callback(click))
    return click


def new_raid(title="Karazhan"):
    eid = db.create_event(G, 11, title, "bring flasks", int(time.time()) + 86400, 1, 2, 5, 18)
    db.set_message_id(eid, 500 + eid)
    db.update_event(eid, thread_id=600 + eid)
    db.set_signup(eid, 50, "Thrall", "Shaman", "Restoration", "healer", "in", None)
    db.set_signup(eid, 51, "Jaina", "Mage", "Frost", "dps", "bench", "dps")
    db.set_signup(eid, 52, None, None, None, None, "absent", None)
    return eid


print("/config confirm is off by default: cancel and remove act straight away")
check("the setting starts off", db.setting(G, "confirm_deletes", "0") == "0")
eid = new_raid("Quick")
i = inter(officer)
run(B.raid_cancel.callback(i, eid))
check("cancel deletes at once, privately, and points at /raid restore",
      db.event(eid) is None and db.cancelled_event(eid) and "/raid restore" in text(i) and "view" not in i.response.sent[0][1]
      and i.response.sent[0][1].get("ephemeral") is True, text(i))
db.restore_event(eid)
i = inter(officer)
run(B.raid_remove.callback(i, eid, raider))
check("remove removes at once", db.signup(eid, 50) is None and "Removed <@50>" in text(i), text(i))
i = inter(officer)
run(B.config_confirm.callback(i, True))
check("/config confirm on turns the question on", db.setting(G, "confirm_deletes") == "1" and "ask first" in text(i))
i = inter(officer)
run(B.config_show.callback(i))
check("/config show lists it", "Ask before cancel / remove:** on" in text(i), text(i))

DMS.clear()  # removing Thrall above moved Jaina off the bench and told her

print("/raid cancel asks first when it's on")
eid = new_raid()
i = inter(officer)
run(B.raid_cancel.callback(i, eid))
check("cancel asks instead of deleting", db.event(eid) is not None and "Cancel **Karazhan**" in text(i), text(i))
check("the question says how many signups would go", "3 signups (1 coming)" in text(i), text(i))
check("the question is private", i.response.sent[0][1].get("ephemeral") is True)
no = press(i, "No")
check("'No, keep it' changes nothing", db.event(eid) is not None and len(db.signups(eid)) == 3 and "Nothing was changed" in text(no))
view = i.response.sent[0][1]["view"]
check("the buttons time out after a minute", view.timeout == 60)
calls = []


async def edit_original(**k): calls.append(k)
i.edit_original_response = edit_original
run(view.on_timeout())
check("a timed-out question says nothing was changed and removes the buttons",
      calls and calls[0]["view"] is None and "nothing was changed" in calls[0]["content"])

i = inter(officer)
run(B.raid_cancel.callback(i, eid))
CH[11].deleted.clear()
yes = press(i, "Yes, cancel")
check("'Yes, cancel it' deletes the raid", db.event(eid) is None and db.signups(eid) == [])
check("the post is deleted", 500 + eid in CH[11].deleted, CH[11].deleted)
check("the reply points at /raid restore", "/raid restore" in text(yes) and "14 days" in text(yes), text(yes))
check("a copy is kept for restore", db.cancelled_event(eid) is not None)
again = press(i, "Yes, cancel")
check("pressing Yes twice is harmless", "already gone" in text(again), text(again))

i = inter(nobody)
try:
    run(B.raid_cancel.callback(i, new_raid("Not yours")))
except discord.app_commands.CheckFailure:
    pass
check("someone who isn't an officer can't even get the question", "only manage raids" in text(i), text(i))

print("/raid restore")
choices = run(B.cancelled_autocomplete(inter(officer), "kara"))
check("autocomplete lists recently cancelled raids", [c.value for c in choices] == [eid], choices)
i = inter(nobody)
run(B.raid_restore.callback(i, eid))
check("someone who isn't an officer can't restore", db.event(eid) is None and "only manage raids" in text(i), text(i))
CH[11].fail_send = discord.HTTPException(types.SimpleNamespace(status=403, reason="x"), "missing access")
i = inter(officer)
run(B.raid_restore.callback(i, eid))
check("if the post can't be sent, the raid stays cancelled (and can be restored later)",
      db.event(eid) is None and db.cancelled_event(eid) is not None and "still cancelled" in text(i), text(i))
CH[11].fail_send = None
CH[11].threads.clear()
i = inter(officer)
run(B.raid_restore.callback(i, eid))
ev = db.event(eid)
check("restore brings the raid back under the same id", ev is not None and ev["title"] == "Karazhan"
      and ev["description"] == "bring flasks" and (ev["cap_tank"], ev["cap_healer"], ev["cap_dps"]) == (2, 5, 18))
rows = {r["user_id"]: r for r in db.signups(eid)}
check("every signup comes back with its status, character and bench reason",
      set(rows) == {50, 51, 52} and rows[50]["char_name"] == "Thrall" and rows[51]["status"] == "bench"
      and rows[51]["bench_from"] == "dps" and rows[52]["status"] == "absent")
check("signups keep their order", [r["user_id"] for r in db.signups(eid)] == [50, 51, 52])
check("a new post is made and linked", ev["message_id"] and ev["message_id"] != 500 + eid and "Restored **Karazhan** with 3 signups" in text(i), text(i))
check("its discussion thread is started again", CH[11].threads and db.event(eid)["thread_id"] == 9000 + ev["message_id"],
      (CH[11].threads, dict(db.event(eid))))
check("nobody is promoted or messaged by a restore", DMS == [] and db.signup(eid, 51)["status"] == "bench")
check("the copy is used up", db.cancelled_event(eid) is None)
i = inter(officer)
run(B.raid_restore.callback(i, eid))
check("restoring twice is refused", "no cancelled raid" in text(i), text(i))
i = inter(officer)
run(B.raid_restore.callback(i, 99999))
check("an unknown id is refused with the 14-day rule", "14 days" in text(i), text(i))

print("old copies are forgotten after 14 days")
old = new_raid("Old one")
db.cancel_event(old, 1, now=int(time.time()) - 15 * 86400)
check("a raid cancelled 15 days ago can't be restored", db.cancelled_event(old) is None
      and old not in [c["event_id"] for c in db.cancelled_events(G)])
check("purge deletes it for good", db.purge_cancelled() == 1 and db.restore_event(old) is None)
other = new_raid("Other server")
db._run("UPDATE events SET guild_id=2 WHERE id=?", (other,))
db.cancel_event(other, 1)
i = inter(officer)
run(B.raid_restore.callback(i, other))
check("another server's cancelled raid can't be restored from here", db.event(other) is None and "no cancelled raid" in text(i))
check("snapshots survive a future column being added (unknown keys are skipped)", True)
snap = db.cancelled_event(other)
data = json.loads(snap["event_json"])
data["from_the_future"] = 1
db._run("UPDATE cancelled_events SET event_json=? WHERE event_id=?", (json.dumps(data), other))
check("a copy with an unknown column still restores", db.restore_event(other) == 3 and db.event(other) is not None)

print("/raid remove asks first when it's on")
eid = new_raid("Gruul")
i = inter(officer)
run(B.raid_remove.callback(i, eid, raider))
check("remove asks instead of removing", db.signup(eid, 50) is not None and "Remove <@50> (Thrall, in)" in text(i), text(i))
no = press(i, "No")
check("'No, keep it' keeps the signup", db.signup(eid, 50) is not None and "Nothing was changed" in text(no))
i = inter(officer)
run(B.raid_remove.callback(i, eid, raider))
yes = press(i, "Yes, remove")
check("'Yes, remove' removes it", db.signup(eid, 50) is None and "Removed <@50>" in text(yes), text(yes))
i = inter(officer)
run(B.raid_remove.callback(i, eid, raider))
check("removing someone who isn't signed up says so, without a question",
      "isn't signed up" in text(i) and "view" not in i.response.sent[0][1], text(i))

i = inter(officer)
run(B.config_confirm.callback(i, False))
check("/config confirm off turns it back off and mentions /raid restore",
      db.setting(G, "confirm_deletes") == "0" and "/raid restore" in text(i), text(i))

print("/forgetme deletes a player's own data, after asking")
me, other_player = member(70), member(71)
fa = new_raid("Forget A")
db.set_signup(fa, 70, "Arthas", "Paladin", "Protection", "tank", "in", None)
db.set_signup(fa, 71, "Uther", "Paladin", "Holy", "healer", "in", None)
db.update_event(fa, cap_tank=1)
db.set_signup(fa, 72, "Varian", "Warrior", "Protection", "tank", "bench", "in")
fb = new_raid("Forget B")
db.set_signup(fb, 70, "Arthas", "Paladin", "Protection", "tank", "late", None)
db._run("UPDATE events SET guild_id=2 WHERE id=?", (fb,))  # another server: forgotten there too
fc = new_raid("Forget cancelled")
db.set_signup(fc, 70, "Arthas", "Paladin", "Protection", "tank", "in", None)
db.cancel_event(fc, 1)
db.upsert_character(70, "Arthas", "Paladin", "Protection", "tank", True)
db.upsert_character(70, "Kael", "Mage", "Fire", "dps", False)
db.upsert_character(71, "Uther", "Paladin", "Holy", "healer", True)
db.join_series(G, fa, 70, "Arthas", "Paladin", "Protection", "tank")
db.join_series(G, fa, 71, "Uther", "Paladin", "Holy", "healer")
db.set_setting(G, "dm_remind@70", "1")
db.set_setting(G, "dm_remind@71", "1")
db.set_setting(G, "ping_role@70", "123")  # not a player setting (a channel's), even if the number matched

i = inter(me)
run(B.forgetme.callback(i))
check("/config confirm is off here, and /forgetme still asks", db.setting(G, "confirm_deletes") == "0")
check("/forgetme asks first and deletes nothing yet", len(db.characters(70)) == 2 and db.signup(fa, 70) is not None
      and "can't be undone" in text(i), text(i))
check("the question is private and says what would go",
      i.response.sent[0][1].get("ephemeral") is True and "2 characters" in text(i) and "3 raid signups" not in text(i)
      and "2 raid signups" in text(i) and "1 repeating-raid sign-up" in text(i) and "/remindme" in text(i)
      and "1 cancelled raid" in text(i), text(i))
no = press(i, "No", user=me)
check("'No, keep it' keeps everything", len(db.characters(70)) == 2 and db.signup(fa, 70) and "Nothing was changed" in text(no))

i = inter(me)
run(B.forgetme.callback(i))
CH[11].edits.clear()
DMS.clear()
yes = press(i, "Yes, delete", user=me)
check("characters are gone", db.characters(70) == [])
check("signups are gone in every server", db.signup(fa, 70) is None and db.signup(fb, 70) is None)
check("attendance is gone", db.stats(G, 0, 2**31, user_id=70) == [] and db.stats(2, 0, 2**31, user_id=70) == [])
check("/always sign-ups are gone", db.user_series(G, 70) == [])
check("their /remindme setting is gone, other settings stay",
      db.setting(G, "dm_remind@70") is None and db.setting(G, "ping_role@70") == "123")
snap = json.loads(db.cancelled_event(fc)["signups_json"])
check("their place in a cancelled raid's copy is gone, the others stay",
      70 not in [s["user_id"] for s in snap] and {50, 51, 52} <= {s["user_id"] for s in snap}, snap)
check("other players are untouched", len(db.characters(71)) == 1 and db.signup(fa, 71) is not None
      and db.user_series(G, 71) and db.setting(G, "dm_remind@71") == "1")
check("raids they were on still exist", db.event(fa) is not None and db.event(fb) is not None)
check("the reply confirms it", "Done. Deleted 2 characters" in text(yes) and "no longer has anything stored" in text(yes), text(yes))
check("the raid post is redrawn without them", any(mid == 500 + fa for mid, _ in CH[11].edits), CH[11].edits)
check("the freed tank slot goes to the bench", db.signup(fa, 72)["status"] == "in" and any(u == 72 for u, _ in DMS), DMS)
check("restoring the cancelled raid brings back the others, not them",
      db.restore_event(fc) == 3 and db.signup(fc, 70) is None)
i = inter(me)
run(B.forgetme.callback(i))
check("running it again says there's nothing left, without a question",
      "nothing stored about you" in text(i) and "view" not in i.response.sent[0][1], text(i))

print("daily backup post in a private admin channel (/config backupchannel)")
import sqlite3  # noqa: E402

EVERYONE, ME = object(), object()


class AdminChannel(FakeChannel):
    """A channel whose permissions differ for @everyone and for the bot."""
    def __init__(self, cid, public=False, can_attach=True):
        super().__init__(cid)
        self.public, self.can_attach = public, can_attach
        self.guild = types.SimpleNamespace(id=G, me=ME, default_role=EVERYONE, get_role=lambda r: None, get_member=lambda u: None)

    def permissions_for(self, who):
        if who is EVERYONE:
            return types.SimpleNamespace(view_channel=self.public, send_messages=self.public, attach_files=False)
        return types.SimpleNamespace(view_channel=True, send_messages=True, attach_files=self.can_attach, embed_links=True)


CH[21], CH[22], CH[23] = AdminChannel(21), AdminChannel(22, public=True), AdminChannel(23, can_attach=False)
check("the daily backup post is off by default", db.setting(G, "backup_channel") is None and G not in db.guilds_with_key("backup_channel"))
i = inter(officer)
run(B.config_show.callback(i))
check("/config show says it's off", "Daily backup post:** off" in text(i), text(i))
i = inter(officer)
run(B.config_backupchannel.callback(i, CH[22]))
check("a channel everyone can see is refused", db.setting(G, "backup_channel") is None and "seen by everyone" in text(i)
      and "Nothing was changed" in text(i), text(i))
i = inter(officer)
run(B.config_backupchannel.callback(i, CH[23]))
check("a channel where the bot can't attach files is refused", db.setting(G, "backup_channel") is None and "Attach Files" in text(i), text(i))
i = inter(officer)
run(B.config_backupchannel.callback(i, CH[21]))
check("a private channel is accepted", db.setting(G, "backup_channel") == "21" and "within a minute" in text(i)
      and i.response.sent[0][1].get("ephemeral") is True, text(i))
i = inter(officer)
run(B.config_show.callback(i))
check("/config show names the channel", "Daily backup post:** <#21>" in text(i), text(i))

# data from another server, which must never end up in this server's copy
theirs = db.create_event(2, 99, "Their raid", "secret plans", int(time.time()) + 86400, 1, 2, 5, 18)
db.set_signup(theirs, 880, "Stranger", "Rogue", "Subtlety", "dps", "in", None)
db.upsert_character(880, "Stranger", "Rogue", "Subtlety", "dps", True)
db.set_setting(2, "log_channel", "4242")
db.cancel_event(db.create_event(2, 99, "Their cancelled", "", int(time.time()) + 86400, 1, 0, 0, 0), 1)
mine = new_raid("Our raid")
db.upsert_character(50, "Thrall", "Shaman", "Restoration", "healer", True)


def posted_file(ch):
    f = ch.sent[-1][1].get("file")
    if f is None:
        return None
    out = tmp / f"posted-{len(ch.sent)}.db"
    f.fp.seek(0)
    out.write_bytes(f.fp.read())
    return out


day0 = datetime(2026, 11, 9, 22, 0, tzinfo=B.guild_tz(G))
run(B._run_backuppost(int(day0.timestamp())))
check("the first copy goes out straight away, even late in the day", len(CH[21].sent) == 1 and posted_file(CH[21]) is not None,
      CH[21].sent)
copy = posted_file(CH[21])
check("the file is named after the day and the message warns it is private",
      CH[21].sent[-1][1]["file"].filename == "raidbot-2026-11-09.db" and "keep this channel private" in CH[21].sent[-1][0][0])
con = sqlite3.connect(copy)
q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
check("the copy is a healthy database", con.execute("PRAGMA quick_check").fetchone()[0] == "ok")
check("it holds this server's raids and signups", {r[0] for r in q("SELECT title FROM events")} >= {"Our raid"}
      and q(f"SELECT user_id FROM signups WHERE event_id={mine}"))
check("it holds no other server's raids, signups, settings or cancelled raids",
      q("SELECT COUNT(*) FROM events WHERE guild_id != 1")[0][0] == 0 and q("SELECT COUNT(*) FROM signups WHERE user_id=880")[0][0] == 0
      and q("SELECT COUNT(*) FROM settings WHERE guild_id != 1")[0][0] == 0
      and q("SELECT COUNT(*) FROM cancelled_events WHERE guild_id != 1")[0][0] == 0)
check("it keeps characters of this server's players, not of strangers",
      q("SELECT COUNT(*) FROM characters WHERE user_id=50")[0][0] == 1 and q("SELECT COUNT(*) FROM characters WHERE user_id=880")[0][0] == 0)
con.close()
raw = copy.read_bytes()
check("nothing deleted is left behind in the file", b"secret plans" not in raw and b"Stranger" not in raw and b"Their raid" not in raw)
check("the live database is untouched", db.event(theirs) is not None and db.characters(880))

print("the manual /config backup is this server's copy too")


class FollowUpFiles:
    def __init__(self): self.sent = []
    async def send(self, *a, **k): self.sent.append((a, k))


i = inter(officer)
i.response.defer = lambda **k: asyncio.sleep(0)
i.followup = FollowUpFiles()
run(B.config_backup.callback(i))
sent = i.followup.sent[-1] if i.followup.sent else ((), {})
f = sent[1].get("file")
check("/config backup sends a private file and says it is only this server's data",
      f is not None and sent[1].get("ephemeral") and "this server's data" in sent[0][0], sent)
manual = tmp / "manual-backup.db"
f.fp.seek(0)
manual.write_bytes(f.fp.read())
con = sqlite3.connect(manual)
check("the manual copy is a healthy database with this server's raid", con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
      and ("Our raid",) in con.execute("SELECT title FROM events").fetchall())
check("it holds no other server's rows", con.execute("SELECT COUNT(*) FROM events WHERE guild_id != 1").fetchone()[0] == 0
      and con.execute("SELECT COUNT(*) FROM settings WHERE guild_id != 1").fetchone()[0] == 0
      and con.execute("SELECT COUNT(*) FROM characters WHERE user_id=880").fetchone()[0] == 0)
con.close()
raw = manual.read_bytes()
check("and the raw file has no trace of the other server's data",
      b"secret plans" not in raw and b"Stranger" not in raw and b"Their raid" not in raw and b"Their cancelled" not in raw)
check("the live database still has the other server's raid", db.event(theirs) is not None and db.characters(880))

run(B._run_backuppost(int(day0.timestamp()) + 3600))
check("only one copy a day", len(CH[21].sent) == 1)
run(B._run_backuppost(int((day0 + timedelta(hours=5)).timestamp())))  # 03:00 the next day
check("the next day's copy waits until 04:00 server time", len(CH[21].sent) == 1)
run(B._run_backuppost(int((day0 + timedelta(hours=6, minutes=1)).timestamp())))
check("and goes out after it", len(CH[21].sent) == 2 and CH[21].sent[-1][1]["file"].filename == "raidbot-2026-11-10.db")
for n in range(2, 9):
    run(B._run_backuppost(int((day0 + timedelta(days=n, hours=6, minutes=1)).timestamp())))
check("only the newest 7 posts are kept, older ones are deleted", len(CH[21].sent) == 9
      and len(db.setting(G, "backup_posts").split(",")) == 7 and len(CH[21].deleted) == 2, (db.setting(G, "backup_posts"), CH[21].deleted))

CH[21].public = True  # someone made the channel public after it was chosen
run(B._run_backuppost(int((day0 + timedelta(days=9, hours=6, minutes=1)).timestamp())))
check("a channel that became public gets no file, and /status hears why",
      len(CH[21].sent) == 9 and B.LAST_ERROR and "seen by everyone" in B.LAST_ERROR[1], B.LAST_ERROR)
CH[21].public = False
CH[21].fail_send = discord.HTTPException(types.SimpleNamespace(status=500, reason="x"), "down")
run(B._run_backuppost(int((day0 + timedelta(days=10, hours=6, minutes=1)).timestamp())))
check("a failed post is tried again next minute", db.setting(G, "backup_post_last") != "2026-11-20")
CH[21].fail_send = None
run(B._run_backuppost(int((day0 + timedelta(days=10, hours=6, minutes=2)).timestamp())))
check("and goes out once Discord is back", len(CH[21].sent) == 10 and db.setting(G, "backup_post_last") == "2026-11-20")

i = inter(officer)
run(B.config_backupchannel.callback(i, None))
check("leaving the channel empty turns it off", db.setting(G, "backup_channel") is None and "No more daily backup posts" in text(i))
run(B._run_backuppost(int((day0 + timedelta(days=11, hours=6, minutes=1)).timestamp())))
check("and nothing more is posted", len(CH[21].sent) == 10)

# ------------------------------------------------------------ realm on characters
print("realm on characters")
old_path = tmp / "before-realms.db"
con = sqlite3.connect(old_path)  # a database from before realms existed
con.executescript(dbmod.SCHEMA)  # SCHEMA has no realm column; it comes from MIGRATIONS
con.execute("INSERT INTO characters(user_id,name,wow_class,spec,role,is_main) VALUES(5,'Oldtimer','Warrior','Arms','dps',1)")
con.commit()
con.close()
old = dbmod.DB(str(old_path))
check("an older database gains the realm column and its characters keep working with no realm",
      old.characters(5)[0]["name"] == "Oldtimer" and old.characters(5)[0]["realm"] == "", dict(old.characters(5)[0]))
old.conn.close()
check("a realm is tidied, and markdown or a mention can't get in",
      dbmod.clean_realm("  Area   52 ") == "Area 52" and dbmod.clean_realm("Kel'Thuzad") == "Kel'Thuzad"
      and dbmod.clean_realm("Azjol-Nerub") == "Azjol-Nerub" and dbmod.clean_realm("**@everyone**") == "everyone"
      and dbmod.clean_realm("Pozzo dell'Eternità") == "Pozzo dell'Eternità" and len(dbmod.clean_realm("x" * 99)) == dbmod.REALM_MAX
      and dbmod.clean_realm(None) == "")

R1 = member(300)
i = inter(R1)
run(B.character_add.callback(i, "Sylvia", "Hunter", "Marksmanship", False, "Area 52"))
check("/character add saves a realm", db.character_by_name(300, "Sylvia")["realm"] == "Area 52" and "on Area 52" in text(i), text(i))
i = inter(R1)
run(B.character_add.callback(i, "Sylvia", "Hunter", "Survival"))
check("changing the spec without a realm keeps the saved realm",
      db.character_by_name(300, "Sylvia")["realm"] == "Area 52" and db.character_by_name(300, "Sylvia")["spec"] == "Survival")
i = inter(R1)
run(B.character_add.callback(i, "Nora", "Priest", "Holy"))
check("a realm stays optional", db.character_by_name(300, "Nora")["realm"] == "" and "on " not in text(i), text(i))
i = inter(R1)
run(B.character_add.callback(i, "Nora", "Priest", "Holy", False, "<<>>"))
check("a realm that is only symbols is refused", db.character_by_name(300, "Nora")["realm"] == "" and "realm name" in text(i), text(i))
i = inter(R1)
run(B.character_list.callback(i))
check("/character list shows the realm", "**Sylvia** · Area 52" in text(i) and "**Nora** —" in text(i), text(i))
i = inter(R1)
run(B.character_realm.callback(i, "nora", "Stormrage"))
check("/character realm sets it on an existing character", db.character_by_name(300, "Nora")["realm"] == "Stormrage"
      and "now on **Stormrage**" in text(i), text(i))
i = inter(R1)
run(B.character_realm.callback(i, "Nora", None))
check("/character realm with no realm clears it", db.character_by_name(300, "Nora")["realm"] == "" and "Cleared" in text(i))
i = inter(R1)
run(B.character_realm.callback(i, "Nobody", "Stormrage"))
check("/character realm on a character you don't have says so", "not found" in text(i))
labels = [c.name for c in run(B.own_char_autocomplete(inter(R1), ""))]
check("menus show the realm next to the name", "Sylvia-Area 52 (Survival Hunter)" in labels and "Nora (Holy Priest)" in labels, labels)
menu = B.CharacterSelect(1, db.characters(300))
check("the sign-up menu shows it too", any("Area 52" in (o.description or "") for o in menu.options))

print("realms on the raid post (/config realms)")
eid = db.create_event(G, 11, "Realm raid", "", int(time.time()) + 86400, 300, 0, 0, 0)
db.set_signup(eid, 300, "Sylvia", "Hunter", "Survival", "dps", "in", None)
db.set_signup(eid, 301, "Walkin", "Mage", "Fire", "dps", "tentative", None)  # no saved character
db.upsert_character(302, "Benchy", "Rogue", "Subtlety", "dps", True, "Draenor")
db.set_signup(eid, 302, "Benchy", "Rogue", "Subtlety", "dps", "bench", None)


def post_text(e):
    emb = B.render_embed(db.event(e), db.signups(e), False)
    return " ".join(f.value for f in emb.fields)


check("off by default: the post shows names only", db.setting(G, "show_realms") is None
      and "Area 52" not in post_text(eid) and "**Sylvia**" in post_text(eid))
i = inter(officer)
run(B.config_realms.callback(i, True))
check("/config realms on", db.setting(G, "show_realms") == "1" and "/character realm" in text(i), text(i))
check("the post shows realms in the role columns and on the bench line",
      "**Sylvia** (Area 52)" in post_text(eid) and "**Benchy** (Draenor)" in post_text(eid), post_text(eid))
check("someone without a saved realm shows as before", "**Walkin**" in post_text(eid) and "**Walkin** (" not in post_text(eid))
db.set_realm(300, db.character_by_name(300, "Sylvia")["id"], "Silvermoon")
check("changing a saved realm updates the post", "**Sylvia** (Silvermoon)" in post_text(eid), post_text(eid))
check("realms_for ignores signups without a character", 301 not in db.realms_for(db.signups(eid)))
i = inter(officer)
run(B.config_show.callback(i))
check("/config show lists it", "Realms on raid posts:** on" in text(i), text(i))
i = inter(officer)
run(B.config_realms.callback(i, False))
check("/config realms off hides them again and keeps the saved realms",
      "Silvermoon" not in post_text(eid) and db.character_by_name(300, "Sylvia")["realm"] == "Silvermoon")

print("/character import keeps the realm")
real_lookup2, real_preset = B.raiderio_lookup, wd.PRESET_NAME
wd.PRESET_NAME = "retail"
db.set_setting(G, "raiderio", "1")


class FollowUp:
    def __init__(self): self.sent = []
    async def send(self, *a, **k): self.sent.append((a, k))


async def fake_rio(region, realm, name):
    return {"name": "Imported", "class": "Druid", "spec": "Balance", "realm": "Area 52", "ilvl": None, "url": None}
B.raiderio_lookup = fake_rio
i = inter(R1)
i.response.defer = lambda **k: asyncio.sleep(0)
i.followup = FollowUp()
run(B.character_import.callback(i, "imported", "area-52"))
check("the realm Raider.IO names is saved", db.character_by_name(300, "Imported")["realm"] == "Area 52"
      and "on Area 52" in i.followup.sent[0][0][0], i.followup.sent)
B.raiderio_lookup, wd.PRESET_NAME = real_lookup2, real_preset

print("realms and the daily backup copy / forgetme")
db.upsert_character(880, "Stranger", "Rogue", "Subtlety", "dps", True, "Secretrealm")
db.set_signup(mine, 300, "Sylvia", "Hunter", "Survival", "dps", "in", None)
out = tmp / "realm-copy.db"
db.snapshot_for_guild(str(out), G)
con = sqlite3.connect(out)
check("this server's copy keeps its players' realms", con.execute("SELECT realm FROM characters WHERE user_id=300 AND name='Sylvia'").fetchone() == ("Silvermoon",))
con.close()
check("and has no other server's player's realm in the file", b"Secretrealm" not in out.read_bytes())
i = inter(R1)
run(B.forgetme.callback(i))
press(i, "Yes, delete", user=R1)
check("/forgetme deletes characters with their realms", db.characters(300) == []
      and db._one("SELECT COUNT(*) c FROM characters WHERE realm='Silvermoon'")["c"] == 0)

# ------------------------------------------------------------ signup notes
print("signup notes: the database")
old_path = tmp / "before-notes.db"
con = sqlite3.connect(old_path)  # a database from before notes existed
con.executescript(dbmod.SCHEMA)  # SCHEMA has no note column; it comes from MIGRATIONS
con.execute("INSERT INTO events(id,guild_id,channel_id,title,start_ts,creator_id) VALUES(1,1,11,'Old raid',2000000000,1)")
con.execute("INSERT INTO signups(event_id,user_id,char_name,wow_class,spec,role,status) VALUES(1,5,'Oldtimer','Warrior','Arms','dps','in')")
con.commit()
con.close()
old = dbmod.DB(str(old_path))
check("an older database gains the note column and its signups keep working with no note",
      old.signup(1, 5)["note"] == "" and old.signup(1, 5)["status"] == "in", dict(old.signup(1, 5)))
old.set_signup(1, 5, "Oldtimer", "Warrior", "Arms", "dps", "late", None)
check("and can still be changed", old.signup(1, 5)["status"] == "late")
old.conn.close()
check("a note is tidied to one plain line, without formatting, mentions or links",
      dbmod.clean_note("  late   15\nmin ") == "late 15 min" and dbmod.clean_note("**@everyone** <@123>") == "everyone 123"
      and dbmod.clean_note("`x` ~~y~~ ||z|| [a](b)") == "x y z a(b)" and dbmod.clean_note("see https://evil.example") == "see https evil.example"
      and dbmod.clean_note("can swap to heals, 100% sure!") == "can swap to heals, 100% sure!"
      and dbmod.clean_note("Ich komme später 😅") == "Ich komme später 😅"
      and len(dbmod.clean_note("x" * 200)) == dbmod.NOTE_MAX and dbmod.clean_note(None) == "")

N1, N2, N3 = member(400), member(401), member(402)
ne = db.create_event(G, 11, "Note raid", "", int(time.time()) + 86400, 1, 0, 0, 1)
db.set_message_id(ne, 4000)
db.set_signup(ne, 400, "Notey", "Mage", "Frost", "dps", "in", None)
db.set_signup(ne, 401, "Benchy", "Rogue", "Subtlety", "dps", "bench", "in")
db.set_signup(ne, 402, None, None, None, None, "tentative", None)


def roster(e):
    i = inter(officer)
    run(B.raid_roster.callback(i, e))
    return text(i)


print("/note is off by default")
check("the setting starts off", B.notes_mode(G) == "off" and db.setting(G, "signup_notes") is None)
i = inter(N1)
run(B.note.callback(i, ne, "late 15 min"))
check("/note says notes are off and stores nothing", "/config notes" in text(i) and db.signup(ne, 400)["note"] == ""
      and i.response.sent[0][1].get("ephemeral") is True, text(i))
check("the roster has no notes section", "Notes" not in roster(ne))
i = inter(officer)
run(B.config_show.callback(i))
check("/config show says off", "Signup notes:** off" in text(i), text(i))

print("/config notes on (officers only)")
i = inter(officer)
run(B.config_notes.callback(i, True))
check("turned on, notes for officers only", B.notes_mode(G) == "officers" and "/raid roster" in text(i)
      and "onpost: True" in text(i), text(i))
CH[11].edits.clear()
i = inter(N1)
run(B.note.callback(i, ne, "  late 15 min "))
check("/note saves a note on your signup", db.signup(ne, 400)["note"] == "late 15 min"
      and "*late 15 min*" in text(i) and "to the raid officers" in text(i), text(i))
check("the post isn't redrawn when it doesn't show notes", not any(mid == 4000 for mid, _ in CH[11].edits))
check("the raid post doesn't show it", "late 15 min" not in post_text(ne))
r = roster(ne)
check("/raid roster lists it with the character and status", "Notes (1):" in r and "Notey (in): late 15 min" in r, r)
i = inter(N3)
run(B.note.callback(i, ne, "might be on call"))
check("someone signed up without a character can add a note too (shown by name, or id if they left the server)",
      "user 402 (tentative): might be on call" in roster(ne), roster(ne))
db.set_signup(ne, 400, "Notey", "Mage", "Frost", "dps", "late", None)
check("the note stays when the player changes their status", db.signup(ne, 400)["note"] == "late 15 min")
db.set_signup(ne, 402, None, None, None, None, "absent", None)
check("an absent player's note isn't in the roster", "might be on call" not in roster(ne) and db.signup(ne, 402)["note"])
db.set_signup(ne, 400, "Notey", "Mage", "Frost", "dps", "in", None)
i = inter(N1)
run(B.myraids.callback(i))
check("/myraids shows your note", "*late 15 min*" in text(i), text(i))
choices = run(B.my_signup_autocomplete(inter(N1), "note"))
check("the raid menu lists raids you signed up for", [c.value for c in choices] == [ne], choices)

i = inter(member(499))
run(B.note.callback(i, ne, "hello"))
check("someone who isn't signed up is told to sign up first", "aren't signed up" in text(i) and db.signup(ne, 499) is None, text(i))
i = inter(N1)
run(B.note.callback(i, 99999, "hello"))
check("an unknown raid is refused", "Raid not found" in text(i))
theirs_note = db.create_event(2, 99, "Elsewhere", "", int(time.time()) + 86400, 1, 0, 0, 0)
db.set_signup(theirs_note, 400, "Notey", "Mage", "Frost", "dps", "in", None)
i = inter(N1)
run(B.note.callback(i, theirs_note, "hello"))
check("a raid on another server is refused", "Raid not found" in text(i) and db.signup(theirs_note, 400)["note"] == "")
i = inter(N1)
run(B.note.callback(i, ne, "<@> ** ``"))
check("a note that is only formatting is refused", "plain words" in text(i) and db.signup(ne, 400)["note"] == "late 15 min", text(i))
i = inter(N1)
run(B.note.callback(i, ne, "y" * 80))
check("a long note is cut and the player is told", db.signup(ne, 400)["note"] == "y" * dbmod.NOTE_MAX and "cut to 60" in text(i), text(i))
past = db.create_event(G, 11, "Last week", "", int(time.time()) - 7 * 86400, 1, 0, 0, 0)
db.set_signup(past, 400, "Notey", "Mage", "Frost", "dps", "in", None)
i = inter(N1)
run(B.note.callback(i, past, "sorry"))
check("a raid that's over can't get a note", "is over" in text(i) and db.signup(past, 400)["note"] == "", text(i))
i = inter(N1)
run(B.note.callback(i, ne, None))
check("/note without text clears it", db.signup(ne, 400)["note"] == "" and "Cleared" in text(i), text(i))

print("/config notes onpost: True")
i = inter(officer)
run(B.config_notes.callback(i, True, True))
check("notes also on the post", B.notes_mode(G) == "post" and "raid post" in text(i), text(i))
CH[11].edits.clear()
i = inter(N1)
run(B.note.callback(i, ne, "late 15 min"))
check("the reply says everyone sees it", "on the raid post" in text(i), text(i))
check("the post is redrawn straight away", any(mid == 4000 for mid, _ in CH[11].edits), CH[11].edits)
i = inter(N2)
run(B.note.callback(i, ne, "can swap to heals"))
check("the post shows notes in italics after the name, in the role columns and on the bench line",
      "**Notey** · *late 15 min*" in post_text(ne) and "**Benchy** (#1 dps) · *can swap to heals*" in post_text(ne), post_text(ne))
db.set_setting(G, "show_realms", "1")
db.upsert_character(400, "Notey", "Mage", "Frost", "dps", True, "Draenor")
check("with realms on too, the note comes after the realm", "**Notey** (Draenor) · *late 15 min*" in post_text(ne), post_text(ne))
db.set_setting(G, "show_realms", "0")
check("without a note a name shows as before", "**Notey**" in post_text(ne)
      and B.render_embed(db.event(ne), db.signups(ne), False).fields)
for n in range(30):  # a full raid where everyone left a long note still fits in one post
    db.set_signup(ne, 1000 + n, f"Player{n}", "Warrior", "Arms", "dps", "in", None)
    db.set_note(ne, 1000 + n, "z" * dbmod.NOTE_MAX)
emb = B.render_embed(db.event(ne), db.signups(ne), False)
check("a full post with long notes stays inside Discord's limits", len(emb) <= 6000 and all(len(f.value) <= 1024 for f in emb.fields), len(emb))
check("and so does the roster", len(roster(ne)) <= 2000, len(roster(ne)))
for n in range(30):
    db.remove_signup(ne, 1000 + n)
i = inter(officer)
run(B.config_show.callback(i))
check("/config show says where notes show", "Signup notes:** on (also on the raid post)" in text(i), text(i))

i = inter(officer)
run(B.config_notes.callback(i, False))
check("/config notes off hides notes everywhere and keeps them", B.notes_mode(G) == "off" and "late 15 min" not in post_text(ne)
      and "Notes" not in roster(ne) and db.signup(ne, 400)["note"] == "late 15 min" and "kept" in text(i), text(i))
i = inter(N1)
run(B.myraids.callback(i))
check("and /myraids doesn't show them", "late 15 min" not in text(i), text(i))
i = inter(officer)
run(B.config_notes.callback(i, True))
check("turning notes back on with onpost left out is officers only again", B.notes_mode(G) == "officers")

print("notes and cancel / restore, the daily backup copy and /forgetme")
eid = new_raid("Noted cancel")
db.set_note(eid, 50, "bringing feasts")
db.cancel_event(eid, 1)
db.restore_event(eid)
check("a cancelled and restored raid keeps its notes", db.signup(eid, 50)["note"] == "bringing feasts")
db.set_note(theirs_note, 400, "Secretnote")
out = tmp / "note-copy.db"
db.snapshot_for_guild(str(out), G)
con = sqlite3.connect(out)
check("this server's copy keeps its notes", con.execute(f"SELECT note FROM signups WHERE event_id={ne} AND user_id=400").fetchone() == ("late 15 min",))
con.close()
check("and has no other server's note in the file", b"Secretnote" not in out.read_bytes())
fc = new_raid("Noted and cancelled")
db.set_signup(fc, 400, "Notey", "Mage", "Frost", "dps", "in", None)
db.set_note(fc, 400, "Cancellednote")
db.cancel_event(fc, 1)
i = inter(N1)
run(B.forgetme.callback(i))
press(i, "Yes, delete", user=N1)
check("/forgetme deletes a player's notes in every server, and in cancelled raids' copies",
      db.signup(ne, 400) is None and db.signup(theirs_note, 400) is None
      and db._one("SELECT COUNT(*) c FROM signups WHERE note IN ('late 15 min','Secretnote')")["c"] == 0
      and "Cancellednote" not in db.cancelled_event(fc)["signups_json"])
check("other players' notes stay", db.signup(ne, 401)["note"] == "can swap to heals")

# ------------------------------------------------------------ switching spec from the post
print("the Spec button")
check("off by default: posts have the usual six buttons and no Spec button", not B.spec_switch_on(G)
      and "raid:spec" not in [c.custom_id for c in B.RaidView().children] and len(B.RaidView().children) == 6)
check("the view registered at startup has it, so a post drawn with the button is always answered",
      "raid:spec" in [c.custom_id for c in B.RaidView(spec_button=True).children])
sp = db.create_event(G, 11, "Spec raid", "", int(time.time()) + 86400, 1, 1, 1, 0)
db.set_message_id(sp, 5000)
db.set_signup(sp, 500, "Healy", "Shaman", "Restoration", "healer", "in", None)
db.set_note(sp, 500, "bringing feasts")
db.set_signup(sp, 501, "Tanky", "Warrior", "Protection", "tank", "in", None)
db.set_signup(sp, 502, "Waity", "Paladin", "Holy", "healer", "bench", "in")
db.set_signup(sp, 503, None, None, None, None, "absent", None)
db.set_signup(sp, 504, "Maybe", "Druid", "Balance", "dps", "tentative", None)
S1, S2, S3, S4, S5 = (member(u) for u in range(500, 505))
CH[11].edits.clear()


def on_post(user):
    i = inter(user)
    i.message = types.SimpleNamespace(id=5000, jump_url="http://x/5000")
    return i


def spec_press(user):
    i = on_post(user)
    run(B.RaidView(spec_button=True).switch_spec.callback(i))
    return i


def pick(i, spec, user):
    sel = i.response.sent[0][1]["view"].children[0]
    sel._values = [spec]  # what Discord fills in when the player picks
    click = on_post(user)
    run(sel.callback(click))
    return click


i = spec_press(S1)
check("pressing it while the switch is off says so and changes nothing", "turned off" in text(i)
      and i.response.sent[0][1].get("ephemeral") is True and db.signup(sp, 500)["spec"] == "Restoration", text(i))

i = inter(officer)
run(B.config_specswitch.callback(i, True))
check("/config specswitch on turns it on and redraws the open posts with the button",
      B.spec_switch_on(G) and "Spec" in text(i) and any(m == 5000 and "raid:spec" in [c.custom_id for c in k["view"].children]
                                                        for m, k in CH[11].edits), text(i))
i = inter(officer)
run(B.config_show.callback(i))
check("/config show lists it", "Spec button on raid posts:** on" in text(i), text(i))

i = spec_press(S1)
opts = [o.label for o in i.response.sent[0][1]["view"].children[0].options]
check("the picker offers the other specs of the class signed up with, privately", sorted(opts) == ["Elemental", "Enhancement"]
      and i.response.sent[0][1].get("ephemeral") is True and "Healy" in text(i), (opts, text(i)))
c = pick(i, "Elemental", S1)
row = db.signup(sp, 500)
check("picking one changes spec and role, keeps character, status and note",
      (row["char_name"], row["wow_class"], row["spec"], row["role"], row["status"], row["note"])
      == ("Healy", "Shaman", "Elemental", "dps", "in", "bringing feasts") and "Switched to **Elemental**" in text(c), (dict(row), text(c)))
check("the healer slot that opened goes to the first one waiting for it", db.signup(sp, 502)["status"] == "in"
      and any(uid == 502 for uid, _ in DMS), dict(db.signup(sp, 502)))
check("and the post is redrawn", "**Healy**" in post_text(sp))

i = spec_press(S1)
c = pick(i, "Restoration", S1)
row = db.signup(sp, 500)
check("switching into a full role puts them on the bench, waiting for that slot, and says why",
      row["status"] == "bench" and row["bench_from"] == "in" and row["role"] == "healer"
      and "Healer slots are full" in text(c) and "#1 in line" in text(c), (dict(row), text(c)))
i = spec_press(S3)
c = pick(i, "Retribution", S3)
check("someone in a role leaving it frees the slot for the one waiting", db.signup(sp, 500)["status"] == "in"
      and db.signup(sp, 502)["role"] == "dps", dict(db.signup(sp, 500)))
db.set_signup(sp, 502, "Waity", "Paladin", "Retribution", "dps", "bench", None)  # chose bench themselves
i = spec_press(S3)
c = pick(i, "Holy", S3)
check("a bench or tentative choice of their own stays as it was", db.signup(sp, 502)["status"] == "bench"
      and db.signup(sp, 502)["bench_from"] is None and db.signup(sp, 502)["spec"] == "Holy")
i = spec_press(S5)
c = pick(i, "Restoration", S5)
check("tentative stays tentative", db.signup(sp, 504)["status"] == "tentative" and db.signup(sp, 504)["role"] == "healer")

i = spec_press(S4)
check("someone marked absent is asked to sign up first", "Sign Up" in text(i) and "view" not in i.response.sent[0][1], text(i))
i = spec_press(member(599))
check("and so is someone who never signed up", "Sign Up" in text(i) and db.signup(sp, 599) is None, text(i))

i = spec_press(S2)
db.set_signup(sp, 501, "Tanky", "Paladin", "Protection", "tank", "in", None)  # changed character before picking
c = pick(i, "Fury", S2)
check("a stale picker (they changed character meanwhile) changes nothing", db.signup(sp, 501)["spec"] == "Protection"
      and "again" in text(c), text(c))

db.update_event(sp, start_ts=int(time.time()) - 60)
i = spec_press(S1)
check("a closed raid can't be changed", "closed" in text(i).lower() and db.signup(sp, 500)["spec"] == "Restoration", text(i))
db.update_event(sp, start_ts=int(time.time()) + 86400)

i = inter(officer)
run(B.config_specswitch.callback(i, False))
check("/config specswitch off takes the button away from the posts again", not B.spec_switch_on(G)
      and "raid:spec" not in [c.custom_id for c in CH[11].edits[-1][1]["view"].children] and "Sign Up" in text(i), text(i))

print("/raid promote moves a chosen benched player in")
pr = db.create_event(G, 11, "Promote night", "", int(time.time()) + 86400, 1, 1, 1, 2)  # 1 tank, 1 healer, 2 dps
db.set_message_id(pr, 880)
db.set_signup(pr, 700, "Tank", "Warrior", "Protection", "tank", "in", None)
db.set_signup(pr, 701, "Heals", "Priest", "Holy", "healer", "bench", None)        # chose bench themselves
db.set_signup(pr, 702, "Firsty", "Mage", "Frost", "dps", "in", None)
db.set_signup(pr, 703, "Secondy", "Rogue", "Combat" if wd.find_spec("Rogue", "Combat") else "Outlaw", "dps", "in", None)
db.set_signup(pr, 704, "Waiter", "Hunter", "Marksmanship", "dps", "bench", "late")   # benched by the bot, wanted late
db.set_signup(pr, 705, "Second", "Warlock", "Affliction", "dps", "bench", "in")
db.set_signup(pr, 706, None, None, None, None, "absent", None)
DMS.clear()
CH[11].edits.clear()

i = inter(officer)
run(B.raid_promote.callback(i, pr, member(701)))
row = db.signup(pr, 701)
check("a benched player whose role has room moves in, privately confirmed", row["status"] == "in" and row["bench_from"] is None
      and "now **in**" in text(i) and i.response.sent[0][1].get("ephemeral") is True, (dict(row), text(i)))
check("the player is told", any(uid == 701 and "off the bench" in t for uid, t in DMS), DMS)
check("and the post is redrawn", any(m == 880 for m, _ in CH[11].edits))

i = inter(officer)
run(B.raid_promote.callback(i, pr, member(705)))
check("a full role is refused by default and nothing changes", db.signup(pr, 705)["status"] == "bench"
      and "Nothing was changed" in text(i) and "DPS slots are full" in text(i) and "over_limit" in text(i), text(i))

i = inter(officer)
run(B.raid_promote.callback(i, pr, member(705), True))
check("over_limit takes them in anyway, ahead of the one first in line", db.signup(pr, 705)["status"] == "in"
      and db.signup(pr, 704)["status"] == "bench" and "over the limit" in text(i), text(i))
check("the one first in line keeps their place", db.bench_wait(pr, 704) == (1, "dps"), db.bench_wait(pr, 704))

i = inter(officer)
run(B.raid_promote.callback(i, pr, member(704), True))
check("someone the bot benched while they asked for late comes in as late", db.signup(pr, 704)["status"] == "late", text(i))

for uid, why in ((702, "is **in**"), (706, "is **absent**"), (799, "isn't signed up")):
    i = inter(officer)
    run(B.raid_promote.callback(i, pr, member(uid)))
    check(f"someone who {why.replace('*', '')} is pointed at /raid add", why in text(i) and "/raid add" in text(i), text(i))

db.set_signup(pr, 707, None, None, None, None, "bench", None)
i = inter(officer)
run(B.raid_promote.callback(i, pr, member(707)))
check("a bench without a character is pointed at /raid add", "without a character" in text(i)
      and db.signup(pr, 707)["status"] == "bench", text(i))

db.update_event(pr, start_ts=int(time.time()) - 60)
db.set_signup(pr, 708, "Late", "Druid", "Balance", "dps", "bench", None)
i = inter(officer)
run(B.raid_promote.callback(i, pr, member(708), True))
check("a raid that has started can't be changed", "already started" in text(i) and db.signup(pr, 708)["status"] == "bench")

print("/raid roster invites: Name-Realm lines to paste into WoW")
iv = db.create_event(G, 11, "Invite night", "", int(time.time()) + 86400, 1, 0, 0, 0)
db.upsert_character(800, "Thrall", "Shaman", "Enhancement", "dps", True, "Area 52")
db.set_signup(iv, 800, "Thrall", "Shaman", "Enhancement", "dps", "in", None)
db.set_signup(iv, 801, "Homey", "Mage", "Frost", "dps", "late", None)          # no saved realm
db.upsert_character(802, "Kael", "Priest", "Holy", "healer", True, "Kel'Thuzad")
db.set_signup(iv, 802, "Kael", "Priest", "Holy", "healer", "in", None)
db.set_signup(iv, 803, "Benched", "Rogue", "Subtlety", "dps", "bench", None)
db.set_signup(iv, 804, "Maybe", "Warrior", "Arms", "dps", "tentative", None)
db.set_signup(iv, 805, None, None, None, None, "in", None)                      # in without a character
db.set_signup(iv, 806, "Ghost", "Druid", "Balance", "dps", "in", None)
db.set_no_show(iv, 806, True)

i = inter(officer)
run(B.raid_roster.callback(i, iv))
check("without invites the roster is unchanged", "/invite" not in text(i) and "Invite night" in text(i), text(i))
i = inter(officer)
run(B.raid_roster.callback(i, iv, True))
t = text(i)
check("invites lists everyone in or late, privately", "/invite Thrall-Area52" in t and "/invite Homey\n" in t
      and i.response.sent[0][1].get("ephemeral") is True, t)
check("the realm keeps its apostrophe and loses its spaces", "/invite Kael-Kel'Thuzad" in t, t)
check("bench, tentative and no-shows are left out", "Benched" not in t and "Maybe" not in t and "Ghost" not in t, t)
check("the count says how many, and someone without a character is mentioned", "3 to invite" in t
      and "1 signed up without a character" in t, t)
check("names inside a code block", "```\n/invite" in t, t)

big = db.create_event(G, 11, "Big night", "", int(time.time()) + 86400, 1, 0, 0, 0)
for n in range(40):
    db.upsert_character(900 + n, f"Player{n:02d}", "Mage", "Frost", "dps", True, "Twisting Nether")
    db.set_signup(big, 900 + n, f"Player{n:02d}", "Mage", "Frost", "dps", "in", None)
i = inter(officer)
run(B.raid_roster.callback(i, big, True))
t = text(i)
blocks = t.split("```\n", 1)[1].rsplit("\n```", 1)[0].split("\n\n")
check("forty players fit in one message", len(t) <= 2000 and "/invite Player39-TwistingNether" in t, len(t))
check("each block fits in one WoW macro (255 characters)", len(blocks) > 1 and all(len(b) <= 255 for b in blocks),
      [len(b) for b in blocks])
check("invite_name without a realm is just the name", B.invite_name("Solo", None) == "Solo" and B.invite_name("Solo", "  ") == "Solo")

empty = db.create_event(G, 11, "Empty night", "", int(time.time()) + 86400, 1, 0, 0, 0)
i = inter(officer)
run(B.raid_roster.callback(i, empty, True))
check("nobody in yet: a plain sentence, no empty block", "nobody to invite" in text(i) and "```" not in text(i), text(i))

print("the Upcoming raids board (/config board)")
for e in db.upcoming_events(G, 0):  # start from a server with no raids
    db.delete_event(e["id"])
EVERY_R, RAIDER_R, BOT_ME = "@everyone role", "Raider role", object()
NOT_FOUND = discord.NotFound(types.SimpleNamespace(status=404, reason="x"), "unknown message")


class RoleChannel(FakeChannel):
    """A channel that some roles can see and others can't, where the bot may lack a permission."""
    def __init__(self, cid, seen_by, embed=True, can_pin=True):
        super().__init__(cid)
        self.seen_by, self.embed, self.can_pin, self.pins, self.gone = seen_by, embed, can_pin, [], set()
        self.guild = types.SimpleNamespace(id=G, me=BOT_ME, default_role=EVERY_R, roles=[EVERY_R, RAIDER_R],
                                           get_role=lambda r: None, get_member=lambda u: None)

    def permissions_for(self, who):
        if who is BOT_ME:
            return types.SimpleNamespace(view_channel=True, send_messages=True, embed_links=self.embed)
        return types.SimpleNamespace(view_channel=who in self.seen_by)

    def get_partial_message(self, mid):
        msg = FakeMsg(self, mid)
        if mid in self.gone:
            async def missing(**k): raise NOT_FOUND
            msg.edit = missing
        return msg


async def fake_pin(self, **k):
    if getattr(self.ch, "can_pin", True) is False:
        raise discord.Forbidden(types.SimpleNamespace(status=403, reason="x"), "missing permissions")
    self.ch.pins.append(self.id)
FakeMsg.pin = fake_pin

CH[31] = RoleChannel(31, {EVERY_R, RAIDER_R})  # a public board channel
CH[32] = RoleChannel(32, {RAIDER_R})  # raiders only
CH[33] = RoleChannel(33, {EVERY_R, RAIDER_R}, embed=False)
CH[34] = RoleChannel(34, {EVERY_R, RAIDER_R}, can_pin=False)
board = CH[31]


def board_text(ch=board):
    """The board as last drawn in this channel: an edit of the current message, or else what was sent."""
    mid = int(db.setting(G, "board_message") or 0)
    edits = [k for m, k in ch.edits if m == mid]
    if edits:
        return edits[-1]["embed"].description
    return ch.sent[-1][1]["embed"].description if ch.sent else ""


check("the board is off by default", db.setting(G, "board_channel") is None)
i = inter(officer)
run(B.config_show.callback(i))
check("/config show says it's off", "Upcoming raids board:** off" in text(i), text(i))
i = inter(officer)
run(B.config_board.callback(i, CH[33]))
check("a channel where the bot can't embed links is refused", db.setting(G, "board_channel") is None
      and "Embed Links" in text(i) and not CH[33].sent, text(i))

i = inter(officer)
run(B.config_board.callback(i, board))
check("turning it on posts the board, privately confirmed", db.setting(G, "board_channel") == "31" and len(board.sent) == 1
      and i.response.sent[0][1].get("ephemeral") is True, text(i))
first = int(db.setting(G, "board_message"))
check("the board message is remembered and pinned", board.pins == [first] and "pinned" in text(i), (board.pins, first))
check("with no raids it says so", "No raids planned" in board_text(), board_text())
check("the post pings nobody", board.sent[0][1]["allowed_mentions"].everyone is False)
i = inter(officer)
run(B.config_show.callback(i))
check("/config show names the channel", "Upcoming raids board:** <#31>" in text(i), text(i))

kara = new_raid("Karazhan")
secret = db.create_event(G, 32, "Raiders-only night", "", int(time.time()) + 2 * 86400, 1, 2, 5, 18)
db.set_message_id(secret, 4242)
unposted = db.create_event(G, 11, "Not posted yet", "", int(time.time()) + 3 * 86400, 1, 2, 5, 18)
run(B.draw_board(G))
t = board_text()
check("a new raid shows up on the board by editing the same message", len(board.sent) == 1 and board.edits[-1][0] == first
      and "Karazhan" in t, t)
check("each raid has its time, a link to its post and the counts per role",
      f"<t:{db.event(kara)['start_ts']}:f>" in t and f"/channels/{G}/11/{500 + kara}" in t and "1 in" in t
      and "💚 1/5" in t and "🛡️ 0/2" in t and "1 bench" in t, t)
check("players don't see raid ids on the board", f"#{kara} " not in t, t)
check("a raid in a channel some board readers can't see is left off", "Raiders-only" not in t, t)
check("a raid without a post yet is left off", "Not posted yet" not in t, t)
edits = len(board.edits)
run(B.draw_board(G))
check("nothing changed: the board isn't edited again", len(board.edits) == edits)

db.set_signup(kara, 53, "Garrosh", "Warrior", "Protection", "tank", "in", None)
run(B.draw_board(G))
check("a signup changes the counts", len(board.edits) == edits + 1 and "2 in" in board_text() and "🛡️ 1/2" in board_text(),
      board_text())

i = inter(officer)
run(B.config_board.callback(i, board))
check("running it again replaces the board (to bring back a deleted one)", first in board.deleted and len(board.sent) == 2
      and int(db.setting(G, "board_message")) != first)
check("and says how many raids are left off and why", "1 upcoming raid is left off" in text(i), text(i))

CH[35] = RoleChannel(35, {RAIDER_R})
i = inter(officer)
run(B.config_board.callback(i, CH[35]))
check("on a raiders-only board the raiders-only raid is listed", "Raiders-only night" in board_text(CH[35])
      and "left off" not in text(i), board_text(CH[35]))
check("moving the board deletes the old one", len(board.deleted) == 2 and db.setting(G, "board_channel") == "35",
      board.deleted)
i = inter(officer)
run(B.config_board.callback(i, board))

print("the board keeps itself up to date")
B.BOARD_DELAY = 0
edits = len(board.edits)


async def several_changes():
    for _ in range(5):
        B.schedule_board(G)
    db.set_signup(kara, 54, "Anduin", "Priest", "Holy", "healer", "in", None)
    await asyncio.sleep(0.05)
run(several_changes())
check("a rush of changes costs one edit", len(board.edits) == edits + 1 and "💚 2/5" in board_text(), board_text())

edits = len(board.edits)


async def redraw_then_wait(eid):
    await B.refresh_message(eid)
    await asyncio.sleep(0.05)
db.set_signup(kara, 55, "Sylvanas", "Hunter", "Marksmanship", "dps", "in", None)
run(redraw_then_wait(kara))
check("redrawing a raid post also updates the board", len(board.edits) == edits + 1 and "4 in" in board_text(), board_text())

later = new_raid("Gruul")
db.update_event(later, start_ts=int(time.time()) + 5 * 86400)
i = inter(officer)


async def cancel_then_wait(eid):
    await B.raid_cancel.callback(i, eid)
    await asyncio.sleep(0.05)
run(B.draw_board(G))
check("a second raid is listed, in start order", board_text().index("Karazhan") < board_text().index("Gruul"), board_text())
run(cancel_then_wait(later))
check("cancelling a raid takes it off the board", db.event(later) is None and "Gruul" not in board_text(), board_text())

db.update_event(kara, start_ts=int(time.time()) - 60)
run(B._run_board(int(time.time())))
check("once a raid has started the minute check takes it off", "Karazhan" not in board_text()
      and "No raids planned" in board_text(), board_text())

board.gone.add(int(db.setting(G, "board_message")))
db.update_event(kara, start_ts=int(time.time()) + 86400)
sent = len(board.sent)
run(B.draw_board(G))
check("a board deleted by hand is posted again and pinned", len(board.sent) == sent + 1
      and int(db.setting(G, "board_message")) == board.pins[-1] and "Karazhan" in board_text(), board.pins)

for n in range(17):
    e = db.create_event(G, 11, f"Alt run {n}", "", int(time.time()) + 7 * 86400 + n, 1, 0, 0, 0)
    db.set_message_id(e, 8000 + n)
run(B.draw_board(G))
check("the board lists 15 raids and counts the rest", board_text().count("/channels/") == 15
      and "3 more raids after these" in board_text() and len(board_text()) <= 4000, board_text()[-200:])

i = inter(officer)
run(B.config_board.callback(i, CH[34]))
check("if the bot can't pin, the board still goes up and the admin is told how to fix it",
      db.setting(G, "board_channel") == "34" and CH[34].sent and "Manage Messages" in text(i), text(i))
last = int(db.setting(G, "board_message"))
i = inter(officer)
run(B.config_board.callback(i, None))
check("turning it off deletes the board and the settings", db.setting(G, "board_channel") is None
      and db.setting(G, "board_message") is None and last in CH[34].deleted and "off" in text(i), text(i))
edits = len(CH[34].edits)


async def change_with_board_off():
    B.schedule_board(G)
    await asyncio.sleep(0.05)
run(change_with_board_off())
run(B._run_board(int(time.time())))
check("with the board off nothing is drawn", len(CH[34].edits) == edits and len(CH[34].sent) == 1)

print("the low-signup alert (/alerts lowsignup)")
for e in db.upcoming_events(G, 0):
    db.delete_event(e["id"])
OFFICER_ROLE = types.SimpleNamespace(id=777, mention="<@&777>")
CH[41] = RoleChannel(41, set())  # officers only: no ordinary role can read it
CH[42] = RoleChannel(42, {EVERY_R, RAIDER_R})  # public
CH[43] = RoleChannel(43, {EVERY_R, RAIDER_R})  # a public raid channel
CH[44] = RoleChannel(44, {RAIDER_R})  # a raiders-only raid channel
for c in (CH[41], CH[42], CH[43], CH[44]):
    c.guild.get_role = lambda r: OFFICER_ROLE if r == 777 else None
alert = CH[41]


class NoSend(RoleChannel):
    def permissions_for(self, who):
        return types.SimpleNamespace(view_channel=True, send_messages=False) if who is BOT_ME else super().permissions_for(who)
CH[45] = NoSend(45, set())


def short_raid(title, channel=43, hours_ahead=3, caps=(2, 5, 18)):
    eid = db.create_event(G, channel, title, "", int(time.time()) + hours_ahead * 3600, 1, *caps)
    db.set_message_id(eid, 5000 + eid)
    return eid


def alerts_sent(ch=alert):
    return [a[0] if a else k.get("content", "") for a, k in ch.sent]


check("it's off by default", db.setting(G, "lowsignup_hours") is None)
i = inter(officer)
run(B.config_show.callback(i))
check("/config show says it's off", "Low-signup alert:** off" in text(i), text(i))
early = short_raid("Unchecked while off")
run(B._run_lowsignup(int(time.time())))
check("while off nothing is posted and the raid stays unchecked", not alert.sent and db.event(early)["low_alerted"] == 0)
db.delete_event(early)

i = inter(officer)
run(B.alerts_lowsignup.callback(i, 4))
check("turning it on without a channel is refused", db.setting(G, "lowsignup_hours") is None and "Pick a channel" in text(i), text(i))
i = inter(officer)
run(B.alerts_lowsignup.callback(i, 4, CH[45]))
check("a channel the bot can't post in is refused", db.setting(G, "lowsignup_hours") is None and "Send Messages" in text(i), text(i))
i = inter(officer)
run(B.alerts_lowsignup.callback(i, 4, alert))
check("turning it on saves the hours and channel, privately confirmed", db.setting(G, "lowsignup_hours") == "4"
      and db.setting(G, "lowsignup_channel") == "41" and i.response.sent[0][1].get("ephemeral") is True
      and "4 hours" in text(i), text(i))
i = inter(officer)
run(B.config_show.callback(i))
check("/config show describes it", "Low-signup alert:** 4 hours before, in <#41>" in text(i), text(i))

kara = short_raid("Karazhan", hours_ahead=3)
db.set_signup(kara, 50, "Thrall", "Shaman", "Restoration", "healer", "in", None)
db.set_signup(kara, 51, "Jaina", "Mage", "Frost", "dps", "bench", "dps")
far = short_raid("Far away", hours_ahead=30)
hidden = short_raid("Raiders-only", channel=44, hours_ahead=2)
full = short_raid("Full house", hours_ahead=2, caps=(1, 1, 0))
db.set_signup(full, 60, "Tank", "Warrior", "Protection", "tank", "in", None)
db.set_signup(full, 61, "Heal", "Priest", "Holy", "healer", "late", None)
run(B._run_lowsignup(int(time.time())))
t = "\n".join(alerts_sent())
check("each short raid inside the window gets an alert", len(alert.sent) == 2 and "Karazhan" in t, t)
check("the alert says what's short, counting only people who are in",
      "2 tank slots open (0/2)" in t and "4 healer slots open (1/5)" in t and "18 DPS slots open (0/18)" in t, t)
check("it links the post and gives the time", f"/channels/{G}/43/{5000 + kara}" in t and f"<t:{db.event(kara)['start_ts']}:R>" in t, t)
check("it pings nobody by default", alert.sent[0][1]["allowed_mentions"].roles is False, alert.sent[0][1]["allowed_mentions"])
check("a raid that isn't in the window yet waits", "Far away" not in t and db.event(far)["low_alerted"] == 0)
check("a full raid gets no message but is marked done", "Full house" not in t and db.event(full)["low_alerted"] == 1)
check("in an officers-only alert channel a raiders-only raid is mentioned too", "Raiders-only" in t, t)
run(B._run_lowsignup(int(time.time())))
check("each raid is alerted once", len(alert.sent) == 2)

i = inter(officer)
run(B.alerts_lowsignup.callback(i, 4, CH[42]))
db.update_event(hidden, low_alerted=0)
db.update_event(kara, low_alerted=0)
run(B._run_lowsignup(int(time.time())))
t = "\n".join(alerts_sent(CH[42]))
check("with a public alert channel a raiders-only raid is skipped, a public one is not",
      "Raiders-only" not in t and "Karazhan" in t and db.event(hidden)["low_alerted"] == 1, t)

i = inter(officer)
run(B.alerts_lowsignup.callback(i, 4, alert, 10, True))
check("minimum and ping are saved, and the channel can be changed back", db.setting(G, "lowsignup_min") == "10"
      and db.setting(G, "lowsignup_ping") == "1" and db.setting(G, "lowsignup_channel") == "41", text(i))
db.set_setting(G, "officer_role", 777)
roomy = short_raid("No limits", hours_ahead=2, caps=(0, 0, 0))
for n in range(3):
    db.set_signup(roomy, 70 + n, f"P{n}", "Mage", "Frost", "dps", "in", None)
run(B._run_lowsignup(int(time.time())))
last = alert.sent[-1]
check("below the minimum counts as short even without role limits", "No limits" in last[0][0] and "3/10 people in" in last[0][0],
      last[0][0])
check("ping_officers mentions the officer role, and only that role", last[0][0].startswith("<@&777>")
      and last[1]["allowed_mentions"].roles == [OFFICER_ROLE] and last[1]["allowed_mentions"].everyone is False, last)
i = inter(officer)
run(B.alerts_lowsignup.callback(i, 6))
check("running it again with just hours keeps the channel, minimum and ping", db.setting(G, "lowsignup_channel") == "41"
      and db.setting(G, "lowsignup_min") == "10" and db.setting(G, "lowsignup_ping") == "1" and "6 hours" in text(i), text(i))
db.set_setting(G, "officer_role", None)

soon_ts = int(time.time()) + 3600
check("a raid posted inside the window counts as checked", B.lowsignup_inside(G, soon_ts)
      and not B.lowsignup_inside(G, int(time.time()) + 7 * 3600))

moved = short_raid("Moved", hours_ahead=2)
db.update_event(moved, low_alerted=1)
i = inter(officer)
run(B.raid_edit.callback(i, moved, date="+2d 20:00"))
check("moving a raid out of the window re-arms the check", db.event(moved)["low_alerted"] == 0, text(i))
i = inter(officer)
run(B.raid_edit.callback(i, moved, date="+0d", at=(datetime.now(B.guild_tz(G)) + timedelta(hours=2)).strftime("%H:%M")))
check("moving it to less than the hours ahead counts it as checked",
      db.event(moved)["start_ts"] - time.time() > 3600 * 6 or db.event(moved)["low_alerted"] == 1, text(i))
db.delete_event(moved)

sent = len(alert.sent)
alert.fail_send = discord.HTTPException(types.SimpleNamespace(status=500, reason="x"), "boom")
flaky = short_raid("Flaky", hours_ahead=2)
run(B._run_lowsignup(int(time.time())))
check("a failed post is retried next minute", db.event(flaky)["low_alerted"] == 0)
alert.fail_send = None
run(B._run_lowsignup(int(time.time())))
check("and goes out once it works", len(alert.sent) == sent + 1 and "Flaky" in alert.sent[-1][0][0] and db.event(flaky)["low_alerted"] == 1)

i = inter(officer)
run(B.alerts_lowsignup.callback(i, 0))
check("0 turns it off", db.setting(G, "lowsignup_hours") == "0" and "No more" in text(i), text(i))
i = inter(officer)
run(B.config_show.callback(i))
check("/config show says it's off again", "Low-signup alert:** off" in text(i), text(i))
after = short_raid("After off", hours_ahead=2)
run(B._run_lowsignup(int(time.time())))
check("with it off nothing more is posted", len(alert.sent) == sent + 1 and db.event(after)["low_alerted"] == 0)

# ------------------------------------------------------------ Discord Scheduled Events
print("Discord Scheduled Events for raids (/alerts events)")
for e in db.upcoming_events(G, 0):
    db.delete_event(e["id"])
ES = discord.EventStatus
HTTP500 = discord.HTTPException(types.SimpleNamespace(status=500, reason="x"), "boom")


class FakeSched:
    def __init__(self, guild, sid, **k):
        self.guild, self.id, self.status = guild, sid, ES.scheduled
        for key in ("name", "start_time", "end_time", "location", "description"):
            setattr(self, key, k.get(key))
        self.kwargs = k

    async def edit(self, reason=None, **k):
        self.guild.calls.append(("edit", self.id, sorted(k)))
        for key, v in k.items():
            setattr(self, key, v)

    async def start(self, reason=None):
        self.guild.calls.append(("start", self.id)); self.status = ES.active

    async def end(self, reason=None):
        self.guild.calls.append(("end", self.id)); self.status = ES.completed

    async def cancel(self, reason=None):
        self.guild.calls.append(("cancel", self.id)); self.status = ES.cancelled

    async def delete(self, reason=None):
        if self.guild.fail:
            raise self.guild.fail
        self.guild.calls.append(("delete", self.id)); self.guild.events.pop(self.id, None)


class FakeGuild:
    def __init__(self, manage=True):
        self.id, self.events, self.calls, self.fail, self.next = G, {}, [], None, 8000
        self.default_role, self.roles = EVERY_R, [EVERY_R, RAIDER_R]
        self.me = types.SimpleNamespace(guild_permissions=types.SimpleNamespace(manage_events=manage, administrator=False))

    def get_scheduled_event(self, sid): return self.events.get(sid)

    async def fetch_scheduled_event(self, sid):
        if sid in self.events:
            return self.events[sid]
        raise NOT_FOUND

    async def create_scheduled_event(self, reason=None, **k):
        if self.fail:
            raise self.fail
        self.next += 1
        self.events[self.next] = FakeSched(self, self.next, **k)
        self.calls.append(("create", self.next))
        return self.events[self.next]


guild = FakeGuild()
B.bot.get_guild = lambda gid: guild if gid == G else None
CH[51] = RoleChannel(51, {EVERY_R, RAIDER_R}); CH[51].name = "raids"  # everyone can see it
CH[52] = RoleChannel(52, {RAIDER_R})  # raiders only: no event, ever


def sched_raid(title, channel=51, hours_ahead=26, description="Bring flasks"):
    eid = db.create_event(G, channel, title, description, int(time.time()) + hours_ahead * 3600, 1, 2, 5, 18)
    db.set_message_id(eid, 5500 + eid)
    return eid


def sid(eid): return db.event(eid)["sched_event_id"]
def sched_of(eid): return guild.events.get(sid(eid))
def tick(at=None): run(B._run_sched(int(time.time()) if at is None else at))


def then_background(coro):
    """Run a command and then the background syncs it started (sched_soon), the way the bot's loop would."""
    async def go():
        out = await coro
        await asyncio.gather(*list(B._sched_tasks))
        return out
    return run(go())


check("it's off by default", db.setting(G, "sched_events") is None and not B.sched_events_on(G))
i = inter(officer)
run(B.config_show.callback(i))
check("/config show says it's off", "Discord events for raids:** off" in text(i), text(i))
ony = sched_raid("Off for now")
tick()
check("while off no event is made", not guild.calls and sid(ony) == 0)
run(B.post_event(CH[51], guild, 1, "Posted while off", "", int(time.time()) + 86400, 2, 5, 18))
run(asyncio.sleep(0))
check("posting a raid while off makes no event either", not guild.calls)

i = inter(officer); i.guild = guild
run(B.alerts_events.callback(i, True))
check("turning it on saves the switch, privately confirmed", B.sched_events_on(G) and i.response.sent[0][1].get("ephemeral") is True
      and "Events" in text(i) and "Manage Events" not in text(i), text(i))
i = inter(officer)
run(B.config_show.callback(i))
check("/config show describes it", "Discord events for raids:** on, 3 hours long" in text(i), text(i))

public = sched_raid("Karazhan")
private = sched_raid("Raiders-only night", channel=52)
unposted = db.create_event(G, 51, "Not posted yet", "", int(time.time()) + 86400, 1, 2, 5, 18)
tick()
se = sched_of(public)
check("each upcoming raid in a public channel gets an event within a minute", se is not None and sched_of(ony) is not None)
check("a raid in a channel some members can't see gets none", sid(private) == 0, sid(private))
check("a raid without a post yet gets none", sid(unposted) == 0)
ev = db.event(public)
check("the event is an external, members-only event with the raid's title and time",
      se.kwargs.get("name") == "Karazhan" and se.start_time.timestamp() == ev["start_ts"]
      and (se.end_time - se.start_time) == timedelta(hours=3), se.kwargs)
check("its location is the link to the post, and the description keeps the raid's text",
      se.location == f"https://discord.com/channels/{G}/51/{ev['message_id']}" and se.description.startswith("Bring flasks")
      and "#raids" in se.description and len(se.location) <= 100, (se.location, se.description))
calls = len(guild.calls)
tick()
check("nothing changed: the event isn't touched again", len(guild.calls) == calls, guild.calls[calls:])

i = inter(officer)
then_background(B.raid_edit.callback(i, public, title="Karazhan (alts)", date="+3d 20:00", notify=False))
se = sched_of(public)
check("editing the raid updates its event (title and time)", se.name == "Karazhan (alts)"
      and se.start_time.timestamp() == db.event(public)["start_ts"] and any(c[0] == "edit" for c in guild.calls[calls:]),
      guild.calls[calls:])

posted, _, _ = then_background(B.post_event(CH[51], guild, 1, "Gruul", "", int(time.time()) + 86400, 2, 5, 18))
check("a newly posted raid gets its event straight away", sched_of(posted) is not None)

i = inter(officer)
cancelled_sid = sid(public)
run(B.raid_cancel.callback(i, public))
check("cancelling the raid deletes its event", cancelled_sid not in guild.events and ("delete", cancelled_sid) in guild.calls)
i = inter(officer)
then_background(B.raid_restore.callback(i, public))
check("restoring it makes a new event", sid(public) > 0 and sid(public) != cancelled_sid and sched_of(public) is not None,
      sid(public))

st = db.event(public)["start_ts"]
tick(st + 30)
check("when the raid starts, its event starts", sched_of(public).status == ES.active and ("start", sid(public)) in guild.calls)
done = sid(public)
tick(st + 3 * 3600 + 30)
check("after `length` hours the event ends and the bot lets it go", ("end", done) in guild.calls and sid(public) == -1)
calls = len(guild.calls)
tick(st + 4 * 3600)
check("an ended event isn't touched again", len(guild.calls) == calls)

hand = sched_raid("Deleted by hand")
tick()
guild.events.pop(sid(hand))
tick()
check("an event deleted by hand isn't made again", sid(hand) == -1 and not any(c[0] == "create" for c in guild.calls[calls + 1:]))

CH[53] = RoleChannel(53, {EVERY_R, RAIDER_R})
moving = sched_raid("Goes private", channel=53)
tick()
gone = sid(moving)
CH[53].seen_by = {RAIDER_R}
tick()
check("a raid whose channel becomes private loses its event", gone > 0 and gone not in guild.events and sid(moving) == 0)

flaky = sched_raid("Flaky")
guild.fail = HTTP500
for n in range(4):
    tick()
check("a failed create is retried next minute", sid(flaky) == 0)
guild.fail = None
tick()
check("and goes through once Discord works again", sid(flaky) > 0)

stuck = sched_raid("Stuck")
guild.fail = HTTP500
for n in range(B.SCHED_MAX_FAILS):
    tick()
check("after repeated failures the bot stops trying, so it doesn't log an error every minute", sid(stuck) == -1)
guild.fail = None

nope = FakeGuild(manage=False)
B.bot.get_guild = lambda gid: nope if gid == G else None
i = inter(officer); i.guild = nope
run(B.alerts_events.callback(i, True, 4))
check("without Manage Events the admin is told what to give the bot", "Manage Events" in text(i) and B.sched_length(G) == 4, text(i))
noperm = sched_raid("No permission")
tick()
check("and nothing is created or counted as an error until it has it", not nope.calls and sid(noperm) == 0)
B.bot.get_guild = lambda gid: guild if gid == G else None
tick()
check("once it has the permission the event is made", sid(noperm) > 0)

before = {e["sched_event_id"] for e in db.sched_events_of(G)}
i = inter(officer); i.guild = guild
then_background(B.alerts_events.callback(i, False))
check("turning it off deletes the events the bot made", before and not db.sched_events_of(G)
      and all(("delete", x) in guild.calls for x in before) and not B.sched_events_on(G), (before, guild.calls[-5:]))
check("and says so", "won't get Discord events" in text(i), text(i))
calls = len(guild.calls)
sched_raid("After off")
tick()
check("with it off no new events are made", len(guild.calls) == calls)

print("a database from before this change gets the column")
import sqlite3  # noqa: E402
old_db = tmp / "old_sched.db"
con = sqlite3.connect(old_db)
con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,"
            " message_id INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',"
            " start_ts INTEGER NOT NULL, creator_id INTEGER NOT NULL, cap_tank INTEGER NOT NULL DEFAULT 0,"
            " cap_healer INTEGER NOT NULL DEFAULT 0, cap_dps INTEGER NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0,"
            " reminded INTEGER NOT NULL DEFAULT 0)")
con.execute("INSERT INTO events(guild_id, channel_id, title, start_ts, creator_id) VALUES(1, 2, 'Old', 1, 3)")
con.commit(); con.close()
migrated = dbmod.DB(str(old_db))
check("the migration adds sched_event_id with 0 for old raids", migrated.event(1)["sched_event_id"] == 0)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
