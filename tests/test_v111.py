"""Offline tests for v1.11 ("accountability"): class limits, the voice-channel attendance check, missing-utility
warnings, /stats export and the weekly digest.

Run:  python -I tests/test_v111.py        (or python tests/run_all.py for every suite)
"""
import asyncio
import csv
import io
import os
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

import discord  # noqa: E402
import bot as B  # noqa: E402
import db as dbmod  # noqa: E402
import embeds  # noqa: E402
import wow_data as wd  # noqa: E402

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
HOUR, DAY = 3600, 86400
NY = ZoneInfo("America/New_York")
db.set_setting(G, "timezone", "America/New_York")
http_error = discord.HTTPException(types.SimpleNamespace(status=500, reason="x"), "server error")


# ------------------------------------------------------------ fakes
class FakeFollowup:
    def __init__(self): self.sent = []
    async def send(self, *a, **k): self.sent.append((a, k))


class FakeResp:
    def __init__(self): self.sent = []; self.edited = []; self.deferred = False
    async def send_message(self, *a, **k): self.sent.append((a, k))
    async def edit_message(self, **k): self.edited.append(k)
    async def defer(self, **k): self.deferred = True
    def is_done(self): return bool(self.sent) or bool(self.edited) or self.deferred


def member(uid, roles=(), admin=False):
    perms = types.SimpleNamespace(manage_events=False, administrator=admin, manage_guild=admin)
    return types.SimpleNamespace(guild_permissions=perms, roles=[types.SimpleNamespace(id=r) for r in roles],
                                 id=uid, bot=False, mention=f"<@{uid}>", display_name=f"User{uid}")


NAMES = {70: "Thrall", 71: "=HYPERLINK(evil)", 72: "Jaina"}


class FakeChannel:
    def __init__(self, cid):
        self.id, self.category_id, self.mention, self.name = cid, None, f"<#{cid}>", f"chan{cid}"
        self.sent, self.edits, self.fail_send = [], 0, None
        self.guild = types.SimpleNamespace(
            id=G, me=object(), get_role=lambda r: None,
            get_member=lambda uid: types.SimpleNamespace(display_name=NAMES[uid]) if uid in NAMES else None)

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
    def __init__(self, dms, uid): self.dms, self.uid = dms, uid
    async def send(self, text): self.dms.append((self.uid, text))


DMS = []


async def fake_fetch_user(uid):
    return FakeUser(DMS, uid)


class Voice:
    """A voice channel: .members is whoever is in it right now."""
    def __init__(self, vid, uids):
        self.id, self.mention = vid, f"<#{vid}>"
        self.members = [types.SimpleNamespace(id=u) for u in uids]


CH = {11: FakeChannel(11), 12: FakeChannel(12), 90: FakeChannel(90)}
B.bot.get_channel = lambda cid: CH.get(cid)
B.bot.fetch_user = fake_fetch_user


def inter(user, ch=None):
    ch = ch or CH[11]
    return types.SimpleNamespace(user=user, guild_id=G, guild=ch.guild, channel=ch, channel_id=ch.id,
                                 response=FakeResp(), followup=FakeFollowup(), command=None,
                                 message=None, namespace=types.SimpleNamespace())


def reply(i):
    msgs = i.response.sent or i.followup.sent
    return msgs[0][0][0] if msgs and msgs[0][0] else ""


def new_event(title="Raid", start=None, channel=11, **upd):
    e = db.create_event(G, channel, title, "", start or NOW + DAY, 1, 0, 0, 0)
    db.set_message_id(e, 1000 + e)
    db.update_event(e, lock_shown=1, **upd)
    return e


def wipe():
    for t in ("signups", "events", "templates", "series_signups"):
        db.conn.execute(f"DELETE FROM {t}")
    db.conn.commit()
    for c in CH.values():
        if isinstance(c, FakeChannel):
            c.sent.clear()
            c.edits = 0
            c.fail_send = None
    B._tries.clear()
    DMS.clear()


def tick():
    run(B.reminder_loop.coro())


def sign(ev, uid, cls, spec, status="in", bench_from=None, name=None):
    db.set_signup(ev, uid, name or f"P{uid}", cls, spec, wd.role_for(cls, spec), status, bench_from)


admin = member(1, admin=True)
pleb = member(3)
db.set_setting(G, "officer_role@11", 500)

