"""Offline tests for the fixes from the v1.13.2 audit: reminders on closed raids, /always and channel visibility, the
privacy rule (private threads, member overwrites), scoped officers and /stats, @everyone guards, per-server backups,
/forgetme, odd install paths, and the reliability fixes that follow.

Run:  python -I tests/test_audit.py        (or python tests/run_all.py for every suite)
"""
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

import discord  # noqa: E402
import bot as B  # noqa: E402
import db as dbmod  # noqa: E402

B.db = dbmod.DB(str(tmp / "audit.db"))
db = B.db
run = asyncio.run
passed = failed = 0
G = 1
NOW = int(time.time())


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


def perms(**k):
    base = dict(view_channel=True, send_messages=True, embed_links=True, manage_threads=False,
                mention_everyone=False, administrator=False, manage_events=False, manage_guild=False)
    base.update(k)
    return types.SimpleNamespace(**base)


class Role:
    def __init__(self, rid, default=False, mentionable=True):
        self.id, self.name, self.mention, self._default, self.mentionable = rid, f"role{rid}", f"<@&{rid}>", default, mentionable

    def is_default(self): return self._default


EVERYONE, RAIDER = Role(G, default=True), Role(2)


class Member:
    def __init__(self, uid, roles=(), **p):
        self.id, self.roles, self.bot = uid, list(roles), False
        self.mention, self.display_name = f"<@{uid}>", f"Player{uid}"
        self.guild_permissions = perms(**p)


MEMBERS = {}


class Guild:
    def __init__(self):
        self.id, self.default_role, self.roles = G, EVERYONE, [EVERYONE, RAIDER]
        self.me = Member(999)

    def get_member(self, uid): return MEMBERS.get(uid)
    def get_role(self, rid): return next((r for r in self.roles if r.id == rid), None)

    @property
    def members(self): return list(MEMBERS.values())


GUILD = Guild()


class Chan:
    """A text channel. `hidden` is a set of role and member ids that can't see it."""
    def __init__(self, cid, hidden=(), kind=discord.ChannelType.text, members=None):
        self.id, self.name, self.mention, self.guild, self.type = cid, f"chan{cid}", f"<#{cid}>", GUILD, kind
        self.hidden, self.sent, self.owner_id, self.category_id, self._members = set(hidden), [], None, None, members

    def permissions_for(self, who):
        ids = {who.id} | {r.id for r in getattr(who, "roles", [])}
        return perms(view_channel=not (ids & self.hidden))

    @property
    def members(self):
        if self._members is not None:
            return self._members
        return [m for m in MEMBERS.values() if self.permissions_for(m).view_channel]

    async def send(self, *a, **k):
        self.sent.append((a, k))
        return types.SimpleNamespace(id=700 + len(self.sent), jump_url="http://x")

    def get_partial_message(self, mid):
        async def edit(**k): pass
        return types.SimpleNamespace(id=mid, edit=edit)


CH = {}
B.bot.get_channel = lambda cid: CH.get(cid)


class Resp:
    def __init__(self): self.sent = []
    async def send_message(self, *a, **k): self.sent.append((a, k))
    async def defer(self, **k): self.sent.append((("deferred",), k))
    def is_done(self): return bool(self.sent)


def inter(user, channel=None, **p):
    return types.SimpleNamespace(user=user, guild_id=G, guild=GUILD, channel=channel, channel_id=getattr(channel, "id", 0),
                                 response=Resp(), permissions=perms(**p), command=None,
                                 followup=types.SimpleNamespace(send=Resp().send_message))


def said(i):
    return " ".join(str(a[0]) for a, _ in i.response.sent if a)


def raid(title, channel, minutes=60, repeat=0):
    eid = db.create_event(G, channel, title, "", NOW + minutes * 60, 1, 0, 0, 0, repeat)
    db.set_message_id(eid, 5000 + eid)
    if repeat:
        db.update_event(eid, series_id=eid)
    return eid


