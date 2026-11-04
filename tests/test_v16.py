"""Offline tests for v1.6 (channel scoping). Run: python -I tests/test_v16.py"""
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

B.db = dbmod.DB(str(tmp / "t.db"))
db = B.db
passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


G = 1
# ---- fake channel tree: category 10 (Raid 1) has 11 signup + 12 chat; category 20 (Raid 2) has 21 signup
def text(cid, name, cat):
    ch = MagicMock(spec=discord.TextChannel)
    ch.id, ch.name, ch.category_id, ch.mention = cid, name, cat, f"<#{cid}>"
    return ch


CH = {11: text(11, "raid-1-signup", 10), 12: text(12, "raid-1-chat", 10),
      21: text(21, "raid-2-signup", 20), 31: text(31, "20-man-signups", 30)}
thread = MagicMock(spec=discord.Thread); thread.id = 99; thread.parent = CH[11]
CH[99] = thread
B.bot.get_channel = lambda cid: CH.get(cid)

print("scope helpers")
check("text channel -> [channel, category]", B._scope_ids(CH[11]) == [11, 10])
check("int id works", B._scope_ids(21) == [21, 20])
check("thread uses parent", B._scope_ids(thread) == [11, 10])
check("unknown channel -> []", B._scope_ids(12345) == [])
db.set_setting(G, "officer_role@10", 500)   # Raid 1 category leaders
db.set_setting(G, "officer_role@21", 600)   # Raid 2 signup channel leaders
db.set_setting(G, "ping_role@10", 700)
db.set_setting(G, "ping_role@11", 701)      # channel beats category
check("scoped role ids, channel first", B.scoped_role_ids(G, CH[11], "ping_role") == [701, 700])
check("category setting reaches sibling channel", B.scoped_role_ids(G, CH[12], "officer_role") == [500])
check("no setting -> empty", B.scoped_role_ids(G, CH[31], "officer_role") == [])
check("channel ping beats category", B.default_ping_role_id(G, CH[11]) == 701)
check("category ping used for sibling", B.default_ping_role_id(G, CH[12]) == 700)
check("no ping anywhere -> None", B.default_ping_role_id(G, CH[31]) is None)
db.set_setting(G, "ping_role", 800)
check("server ping is the fallback", B.default_ping_role_id(G, CH[31]) == 800 and B.default_ping_role_id(G, CH[21]) == 800)


def member(roles=(), manage_events=False, admin=False):
    perms = types.SimpleNamespace(manage_events=manage_events, administrator=admin)
    return types.SimpleNamespace(guild_permissions=perms, roles=[types.SimpleNamespace(id=r) for r in roles], id=77, mention="<@77>")


class FakeResp:
    def __init__(self): self.sent = []; self.deferred = False
    async def send_message(self, *a, **k): self.sent.append((a, k))
    async def defer(self, **k): self.deferred = True
    def is_done(self): return bool(self.sent) or self.deferred


def inter(user, channel=None):
    ch = channel or CH[11]
    posted = []
    ch.send = MagicMock(side_effect=lambda **kw: _post(kw, posted))
    followup = types.SimpleNamespace(sent=[])
    async def fsend(*a, **k): followup.sent.append((a, k))
    followup.send = fsend
    guild = types.SimpleNamespace(id=G, me=object(), get_role=lambda rid: types.SimpleNamespace(
        id=rid, mention=f"<@&{rid}>", name=f"role{rid}", mentionable=True, is_default=lambda: False))
    return types.SimpleNamespace(user=user, guild_id=G, guild=guild, channel=ch, channel_id=ch.id,
                                 response=FakeResp(), followup=followup, posted=posted, command=None)


async def _post(kw, posted):
    posted.append(kw)
    return types.SimpleNamespace(id=4242, jump_url="http://x")