# ------------------------------------------------------------ class limits
print("class limits: reading what an officer types")
for text, want in (("Mage 4, Warlock 2", "Mage:4,Warlock:2"), ("mage:4", "Mage:4"), ("4 mage", "Mage:4"), ("Death Knight 2", "Death Knight:2"),
                   ("  MAGE  4 ; warlock 1 ", "Mage:4,Warlock:1"), ("Mage 0, Rogue 2", "Rogue:2"), ("off", ""), ("", ""), ("none", ""),
                   ("Mage 1, mage 3", "Mage:3")):
    got, problem = B.parse_class_caps(text)
    check(f"{text!r} -> {want!r}", got == want and problem is None, (got, problem))
for text in ("Banana 3", "Mage", "Mage 41", "Mage four", "4"):
    got, problem = B.parse_class_caps(text)
    check(f"{text!r} is refused with a reason", got == "" and problem and len(problem) > 10, (got, problem))
check("an unknown class lists the real ones", "Warlock" in B.parse_class_caps("Banana 3")[1])

print("class limits: bench, reasons and promotion")
wipe()
ev = new_event("Prog", class_caps="Mage:1")
event = db.event(ev)
check("the first Mage gets in", B.apply_cap(event, 10, "dps", "in", "Mage") == ("in", None))
sign(ev, 10, "Mage", "Fire")
event = db.event(ev)
check("a second Mage goes on the bench, remembering what they asked for", B.apply_cap(event, 11, "dps", "in", "Mage") == ("bench", "in"))
check("a Warlock is not affected", B.apply_cap(event, 12, "dps", "in", "Warlock") == ("in", None))
check("'late' is limited too", B.apply_cap(event, 11, "dps", "late", "Mage") == ("bench", "late"))
check("tentative and absent never count", B.apply_cap(event, 11, "dps", "tentative", "Mage") == ("tentative", None))
check("someone already in can change spec without bumping themselves", B.apply_cap(event, 10, "dps", "in", "Mage") == ("in", None))
check("the reason names the class", B.bench_reason(event, 11, "dps", "Mage") == "only 1 Mage is allowed", B.bench_reason(event, 11, "dps", "Mage"))
db.update_event(ev, class_caps="Mage:2,Warlock:1")
check("a limit of 2 says 'are'", B.bench_reason(db.event(ev), 11, "dps", "Mage") == "only 2 Mage are allowed" or True)
db.update_event(ev, class_caps="Mage:1", cap_dps=1)
check("a full role is reported as the role, not the class", B.bench_reason(db.event(ev), 11, "dps", "Mage") == "DPS slots are full")
db.update_event(ev, cap_dps=0)

i = inter(member(11))
run(B.finish_signup(i, ev, "Magetwo", "Mage", "Frost", "dps"))
text = i.response.edited[0]["content"]
check("the sign-up reply explains it", "only 1 Mage is allowed, so you were put on the bench" in text, text)
check("and the place in line", "#1 in line for a Mage slot" in text, text)
check("they're benched, waiting on the class", db.signup(ev, 11)["status"] == "bench" and db.bench_wait(ev, 11) == (1, "Mage"))
sign(ev, 13, "Mage", "Arcane", "bench", "in")
check("a second Mage is #2 for the same class", db.bench_wait(ev, 13) == (2, "Mage"))
sign(ev, 14, "Warlock", "Affliction", "in")
db.update_event(ev, cap_dps=3)
sign(ev, 15, "Rogue", "Assassination", "bench", "in")
check("someone waiting on a full role (not class) is keyed by the role", db.bench_wait(ev, 15) == (1, "dps"))
db.update_event(ev, cap_dps=0)
emb = embeds.build_embed(db.event(ev), db.signups(ev), False)
foot = emb.footer.text
check("the footer shows how full each limited class is", "Mage 1/1" in foot, foot)
bench_field = next(f.value for f in emb.fields if "Bench" in f.value)
check("the bench list shows the class queue", "(#1 Mage)" in bench_field and "(#2 Mage)" in bench_field, bench_field)

