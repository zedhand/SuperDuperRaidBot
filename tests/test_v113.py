"""Offline tests for v1.13/v1.14: the WoW version presets, and confirm/undo for /raid cancel and /raid remove
(more suites are added below as features land).

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

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