# ------------------------------------------------------------------ reminders on closed raids
print("reminders for a raid closed for sign-ups")
e = raid("Closed early", 11, minutes=10)
db.set_closed(e, True)
check("a closed raid is still due a reminder", any(r["id"] == e for r in db.pending_reminders(NOW)))
check("but it isn't nudged (nobody can answer any more)", all(r["id"] != e for r in db.pending_nudges(NOW)))

# ------------------------------------------------------------------ who can see what
print("channel visibility helpers")
officer, alice, bob, trial = Member(1, [RAIDER], manage_events=True, administrator=True), Member(10, [RAIDER]), Member(11, [RAIDER]), Member(12)
MEMBERS.update({m.id: m for m in (officer, alice, bob, trial)})
CH[11] = Chan(11)                       # public raid channel
CH[12] = Chan(12, hidden={G, 12})       # team-only: @everyone and the trial member can't see it
CH[13] = Chan(13, hidden={11})          # public, but one member is denied by a member overwrite
CH[14] = Chan(14, kind=discord.ChannelType.private_thread, members=[types.SimpleNamespace(id=10)])
CH[31] = Chan(31)                       # a public board channel
check("a member who can see the channel", B.member_can_view(CH[11], 10))
check("someone who left the server can't", not B.member_can_view(CH[11], 4242))
check("a member denied by an overwrite can't", not B.member_can_view(CH[13], 11))
check("a private thread: its members can", B.member_can_view(CH[14], 10))
check("a private thread: others can't", not B.member_can_view(CH[14], 11))
check("user_sees agrees for a private thread", B.user_sees(CH[14], alice) and not B.user_sees(CH[14], bob))

print("the board / alert / events privacy rule")
check("a public raid shows on a public board", B.board_can_show(CH[31], 11))
check("a team-only raid doesn't", not B.board_can_show(CH[31], 12))
check("a raid one board reader is denied by a member overwrite doesn't", not B.board_can_show(CH[31], 13))
check("a raid in a private thread never shows", not B.board_can_show(CH[31], 14))
check("a public raid gets a server event", B.sched_can_show(GUILD, 11))
check("a member-overwrite raid gets no server event", not B.sched_can_show(GUILD, 13))
check("a private-thread raid gets no server event", not B.sched_can_show(GUILD, 14))

# ------------------------------------------------------------------ /always
print("/always only offers and accepts raids you can see")
pub, team = raid("Weekly public", 11, 600, repeat=7), raid("Weekly team", 12, 600, repeat=7)
names = [c.name for c in run(B.recurring_autocomplete(inter(trial), ""))]
check("the list shows the public raid", any("Weekly public" in n for n in names), names)
check("the list hides the team-only raid", not any("Weekly team" in n for n in names), names)
i = inter(trial)
run(B.always_join.callback(i, team))
check("joining a raid you can't see is refused", "isn't a repeating raid" in said(i) and not db.series_members(team), said(i))

print("a series member who lost access isn't signed up to the next raid")
db.join_series(G, team, 12, "Placeholdra", "Mage", "Frost", "ranged")
db.join_series(G, team, 10, "Placeholdrix", "Priest", "Holy", "healer")
nxt = raid("Weekly team (next)", 12, 600 + 7 * 1440)


async def no_refresh(*a, **k): return True
B.refresh_message = no_refresh
run(B.seed_series(nxt, team))
check("the member who can see it is signed up", db.signup(nxt, 10) is not None)
check("the member who can't is skipped", db.signup(nxt, 12) is None)