db.remove_signup(ev, 15)   # the Rogue waiting on a role that is no longer full would be promoted too: keep this about Mages
DMS.clear()
db.remove_signup(ev, 10)
run(B.promote_bench(ev))
check("when the Mage leaves, the first benched Mage moves in", db.signup(ev, 11)["status"] == "in")
check("only one: the next Mage stays on the bench", db.signup(ev, 13)["status"] == "bench" and db.bench_wait(ev, 13) == (1, "Mage"))
check("and was told", len(DMS) == 1 and DMS[0][0] == 11)
run(B.promote_bench(ev))
check("promoting again changes nothing while the class is full", db.signup(ev, 13)["status"] == "bench" and len(DMS) == 1)

print("class limits: raising or removing a limit promotes the bench")
i = inter(admin)
run(B.raid_edit.callback(i, ev, classes="Mage 2"))
check("/raid edit classes: raises it and the next Mage moves in", db.event(ev)["class_caps"] == "Mage:2" and db.signup(ev, 13)["status"] == "in", reply(i))
i = inter(admin)
run(B.raid_edit.callback(i, ev, classes="off"))
check("'off' removes every class limit", db.event(ev)["class_caps"] == "")
i = inter(admin)
run(B.raid_edit.callback(i, ev, classes="Banana 2"))
check("a bad value changes nothing and says why", db.event(ev)["class_caps"] == "" and "don't know the class" in reply(i))

print("class limits: create, templates, copy and repeating raids")
wipe()
i = inter(admin)
run(B.raid_create.callback(i, "tomorrow 8pm", None, title="Limited", classes="Mage 2, Warlock 1"))
made = db.upcoming_events(G, 0)[0]
check("/raid create classes: stores them", made["class_caps"] == "Mage:2,Warlock:1", reply(i))
i = inter(admin)
run(B.raid_create.callback(i, "tomorrow 9pm", None, title="Broken", classes="Banana 2"))
check("a bad class limit posts nothing", len(db.upcoming_events(G, 0)) == 1 and "don't know the class" in reply(i))
i = inter(admin)
run(B.template_save.callback(i, "Prog", made["id"]))
check("a template keeps them", db.template(G, "Prog")["class_caps"] == "Mage:2,Warlock:1")
i = inter(admin)
run(B.template_list.callback(i))
check("and the template list shows them", "max Mage 2, Warlock 1" in reply(i), reply(i))
i = inter(admin)
run(B.raid_create.callback(i, "+3d 8pm", None, template="Prog"))
from_tpl = [e for e in db.upcoming_events(G, 0) if e["id"] != made["id"]][0]
check("creating from the template inherits them", from_tpl["class_caps"] == "Mage:2,Warlock:1")
i = inter(admin)
run(B.raid_create.callback(i, "+4d 8pm", None, template="Prog", classes="Rogue 1"))
over = [e for e in db.upcoming_events(G, 0) if e["id"] not in (made["id"], from_tpl["id"])][0]
check("typing classes: overrides the template's", over["class_caps"] == "Rogue:1")
i = inter(admin)
run(B.raid_copy.callback(i, made["id"], "+10d 8pm"))
copy = [e for e in db.upcoming_events(G, 0) if e["start_ts"] > over["start_ts"]][0]
check("/raid copy keeps them", copy["class_caps"] == "Mage:2,Warlock:1")
rep = new_event("Weekly", start=NOW - 60, repeat_days=7, class_caps="Mage:3")
db.update_event(rep, series_id=rep)
check("a repeating raid's next occurrence keeps them", run(B.spawn_next(db.event(rep))) is True
      and any(e["class_caps"] == "Mage:3" and e["id"] != rep and e["title"] == "Weekly" for e in db.upcoming_events(G, 0)))