print("who can manage where")
pleb, admin, mgr = member(), member(admin=True), member(manage_events=True)
r1, r2, global_off = member([500]), member([600]), member([900])
db.set_setting(G, "officer_role", 900)
check("pleb: nowhere", not B.can_manage_channel(inter(pleb), CH[11]))
check("admin / Manage Events: everywhere", B.can_manage_channel(inter(admin), CH[31]) and B.can_manage_channel(inter(mgr), CH[21]))
check("server officer role: everywhere", B.can_manage_channel(inter(global_off), CH[31]))
check("Raid 1 leader (category): signup channel", B.can_manage_channel(inter(r1), CH[11]))
check("Raid 1 leader (category): sibling chat channel", B.can_manage_channel(inter(r1), CH[12]))
check("Raid 1 leader: NOT Raid 2", not B.can_manage_channel(inter(r1), CH[21]))
check("Raid 1 leader: NOT 20-man", not B.can_manage_channel(inter(r1), CH[31]))
check("Raid 2 leader (channel): own channel", B.can_manage_channel(inter(r2), CH[21]))
check("Raid 2 leader: NOT Raid 1", not B.can_manage_channel(inter(r2), CH[11]))
check("works from a thread under the channel", B.can_manage_channel(inter(r1), thread))
check("holds_scoped_officer_role true for leaders", B.holds_scoped_officer_role(inter(r1)) and B.holds_scoped_officer_role(inter(r2)))
check("holds_scoped_officer_role false for pleb", not B.holds_scoped_officer_role(inter(pleb)))

print("/raid group gate")
grp = B.raid
for who, expect, label in ((pleb, False, "pleb blocked"), (r1, True, "channel leader allowed in"),
                           (global_off, True, "server officer allowed in"), (mgr, True, "Manage Events allowed in")):
    i = inter(who)
    ok = asyncio.run(grp.interaction_check(i))
    check(label, ok is expect)
    if not expect:
        check("  and told why", len(i.response.sent) == 1 and "officers" in i.response.sent[0][0][0])

print("_get_event")
FUT = int(time.time()) + 86400
e1 = db.create_event(G, 11, "Naxx", "", FUT, 1, 0, 0, 0)       # Raid 1
e2 = db.create_event(G, 21, "Naxx", "", FUT + 60, 1, 0, 0, 0)  # Raid 2, same title!
e3 = db.create_event(G, 31, "Onyxia", "", FUT + 120, 1, 0, 0, 0)
eX = db.create_event(999, 11, "Other guild", "", FUT, 1, 0, 0, 0)
run = asyncio.run
check("leader gets own event", run(B._get_event(inter(r1), e1))["id"] == e1)
i = inter(r1)
try:
    run(B._get_event(i, e2)); denied = False
except app_commands.CheckFailure:
    denied = True
check("leader denied other group's event", denied)
check("  and sent an explanation", i.response.sent and "channels you're an officer for" in i.response.sent[0][0][0])
check("missing event -> None", run(B._get_event(inter(admin), 424242)) is None)
check("other server's event -> None", run(B._get_event(inter(admin), eX)) is None)
check("admin gets any event", run(B._get_event(inter(admin), e3))["id"] == e3)

print("autocomplete")
ch = lambda who, cur="", chan=None: run(B.event_autocomplete(inter(who, chan), cur))
names = lambda cs: [c.name for c in cs]
res = ch(r1)
check("leader only sees own raids", [c.value for c in res] == [e1], names(res))
check("label has channel, title, date, id", res and res[0].name.startswith("#raid-1-signup · Naxx · ") and res[0].name.endswith(f"(#{e1})"), names(res))
res = ch(admin)
check("admin sees all, ordered by time", [c.value for c in res] == [e1, e2, e3], names(res))
res = ch(admin, chan=CH[31])
check("raids from the current channel come first", res[0].value == e3, names(res))
check("same-title raids are told apart by channel", len({n for n in names(ch(admin)) if "Naxx" in n}) == 2)
check("search matches channel name", [c.value for c in ch(admin, "20-man")] == [e3])
check("search matches title", [c.value for c in ch(admin, "onyx")] == [e3])
check("search matches id", [c.value for c in ch(admin, f"#{e2})")] == [e2])
check("pleb sees nothing", ch(pleb) == [])
for i2 in range(30):
    db.create_event(G, 31, "x" * 90, "", FUT + 1000 + i2, 1, 0, 0, 0)