# ------------------------------------------------------------------ scoped officers
print("channel-scoped raid leaders and /stats")
past_pub = db.create_event(G, 11, "Old public", "", NOW - 3600, 1, 0, 0, 0)
past_team = db.create_event(G, 12, "Old team", "", NOW - 3600, 1, 0, 0, 0)
db.set_signup(past_pub, 10, "Placeholdrix", "Priest", "Holy", "healer", "in", None)
db.set_signup(past_team, 11, "Placeholdrin", "Rogue", "Subtlety", "melee", "in", None)
LEAD = Role(77)
db.set_setting(G, "officer_role@11", 77)
lead = Member(20, [LEAD])
i = inter(lead, CH[11])
scope = B.stats_scope(i, NOW - 86400, NOW)
check("a scoped leader's stats cover only their channel", scope == [11], scope)
rows = db.stats(G, NOW - 86400, NOW, channel_ids=scope)
check("and nobody from the other team's raids appears", {r["user_id"] for r in rows} == {10}, [r["user_id"] for r in rows])
check("a full officer still sees everything", B.stats_scope(inter(officer), NOW - 86400, NOW) is None)
check("no channels at all gives no rows, not everything", db.stats(G, NOW - 86400, NOW, channel_ids=[]) == [])

print("@everyone guards")
i = inter(officer)
run(B.config_officer.callback(i, EVERYONE))
check("/config officer refuses @everyone", "can't be the officer role" in said(i) and db.setting(G, "officer_role") is None, said(i))
i = inter(officer, CH[11])
run(B.config_channel.callback(i, None, officer=EVERYONE))
check("/config channel officer: refuses @everyone", "can't be a channel's officer role" in said(i), said(i))
check("someone without Mention Everyone may not borrow the bot's", not B.may_mention_everyone(inter(lead)))
check("someone with it may", B.may_mention_everyone(inter(lead, mention_everyone=True)))
check("an administrator may", B.may_mention_everyone(inter(officer)))

print("/raid add checks the player can see the channel")
e = raid("Team raid", 12, 120)
i = inter(officer, CH[12])


async def get_event(interaction, eid): return db.event(eid)
B._get_event = get_event
run(B.raid_add.callback(i, e, trial))
check("adding someone who can't see the channel is refused", "can't see the raid's channel" in said(i) and db.signup(e, 12) is None, said(i))

# ------------------------------------------------------------------ database
print("per-server backups when the bot serves several servers")
d2 = dbmod.DB(str(tmp / "multi.db"))
a = d2.create_event(1, 11, "Here", "", NOW + 3600, 1, 0, 0, 0)
b = d2.create_event(2, 21, "Elsewhere", "", NOW + 3600, 1, 0, 0, 0)
d2.conn.executemany("INSERT INTO characters(user_id, name, wow_class, spec, role, is_main) VALUES(?,?,?,?,?,?)",
                    [(10, "Usedhere", "Mage", "Frost", "ranged", 0), (10, "Onlyelsewhere", "Rogue", "Subtlety", "melee", 0),
                     (10, "Mainchar", "Priest", "Holy", "healer", 1)])
d2.conn.commit()
d2.set_signup(a, 10, "Usedhere", "Mage", "Frost", "ranged", "in", None)
d2.set_signup(b, 10, "Onlyelsewhere", "Rogue", "Subtlety", "melee", "in", None)
d2.snapshot_for_guild(str(tmp / "snap.db"), 1)
con = sqlite3.connect(str(tmp / "snap.db"))
names = {r[0] for r in con.execute("SELECT name FROM characters")}
con.close()
check("the character used in this server is kept", "Usedhere" in names, names)
check("the player's main is kept", "Mainchar" in names, names)
check("an alt only used in another server is left out", "Onlyelsewhere" not in names, names)

print("/forgetme overwrites deleted rows")
check("secure_delete is on", d2.conn.execute("PRAGMA secure_delete").fetchone()[0] == 1)

print("an install folder with % in its name")
odd = tmp / "pct%41x"
odd.mkdir()
d3 = dbmod.DB(str(odd / "r.db"))
d3.conn.close()
check("a healthy database there passes the check", dbmod.file_is_healthy(odd / "r.db"))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