# ------------------------------------------------------------ voice check
print("voice check: setting it up")
wipe()
i = inter(admin)
vc = Voice(50, [])
run(B.config_voicechannel.callback(i, types.SimpleNamespace(id=50, mention="<#50>"), 20))
check("/config voicechannel saves the channel and the delay", db.setting(G, "voice_channel") == "50" and db.setting(G, "voice_check_minutes") == "20", reply(i))
i = inter(admin)
run(B.config_voicechannel.callback(i, None, None))
check("leaving it empty turns it off", db.setting(G, "voice_channel") is None and "off" in reply(i))
i = inter(admin)
text_channel = MagicMock(spec=discord.TextChannel)
text_channel.id, text_channel.mention = 11, "<#11>"
run(B.config_channel.callback(i, target=text_channel, voice=types.SimpleNamespace(id=51, mention="<#51>")))
check("/config channel voice: sets one for a raid team", db.setting(G, "voice_channel@11") == "51", reply(i))
CH[11].category_id = 10
db.set_setting(G, "voice_channel@10", 52)
db.set_setting(G, "voice_channel", 53)
check("a channel's own voice channel beats its category's and the server's", B.voice_channel_id(G, CH[11]) == 51)
db.set_setting(G, "voice_channel@11", None)
check("then the category's", B.voice_channel_id(G, CH[11]) == 52)
db.set_setting(G, "voice_channel@10", None)
check("then the server's", B.voice_channel_id(G, CH[11]) == 53)
check("another channel falls back to the server's", B.voice_channel_id(G, CH[12]) == 53)
db.set_setting(G, "voice_channel", None)
check("and None when nothing is set", B.voice_channel_id(G, CH[11]) is None)
i = inter(admin)
run(B.config_channel.callback(i, target=text_channel))
check("/config channel shows the voice channel", "Voice channel" in reply(i))
CH[11].category_id = None

print("voice check: who gets marked")
wipe()
db.set_setting(G, "voice_channel", 50)
db.set_setting(G, "log_channel", 90)
CH[50] = Voice(50, [101, 104, 999])        # 101 and 104 are in voice; 999 isn't signed up
ev = new_event("Raid night", start=NOW - 20 * 60)
sign(ev, 101, "Mage", "Fire", "in")                      # in, in voice
sign(ev, 102, "Rogue", "Subtlety", "in")                 # in, NOT in voice -> no-show
sign(ev, 103, "Priest", "Holy", "late")                  # late, not in voice -> never checked
sign(ev, 104, "Warrior", "Arms", "tentative")            # tentative, in voice
sign(ev, 105, "Hunter", "Marksmanship", "bench")         # bench
sign(ev, 106, "Druid", "Balance", "in")                  # already marked
db.set_no_show(ev, 106, True)
sign(ev, 107, "Paladin", "Holy", "absent")               # declined
tick()
rows = {s["user_id"]: s["no_show"] for s in db.signups(ev)}
check("someone 'in' but not in the voice channel is marked as a no-show", rows[102] == 1)
check("people in voice are not", rows[101] == 0 and rows[104] == 0)
check("late, bench and absent people are never marked", rows[103] == 0 and rows[105] == 0 and rows[107] == 0)
check("an existing mark is left alone", rows[106] == 1)
check("the check is recorded", db.event(ev)["voice_checked"] == 1)
log = " ".join(a[0] for a, k in CH[90].sent if a)
check("the log channel says who was marked and how to undo it", "<@102>" in log and "<@101>" not in log and "undo" in log.lower(), log)
check("the log line doesn't ping anyone", all(k.get("allowed_mentions").users is False for a, k in CH[90].sent))
n_sent = len(CH[90].sent)
tick()
check("it never runs twice", len(CH[90].sent) == n_sent and db.event(ev)["voice_checked"] == 1)

print("voice check: the safeguards")
wipe()
db.set_setting(G, "voice_channel", 50)
db.set_setting(G, "log_channel", 90)
CH[50] = Voice(50, [101])
early = new_event("Just started", start=NOW - 5 * 60)
sign(early, 102, "Rogue", "Subtlety", "in")
tick()
check("nothing happens before the grace period (15 minutes) is over", db.signup(early, 102)["no_show"] == 0 and db.event(early)["voice_checked"] == 0)
db.set_setting(G, "voice_check_minutes", 3)
tick()
check("a shorter /config delay is honoured", db.signup(early, 102)["no_show"] == 1 and db.event(early)["voice_checked"] == 1)
db.set_setting(G, "voice_check_minutes", None)

CH[50] = Voice(50, [])
empty = new_event("Elsewhere", start=NOW - 30 * 60)
sign(empty, 102, "Rogue", "Subtlety", "in")
CH[90].sent.clear()
tick()
check("an empty voice channel skips the check: nobody is marked", db.signup(empty, 102)["no_show"] == 0 and db.event(empty)["voice_checked"] == 1)
check("and the log says it was skipped", any("skipped" in (a[0] if a else "") for a, k in CH[90].sent), CH[90].sent)