res = ch(admin)
check("max 25 choices, each <= 100 chars", len(res) == 25 and all(len(c.name) <= 100 for c in res))

print("/raid create")
cb = B.raid_create.callback
i = inter(pleb)
run(cb(i, "2099-01-01", "20:00", "T"))
check("pleb can't create (and nothing posted)", i.posted == [] and "officer" in i.response.sent[0][0][0])
i = inter(r1, CH[12])  # a category leader, in sibling channel
run(cb(i, "2099-01-01", "20:00", "Raid 1 night"))
check("leader can create in own category", len(i.posted) == 1)
check("category default ping role used (701 is channel 11's, 700 category's)", i.posted[0]["content"] == "<@&700>", i.posted[0].get("content"))
i = inter(r1, CH[11])
run(cb(i, "2099-01-01", "20:00", "Raid 1 night"))
check("channel ping beats category ping", i.posted[0]["content"] == "<@&701>")
i = inter(r1, CH[21])
run(cb(i, "2099-01-01", "20:00", "Sneaky"))
check("leader can NOT create in another group's channel", i.posted == [] and i.response.sent)
i = inter(admin, CH[31])
run(cb(i, "2099-01-01", "20:00", "20 man"))
check("no channel ping -> server ping fallback", i.posted[0]["content"] == "<@&800>")
i = inter(admin, CH[31])
run(cb(i, "2099-01-01", "20:00", "20 man", ping=types.SimpleNamespace(id=5, mention="<@&5>", name="r", mentionable=True, is_default=lambda: False)))
check("explicit ping wins", i.posted[0]["content"] == "<@&5>")

print("/raid edit / cancel respect scope")
i = inter(r1)
try:
    run(B.raid_edit.callback(i, e2, title="Hijacked"))
except app_commands.CheckFailure:
    pass
check("leader can't edit other group's raid", db.event(e2)["title"] == "Naxx")
i = inter(r1)
run(B.raid_edit.callback(i, e1, title="Renamed by leader"))
check("leader can edit own raid", db.event(e1)["title"] == "Renamed by leader")
i = inter(r2)
try:
    run(B.raid_cancel.callback(i, e1))
except app_commands.CheckFailure:
    pass
check("other leader can't cancel it", db.event(e1) is not None)

print("/config channel")
cc = B.config_channel.callback
i = inter(admin, CH[31])
run(cc(i))
check("no options: shows current", "Ping role: none" in i.response.sent[0][0][0])
role = lambda rid: types.SimpleNamespace(id=rid, mention=f"<@&{rid}>")
i = inter(admin, CH[31])
run(cc(i, ping=role(55), officer=role(66)))
check("sets both for the current channel", db.setting(G, "ping_role@31") == "55" and db.setting(G, "officer_role@31") == "66")
i = inter(admin, CH[31])
run(cc(i, target=CH[21], ping=role(56)))
check("explicit target channel", db.setting(G, "ping_role@21") == "56")
i = inter(admin, CH[11])
run(cc(i, target=MagicMock(spec=discord.CategoryChannel, id=20, mention="<#20>"), officer=role(67)))
check("category target works", db.setting(G, "officer_role@20") == "67")
i = inter(admin, thread)
run(cc(i, ping=role(57)))
check("from a thread -> applies to parent channel", db.setting(G, "ping_role@11") == "57")
i = inter(admin, CH[31])
run(cc(i, clear=True))
check("clear removes both", db.setting(G, "ping_role@31") is None and db.setting(G, "officer_role@31") is None)
i = inter(admin, CH[31]); i.channel = MagicMock(spec=discord.VoiceChannel)
run(cc(i, ping=role(1)))
check("non-text channel rejected", "text channel" in i.response.sent[0][0][0])
i = inter(admin)
run(B.config_show.callback(i))
txt = i.response.sent[0][0][0]
check("/config show lists channel settings", "Channel / category settings" in txt and "<#21>" in txt and "<#20>" in txt, txt)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