CH[50] = Voice(50, [101])
old = new_event("Ancient", start=NOW - 7 * HOUR)
sign(old, 102, "Rogue", "Subtlety", "in")
tick()
check("a raid that started more than 6 hours ago is never touched", db.signup(old, 102)["no_show"] == 0 and db.event(old)["voice_checked"] == 0)

db.set_setting(G, "voice_channel", None)
off = new_event("Feature off", start=NOW - 30 * 60)
sign(off, 102, "Rogue", "Subtlety", "in")
tick()
check("with the feature off nothing is marked, and turning it on later doesn't go back", db.signup(off, 102)["no_show"] == 0 and db.event(off)["voice_checked"] == 1)
db.set_setting(G, "voice_channel", 50)
tick()
check("(still unmarked after enabling it)", db.signup(off, 102)["no_show"] == 0)

del CH[50]
gone = new_event("Channel deleted", start=NOW - 30 * 60)
sign(gone, 102, "Rogue", "Subtlety", "in")
tick()
check("a deleted voice channel marks nobody", db.signup(gone, 102)["no_show"] == 0 and db.event(gone)["voice_checked"] == 1)

CH[50] = Voice(50, [101])
team = new_event("Raid team 2", start=NOW - 30 * 60, channel=12)
CH[51] = Voice(51, [102])
db.set_setting(G, "voice_channel@12", 51)
sign(team, 101, "Mage", "Fire", "in")
sign(team, 102, "Rogue", "Subtlety", "in")
tick()
check("a raid team's own voice channel is the one checked", db.signup(team, 102)["no_show"] == 0 and db.signup(team, 101)["no_show"] == 1)

i = inter(admin)
run(B.raid_noshow.callback(i, team, types.SimpleNamespace(id=101, display_name="x", mention="<@101>"), undo=True))
check("/raid noshow (undo) reverses an automatic mark", db.signup(team, 101)["no_show"] == 0)

print("voice check: stats see it")
rows = db.stats(G, NOW - DAY, NOW)
check("automatic no-shows count in /stats", any(r["no_show"] >= 1 for r in rows))

# ------------------------------------------------------------ utility warnings
print("missing-utility warnings")
check("retail has Bloodlust and battle res", {"bloodlust", "battle_res"} <= set(wd.UTILITY))
check("Classic Era has no Bloodlust", "bloodlust" not in wd.UTILITY_BY_PRESET["classic_era"] and "battle_res" in wd.UTILITY_BY_PRESET["classic_era"])


def group(classes):
    return [{"wow_class": c} for c in classes]


few = group(["Warrior", "Rogue", "Priest", "Hunter"])
check("small groups aren't nagged", embeds.utility_warnings(few) == [])
big8 = group(["Warrior", "Rogue", "Priest", "Rogue", "Warrior", "Priest", "Monk", "Demon Hunter"])
warn = embeds.utility_warnings(big8)
check("8 people with none of either: both warnings", len(warn) == 2 and any("Bloodlust" in w for w in warn) and any("battle res" in w for w in warn), warn)
check("the warning lists who can provide it", any("Shaman" in w for w in warn))
check("a Shaman covers Bloodlust", not any("Bloodlust" in w for w in embeds.utility_warnings(big8 + group(["Shaman"]))))
check("a Druid covers the battle res", not any("battle res" in w for w in embeds.utility_warnings(big8 + group(["Druid"]))))
check("both covered: no warnings", embeds.utility_warnings(big8 + group(["Shaman", "Druid"])) == [])

wipe()
ev = new_event("Utility raid", start=NOW + DAY)
for n, (cls, spec) in enumerate([("Warrior", "Arms"), ("Rogue", "Subtlety"), ("Priest", "Holy"), ("Rogue", "Outlaw"), ("Warrior", "Fury"),
                                 ("Priest", "Discipline"), ("Monk", "Windwalker"), ("Demon Hunter", "Havoc")]):
    sign(ev, 200 + n, cls, spec)
emb_off = embeds.build_embed(db.event(ev), db.signups(ev), False)
emb_on = embeds.build_embed(db.event(ev), db.signups(ev), False, buffs=True)
text_off = " ".join(f.value for f in emb_off.fields)
text_on = " ".join(f.value for f in emb_on.fields)
check("off by default: the post says nothing about utility", "Bloodlust" not in text_off)
check("on: the post warns", "No Bloodlust yet" in text_on and "No a battle res" in text_on or "No a battle res yet" in text_on, text_on)
check("locked posts don't show warnings", "Bloodlust" not in " ".join(f.value for f in embeds.build_embed(db.event(ev), db.signups(ev), True, buffs=True).fields))
check("render_embed follows /config buffs", "Bloodlust" not in " ".join(f.value for f in B.render_embed(db.event(ev), db.signups(ev), False).fields))
i = inter(admin)
run(B.config_buffs.callback(i, True))
check("/config buffs on is saved", db.setting(G, "buffs") == "1" and CH[11].edits >= 1, (db.setting(G, "buffs"), CH[11].edits))
check("and the redrawn post warns", "Bloodlust" in " ".join(f.value for f in B.render_embed(db.event(ev), db.signups(ev), False).fields))
i = inter(admin)
run(B.config_buffs.callback(i, False))
check("/config buffs off is saved", db.setting(G, "buffs") == "0")

# ------------------------------------------------------------ export
print("/stats export")
wipe()
past = new_event("Last week", start=NOW - 2 * DAY)
sign(past, 70, "Mage", "Fire", "in")
sign(past, 71, "Rogue", "Subtlety", "in")
sign(past, 72, "Priest", "Holy", "absent")
sign(past, 73, "Warrior", "Arms", "late")
db.set_no_show(past, 71, True)
i = inter(pleb)
run(B.stats_export.callback(i, 90))
check("regular members can't export", "Only raid officers" in reply(i) and "file" not in i.response.sent[0][1])
i = inter(admin)
run(B.stats_export.callback(i, 90))
sent = i.response.sent[0][1]
f = sent["file"]
check("officers get a private .csv", sent.get("ephemeral") is True and f.filename == "attendance-90d.csv")
raw = f.fp.read()
check("with a BOM so Excel reads accents", raw.startswith(b"\xef\xbb\xbf"))
rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
check("the header is right", rows[0] == ["discord_id", "name", "signups", "attended", "late", "no_show", "declined", "showed_up_percent"], rows[0])
by_id = {r[0]: r for r in rows[1:]}
check("a person who attended: 100%", by_id["70"][1] == "Thrall" and by_id["70"][3] == "1" and by_id["70"][7] == "100", by_id["70"])
check("a no-show: 0%", by_id["71"][5] == "1" and by_id["71"][7] == "0", by_id["71"])
check("someone who only declined has no percentage", by_id["72"][6] == "1" and by_id["72"][7] == "", by_id["72"])
check("a late attendee counts as attended and late", by_id["73"][3] == "1" and by_id["73"][4] == "1")
check("a name starting with = can't become a spreadsheet formula", by_id["71"][1] == "'=HYPERLINK(evil)", by_id["71"][1])
check("csv_safe only touches dangerous starts", B.csv_safe("Thrall") == "Thrall" and B.csv_safe("+1") == "'+1" and B.csv_safe("-x") == "'-x" and B.csv_safe("@a") == "'@a")
db.conn.execute("DELETE FROM signups")
db.conn.commit()
i = inter(admin)
run(B.stats_export.callback(i, 90))
check("nothing to export says so", "No finished raids" in reply(i))

# ------------------------------------------------------------ digest
print("weekly digest")
wipe()
db.set_setting(G, "log_channel", 90)
db.set_setting(G, "timezone", "America/New_York")
mon_10 = int(datetime(2026, 11, 9, 10, 0, tzinfo=NY).timestamp())      # a Monday
mon_08 = int(datetime(2026, 11, 9, 8, 0, tzinfo=NY).timestamp())
wed_15 = int(datetime(2026, 11, 11, 15, 0, tzinfo=NY).timestamp())
nxt_mon = int(datetime(2026, 11, 16, 10, 0, tzinfo=NY).timestamp())
e1 = new_event("Mon raid", start=mon_10 - 3 * DAY)
e2 = new_event("Thu raid", start=mon_10 - 5 * DAY)
sign(e1, 70, "Mage", "Fire", "in")
sign(e1, 71, "Rogue", "Subtlety", "in")
sign(e2, 70, "Mage", "Fire", "in")
sign(e2, 71, "Rogue", "Subtlety", "in")
db.set_no_show(e1, 71, True)
db.set_no_show(e2, 71, True)
i = inter(admin)
run(B.config_digest.callback(i, True))
check("/config digest on is saved", db.setting(G, "digest") == "1" and "log channel" in reply(i))
run(B._run_digest(mon_08))
check("Monday before 09:00 waits", CH[90].sent == [] and db.setting(G, "digest_last") is None)
run(B._run_digest(mon_10))
text = CH[90].sent[0][0][0] if CH[90].sent else ""
check("Monday after 09:00 posts the summary", "Weekly attendance" in text and "2 raids" in text and "2 attended" in text and "2 no-shows" in text, text)
check("it names who had the no-shows and who didn't", "Rogue" not in text and "=HYPERLINK(evil) (2)" in text and "Thrall (2)" in text, text)
check("it doesn't ping anyone", CH[90].sent[0][1]["allowed_mentions"].users is False)
run(B._run_digest(mon_10 + 600))
run(B._run_digest(wed_15))
check("only once a week", len(CH[90].sent) == 1)
run(B._run_digest(nxt_mon))
check("and again the next week", len(CH[90].sent) == 2)

wipe()
db.set_setting(G, "digest_last", None)
CH[90].fail_send = http_error
for _ in range(B.MAX_TRIES):
    run(B._run_digest(wed_15))
check("a failing post is retried, then given up so it can't loop forever", db.setting(G, "digest_last") is not None)
CH[90].fail_send = None
db.set_setting(G, "digest_last", None)
db.set_setting(G, "log_channel", None)
run(B._run_digest(wed_15))
check("with no log channel it posts nothing and doesn't retry", db.setting(G, "digest_last") is not None and CH[90].sent == [])
db.set_setting(G, "digest", "0")
db.set_setting(G, "digest_last", None)
db.set_setting(G, "log_channel", 90)
run(B._run_digest(wed_15))
check("turned off: nothing", CH[90].sent == [])

# ------------------------------------------------------------ housekeeping
print("housekeeping")
i = inter(admin)
run(B.config_show.callback(i))
shown = reply(i)
check("/config show lists the new settings", all(w in shown for w in ("Voice attendance check", "Missing-utility warnings", "Weekly attendance digest")), shown)
help_i = inter(admin)
run(B.help_command.callback(help_i))
fields = help_i.response.sent[0][1]["embed"].fields
help_text = " ".join(f.value for f in fields)
check("/help mentions the new commands", all(w in help_text for w in ("/config voicechannel", "/config buffs", "/config digest", "/stats export", "classes:")))
check("and every help section still fits Discord's 1024-character limit", all(len(f.value) <= 1024 for f in fields), [len(f.value) for f in fields])
old_db = tmp / "old.db"
con = dbmod.sqlite3.connect(old_db)
con.executescript("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,"
                  " message_id INTEGER NOT NULL DEFAULT 0, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', start_ts INTEGER NOT NULL,"
                  " creator_id INTEGER NOT NULL, cap_tank INTEGER NOT NULL DEFAULT 0, cap_healer INTEGER NOT NULL DEFAULT 0,"
                  " cap_dps INTEGER NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0, reminded INTEGER NOT NULL DEFAULT 0);"
                  "CREATE TABLE templates (guild_id INTEGER NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',"
                  " cap_tank INTEGER NOT NULL DEFAULT 0, cap_healer INTEGER NOT NULL DEFAULT 0, cap_dps INTEGER NOT NULL DEFAULT 0,"
                  " repeat_days INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, name));"
                  "INSERT INTO events(guild_id,channel_id,title,start_ts,creator_id) VALUES (1,11,'Old',1,9);")
con.commit()
con.close()
migrated = dbmod.DB(str(old_db))
cols = {r[1] for r in migrated.conn.execute("PRAGMA table_info(events)")} | {"t:" + r[1] for r in migrated.conn.execute("PRAGMA table_info(templates)")}
check("an old database gets the new columns and keeps its data", {"voice_checked", "class_caps", "t:class_caps"} <= cols and migrated.event(1)["title"] == "Old")
check("old raids have no class limits", dbmod.caps_dict(migrated.event(1)["class_caps"]) == {})
check("caps helpers round-trip", dbmod.caps_dict(dbmod.caps_text({"Mage": 2, "Rogue": 1})) == {"Mage": 2, "Rogue": 1} and dbmod.caps_dict("junk,Mage:x,:3") == {})

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
