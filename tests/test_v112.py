"""Offline tests for v1.12 ("polish"): the setup panel, the new-version notice, Raider.IO import, and the Windows
shortcuts. No network is used: the two outside lookups are replaced by fakes.

Run:  python -I tests/test_v112.py        (or python tests/run_all.py for every suite)
"""
import asyncio
import builtins
import io
import os
import subprocess
import sys
import tempfile
import time
import types
import urllib.error
from pathlib import Path

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
import setup as S  # noqa: E402

run = asyncio.run
passed = failed = 0
WINDOWS = os.name == "nt"


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


def member(uid, admin=False):
    perms = types.SimpleNamespace(manage_events=False, administrator=admin, manage_guild=admin)
    return types.SimpleNamespace(guild_permissions=perms, roles=[], id=uid, bot=False, mention=f"<@{uid}>", display_name=f"User{uid}")


class FakeChannel:
    def __init__(self, cid):
        self.id, self.mention, self.name, self.sent, self.fail_send = cid, f"<#{cid}>", f"chan{cid}", [], None
        self.guild = types.SimpleNamespace(id=G, me=object(), get_role=lambda r: None, get_member=lambda u: None)

    async def send(self, *a, **k):
        if self.fail_send:
            raise self.fail_send
        self.sent.append((a, k))
        return types.SimpleNamespace(id=len(self.sent) + 700, jump_url="http://x")

    def permissions_for(self, me):
        return types.SimpleNamespace(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)


CH = {11: FakeChannel(11), 90: FakeChannel(90)}
B.bot.get_channel = lambda cid: CH.get(cid)


def inter(user, guild_id=G):
    ch = CH[11]
    return types.SimpleNamespace(user=user, guild_id=guild_id, guild=ch.guild, channel=ch, channel_id=11,
                                 response=FakeResp(), followup=FakeFollowup(), command=None, message=None)


def reply(i):
    msgs = i.response.sent or i.followup.sent
    return msgs[0][0][0] if msgs and msgs[0][0] else ""



def same(a, b) -> bool:
    """True if two paths are the same file or folder, whichever way Windows spells them (False if either is missing)."""
    try:
        return Path(a).samefile(b)
    except OSError:
        return False


admin = member(1, admin=True)
http_error = discord.HTTPException(types.SimpleNamespace(status=500, reason="x"), "server error")

# ------------------------------------------------------------ versions
print("version comparison")
check("'v1.12.0' is read as numbers", B.parse_version("v1.12.0") == (1, 12, 0, 0) and B.parse_version("1.12") == (1, 12, 0, 0))
check("beta tags, junk and nothing are not versions", all(B.parse_version(x) is None for x in ("v1.12.0-beta", "latest", "", None, "v1..2", "1.2.3.4.5")))
check("1.10 is newer than 1.9 (numbers, not text)", B.parse_version("1.10.0") > B.parse_version("1.9.0"))
check("this version is 1.12.0", B.VERSION == "1.12.0")
for tag, want in (("v1.12.1", True), ("v1.12.0", False), ("v1.11.9", False), ("v1.13.0", True), ("v2.0", True),
                  ("1.12.0-rc1", False), ("nightly", False), (None, False), ("v1.12", False)):
    check(f"{tag!r} newer than {B.VERSION}: {want}", B.release_is_newer(tag) is want)

# ------------------------------------------------------------ the outside-lookup helper
print("http_get_json fails safely")
real_urlopen = urllib.request.urlopen


class Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n=-1): return self.body


def attempt(fn, url="https://example.org/x"):
    urllib.request.urlopen = fn
    try:
        return "ok", B.http_get_json(url)
    except LookupError:
        return "lookup", None
    except ConnectionError as e:
        return "connection", str(e)
    finally:
        urllib.request.urlopen = real_urlopen


def raises(exc):
    def f(request, timeout=None):
        raise exc
    return f


check("a good answer is parsed", attempt(lambda r, timeout=None: Resp(b'{"a": 1}')) == ("ok", {"a": 1}))
check("HTTP 404 means 'not found'", attempt(raises(urllib.error.HTTPError("u", 404, "nf", {}, None)))[0] == "lookup")
check("HTTP 400 means 'not found' (Raider.IO's way of saying it)", attempt(raises(urllib.error.HTTPError("u", 400, "bad", {}, None)))[0] == "lookup")
check("HTTP 500 is a connection problem", attempt(raises(urllib.error.HTTPError("u", 500, "err", {}, None)))[0] == "connection")
check("no internet is a connection problem", attempt(raises(urllib.error.URLError("no route")))[0] == "connection")
check("a timeout is a connection problem", attempt(raises(TimeoutError("slow")))[0] == "connection")
check("text that isn't JSON is a connection problem", attempt(lambda r, timeout=None: Resp(b"<html>oops</html>"))[0] == "connection")
check("an address that isn't https is refused without any request", attempt(raises(AssertionError("must not be called")), "http://example.org")[0] == "connection")
sent_headers = {}


def spy(request, timeout=None):
    sent_headers.update(request.headers)
    sent_headers["timeout"] = timeout
    return Resp(b"{}")


attempt(spy)
check("it identifies itself and sets a timeout, and sends nothing else about you",
      "SuperDuperRaidBot" in sent_headers.get("User-agent", "") and sent_headers["timeout"] == 8 and set(sent_headers) <= {"User-agent", "Accept", "timeout"}, sent_headers)

# ------------------------------------------------------------ Raider.IO
print("Raider.IO lookup")
url = B.raiderio_url("eu", "Area 52", "Thr'all & co")
check("the address is built safely", url.startswith("https://raider.io/api/v1/characters/profile?") and "realm=Area+52" in url
      and "region=eu" in url and "&co" not in url.split("name=")[1].split("&")[0] and "fields=gear" in url, url)
SAMPLE = {"name": "Thrall", "class": "Shaman", "active_spec_name": "Enhancement", "gear": {"item_level_equipped": 639.5},
          "profile_url": "https://raider.io/characters/us/area-52/Thrall"}
real_get = B.http_get_json


def fake_get(payload):
    def f(u, timeout=8):
        if isinstance(payload, Exception):
            raise payload
        return payload
    return f


def lookup(payload):
    B.http_get_json = fake_get(payload)
    try:
        return run(B.raiderio_lookup("us", "Area 52", "Thrall"))
    except (LookupError, ConnectionError) as e:
        return type(e).__name__
    finally:
        B.http_get_json = real_get


got = lookup(SAMPLE)
check("class, spec, item level and link come back", got == {"name": "Thrall", "class": "Shaman", "spec": "Enhancement", "ilvl": 639,
                                                            "url": "https://raider.io/characters/us/area-52/Thrall"}, got)
check("a spec we don't know gives spec None (we ask the player)", lookup({**SAMPLE, "active_spec_name": "Banana"})["spec"] is None)
check("a link to some other site is dropped", lookup({**SAMPLE, "profile_url": "https://evil.example/x"})["url"] is None)
check("a text item level is ignored, not trusted", lookup({**SAMPLE, "gear": {"item_level_equipped": "9999"}})["ilvl"] is None)
check("an answer with no class is a connection problem", lookup({"name": "x"}) == "ConnectionError")
check("an answer that isn't an object is a connection problem", lookup(["nope"]) == "ConnectionError")
check("'not found' passes through", lookup(LookupError("not found")) == "LookupError")
check("a connection error passes through", lookup(ConnectionError("down")) == "ConnectionError")

print("/character import")
calls = []


def fake_lookup(result):
    async def f(region, realm, name):
        calls.append((region, realm, name))
        if isinstance(result, Exception):
            raise result
        return result
    return f


real_lookup = B.raiderio_lookup
user = member(500)


def do_import(*a, **k):
    i = inter(user, k.pop("guild_id", G))
    run(B.character_import.callback(i, *a, **k))
    return i, reply(i)


i, text = do_import("Thrall", "Area 52")
check("off by default: it says so and saves nothing", "switched off" in text and db.character_by_name(500, "Thrall") is None, text)
db.set_setting(G, "raiderio", "1")
B.raiderio_lookup = fake_lookup({"name": "Thrall", "class": "Shaman", "spec": "Enhancement", "ilvl": 639, "url": "https://raider.io/characters/us/area-52/Thrall"})
i, text = do_import("Thrall")
check("no realm anywhere: it asks for one and doesn't look anything up", "realm" in text and calls == [], text)
db.set_setting(G, "raiderio_realm", "Area 52")
db.set_setting(G, "raiderio_region", "eu")
i, text = do_import("Thrall", main=True)
c = db.character_by_name(500, "Thrall")
check("with the server's default realm and region it saves the character", c and c["wow_class"] == "Shaman" and c["spec"] == "Enhancement"
      and c["role"] == "dps" and c["is_main"] == 1, text)
check("it used the default region and realm", calls[-1] == ("eu", "Area 52", "Thrall"), calls)
check("the reply shows spec, class, item level, the link, and what was sent out",
      "Enhancement Shaman" in text and "item level 639" in text and "<https://raider.io/characters/us/area-52/Thrall>" in text and "raider.io" in text and "name and realm" in text, text)
check("the reply is private and the command acknowledged first", i.response.deferred and i.followup.sent[0][1].get("ephemeral") is True)
i, text = do_import("Jaina", "Silvermoon", region=types.SimpleNamespace(value="us", name="US"))
check("a typed realm and region win over the defaults", calls[-1] == ("us", "Silvermoon", "Jaina"), calls[-1])

B.raiderio_lookup = fake_lookup(LookupError("nope"))
i, text = do_import("Nobody", "Area 52")
check("no such character: a clear message, nothing saved", "no character called" in text and db.character_by_name(500, "Nobody") is None, text)
B.raiderio_lookup = fake_lookup(ConnectionError("down"))
i, text = do_import("Thrall2", "Area 52")
check("Raider.IO down: a friendly message and the alternative", "couldn't reach Raider.IO" in text and "/character add" in text, text)
B.raiderio_lookup = fake_lookup({"name": "Specless", "class": "Mage", "spec": None, "ilvl": None, "url": None})
i, text = do_import("Specless", "Area 52")
check("no spec reported: refuses and says how to continue", "which spec" in text and db.character_by_name(500, "Specless") is None, text)
B.raiderio_lookup = fake_lookup({"name": "Extra", "class": "Mage", "spec": "Fire", "ilvl": None, "url": None})
for n in range(B.MAX_CHARACTERS):
    db.upsert_character(500, f"Filler{n}", "Mage", "Fire", "dps", False)
i, text = do_import("Extra", "Area 52")
check("at the 25-character limit it refuses with the usual explanation", "25 characters" in text and db.character_by_name(500, "Extra") is None, text)
B.raiderio_lookup = real_lookup
long = do_import("N" * 40, "Area 52")[1]
check("an absurdly long name is refused without a lookup", "too long" in long)
check("in a private message (no server) it says to use a server", "your server" in do_import("Thrall", "Area 52", guild_id=None)[1])
B.wd.PRESET_NAME = "classic_era"
B.raiderio_lookup = fake_lookup(SAMPLE)
check("on the Classic Era class list it refuses (Raider.IO is retail)", "only covers retail" in do_import("Thrall", "Area 52")[1])
B.wd.PRESET_NAME = "retail"
B.raiderio_lookup = real_lookup

print("/config raiderio")
i = inter(admin)
run(B.config_raiderio.callback(i, False))
check("off", db.setting(G, "raiderio") == "0" and "off" in reply(i))
i = inter(admin)
run(B.config_raiderio.callback(i, True, types.SimpleNamespace(value="us"), "Area 52"))
check("on, with a region and realm, and it tells the admin what is sent", db.setting(G, "raiderio") == "1" and db.setting(G, "raiderio_region") == "us"
      and db.setting(G, "raiderio_realm") == "Area 52" and "raider.io" in reply(i) and "Area 52" in reply(i), reply(i))
B.wd.PRESET_NAME = "classic_era"
db.set_setting(G, "raiderio", "0")
i = inter(admin)
run(B.config_raiderio.callback(i, True))
check("it can't be turned on for a Classic Era server", db.setting(G, "raiderio") == "0" and "only covers retail" in reply(i))
B.wd.PRESET_NAME = "retail"

# ------------------------------------------------------------ update notice
print("new-version notice")
TAG, URL = "v9.9.9", "https://github.com/zedhand/SuperDuperRaidBot/releases/tag/v9.9.9"
real_latest = B.latest_release


def fake_latest(result):
    async def f():
        fake_latest.calls += 1
        return result
    return f


fake_latest.calls = 0


def reset_update():
    B._UPDATE.update(next_check=0.0, tag=None, url=None)
    B._tries.clear()
    CH[90].sent.clear()
    CH[90].fail_send = None
    fake_latest.calls = 0


def good_release(tag_name, html_url):
    return {"tag_name": tag_name, "html_url": html_url}


def get_latest(payload):
    B.http_get_json = fake_get(payload)
    try:
        return run(B.latest_release())
    finally:
        B.http_get_json = real_get


check("latest_release reads the tag and address", get_latest(good_release(TAG, URL)) == (TAG, URL))
check("a junk tag is ignored", get_latest(good_release("nightly", URL)) is None)
check("a beta tag is ignored", get_latest(good_release("v2.0.0-beta", URL)) is None)
check("an address that isn't on github.com is ignored", get_latest(good_release(TAG, "https://evil.example/x")) is None)
check("an answer with missing fields is ignored", get_latest({"tag_name": TAG}) is None and get_latest(["x"]) is None)
check("a network failure gives None, not an error", get_latest(ConnectionError("down")) is None and get_latest(LookupError("x")) is None)

reset_update()
db.set_setting(G, "log_channel", 90)
B.latest_release = fake_latest((TAG, URL))
now = int(time.time())
run(B._run_updatecheck(now))
check("with the notice off nothing is asked and nothing is posted", fake_latest.calls == 0 and CH[90].sent == [])
db.set_setting(G, "update_notice", "1")
run(B._run_updatecheck(now))
text = CH[90].sent[0][0][0] if CH[90].sent else ""
check("on: one line in the log channel with the version, the update instruction and a link", TAG in text and "Update.bat" in text and f"<{URL}>" in text, text)
check("it doesn't ping anyone", CH[90].sent[0][1]["allowed_mentions"].roles is False and CH[90].sent[0][1]["allowed_mentions"].everyone is False)
check("the version is remembered so it is announced once", db.setting(G, "update_announced") == TAG)
run(B._run_updatecheck(now + 60))
check("the next tick doesn't ask GitHub again (about once a day)", fake_latest.calls == 1 and len(CH[90].sent) == 1)
B._UPDATE["next_check"] = 0
run(B._run_updatecheck(now + 86500))
check("a day later it asks again but doesn't repeat the same announcement", fake_latest.calls == 2 and len(CH[90].sent) == 1)
B.latest_release = fake_latest(("v9.9.10", URL))
B._UPDATE["next_check"] = 0
run(B._run_updatecheck(now + 2 * 86400))
check("a newer release is announced", len(CH[90].sent) == 2 and "v9.9.10" in CH[90].sent[1][0][0])

reset_update()
db.set_setting(G, "update_announced", None)
B.latest_release = fake_latest(("v1.0.0", URL))
run(B._run_updatecheck(now))
check("an older release is never announced", CH[90].sent == [])
B.latest_release = fake_latest((B.VERSION, URL))
B._UPDATE["next_check"] = 0
run(B._run_updatecheck(now))
check("neither is the same version", CH[90].sent == [])
B.latest_release = fake_latest(None)
B._UPDATE["next_check"] = 0
run(B._run_updatecheck(now))
check("a failed check posts nothing and is retried in an hour, not a day", CH[90].sent == [] and B._UPDATE["next_check"] == now + 3600, B._UPDATE["next_check"] - now)

reset_update()
B.latest_release = fake_latest((TAG, URL))
CH[90].fail_send = http_error
run(B._run_updatecheck(now))
check("if posting fails it isn't marked as announced (it will be retried)", db.setting(G, "update_announced") is None)
CH[90].fail_send = None
B._UPDATE["next_check"] = 0
run(B._run_updatecheck(now + 100))
check("and goes out once the channel works again", len(CH[90].sent) == 1 and db.setting(G, "update_announced") == TAG)
db.set_setting(G, "log_channel", None)
db.set_setting(G, "update_announced", None)
reset_update()
run(B._run_updatecheck(now))
check("with no log channel it quietly does nothing", CH[90].sent == [])
db.set_setting(G, "log_channel", 90)

i = inter(admin)
run(B.status.callback(i))
check("/status shows the latest release when the notice is on", "Latest release" in reply(i) and TAG in reply(i) and "update is available" in reply(i), reply(i))
db.set_setting(G, "update_notice", "0")
i = inter(admin)
run(B.status.callback(i))
check("and nothing when it is off", "Latest release" not in reply(i))
i = inter(admin)
run(B.config_updatenotice.callback(i, True))
check("/config updatenotice on explains what is contacted", db.setting(G, "update_notice") == "1" and "GitHub" in reply(i))
i = inter(admin)
run(B.config_updatenotice.callback(i, False))
check("/config updatenotice off", db.setting(G, "update_notice") == "0")
import inspect  # noqa: E402
check("the scheduler runs the check", "_run_updatecheck" in inspect.getsource(B.reminder_loop.coro))
B.latest_release = real_latest

# ------------------------------------------------------------ the setup panel
print("/config setup: structure")
i = inter(admin)
run(B.config_setup.callback(i))
sent = i.response.sent[0][1]
view = sent["view"]
check("it opens a private panel", isinstance(view, B.SetupView) and sent["ephemeral"] is True)
labels = [c.label for c in view.children]
check("the home page has the four sections and Done", labels == ["Roles", "Channels", "Timing", "Extras", "Done"], labels)
check("every page builds with valid rows and fits Discord's limits", all(
    (setattr(view, "page", p), view.build(), view.to_components())[2] is not None and len(view.children) <= 25
    for p in ("home", "roles", "channels", "timing", "extras")))
check("every page's text fits one message", all((setattr(view, "page", p), len(view.text()) <= 2000)[1] for p in ("home", "roles", "channels", "timing", "extras")))
view.page = "home"
view.build()


def press(v, label):
    ii = inter(admin)
    button = next(c for c in v.children if getattr(c, "label", None) == label)
    run(button.callback(ii))
    return ii


ii = press(view, "Roles")
check("Roles opens a page of three role pickers and Back", [type(c).__name__ for c in view.children] == ["RoleSelect"] * 3 + ["Button"]
      and "Officer" in ii.response.edited[0]["content"])
check("each picker allows clearing the choice", all(c.min_values == 0 and c.max_values == 1 for c in view.children[:3]))

print("/config setup: roles")
roles = [c for c in view.children if isinstance(c, discord.ui.RoleSelect)]
role = types.SimpleNamespace(id=700, is_default=lambda: False)
roles[0]._values = [role]
ii = inter(admin)
run(roles[0].callback(ii))
check("choosing an officer role saves it and redraws", db.setting(G, "officer_role") == "700" and "<@&700>" in ii.response.edited[0]["content"])
check("the picker then shows it as the current choice", view.children[0].default_values and view.children[0].default_values[0].id == 700)
roles = [c for c in view.children if isinstance(c, discord.ui.RoleSelect)]
roles[1]._values = [types.SimpleNamespace(id=701, is_default=lambda: False)]
run(roles[1].callback(inter(admin)))
check("the ping role saves under the same key as /config pingrole", db.setting(G, "ping_role") == "701")
roles[2]._values = [types.SimpleNamespace(id=702, is_default=lambda: False)]
run(roles[2].callback(inter(admin)))
check("the sign-up role saves", db.setting(G, "raider_role") == "702")
roles = [c for c in view.children if isinstance(c, discord.ui.RoleSelect)]
roles[2]._values = [types.SimpleNamespace(id=1, is_default=lambda: True)]
run(roles[2].callback(inter(admin)))
check("choosing @everyone clears the sign-up role (it means 'no restriction')", db.setting(G, "raider_role") is None)
roles = [c for c in view.children if isinstance(c, discord.ui.RoleSelect)]
roles[0]._values = []
run(roles[0].callback(inter(admin)))
check("choosing nothing clears the role", db.setting(G, "officer_role") is None)
press(view, "Back")
check("Back returns to the home page", [c.label for c in view.children][0] == "Roles" and view.page == "home")

print("/config setup: channels")
press(view, "Channels")
sel = [c for c in view.children if isinstance(c, discord.ui.ChannelSelect)]
check("a text-channel picker and a voice/stage picker", len(sel) == 2 and sel[0].channel_types == [discord.ChannelType.text]
      and set(sel[1].channel_types) == {discord.ChannelType.voice, discord.ChannelType.stage_voice})
sel[0]._values = [types.SimpleNamespace(id=90)]
ii = inter(admin)
run(sel[0].callback(ii))
check("the log channel saves", db.setting(G, "log_channel") == "90" and "<#90>" in ii.response.edited[0]["content"])
sel = [c for c in view.children if isinstance(c, discord.ui.ChannelSelect)]
sel[1]._values = [types.SimpleNamespace(id=55)]
run(sel[1].callback(inter(admin)))
check("the voice channel saves under the same key as /config voicechannel", db.setting(G, "voice_channel") == "55")
sel = [c for c in view.children if isinstance(c, discord.ui.ChannelSelect)]
sel[1]._values = []
run(sel[1].callback(inter(admin)))
check("and can be cleared", db.setting(G, "voice_channel") is None)
press(view, "Back")

print("/config setup: timing")
db.set_setting(G, "timezone", "Asia/Tokyo")
press(view, "Channels")
press(view, "Back")
press(view, "Timing")
selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
check("four menus and Back", len(selects) == 4 and len(view.children) == 5)
tz_opts = [o.value for o in selects[0].options]
check("a timezone that isn't in the short list is still offered, as the current one",
      tz_opts[0] == "Asia/Tokyo" and "America/Vancouver" in tz_opts and len(tz_opts) <= 25 and selects[0].options[0].default)
selects[0]._values = ["Europe/London"]
run(selects[0].callback(inter(admin)))
check("choosing a timezone saves it", db.setting(G, "timezone") == "Europe/London")
selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
selects[1]._values = ["45"]
run(selects[1].callback(inter(admin)))
check("the reminder time saves", db.setting(G, "reminder_minutes") == "45" and B.guild_reminder_minutes(G) == 45)
selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
selects[2]._values = ["6"]
run(selects[2].callback(inter(admin)))
selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
selects[3]._values = ["3"]
ii = inter(admin)
run(selects[3].callback(ii))
check("the nudge and auto-close save", db.setting(G, "nudge_hours") == "6" and db.setting(G, "autoclose_hours") == "3")
check("and the page shows them", "6 hours before" in ii.response.edited[0]["content"] and "3 hours before" in ii.response.edited[0]["content"], ii.response.edited[0]["content"])
selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
check("the current choices are marked in the menus", [o.value for o in selects[1].options if o.default] == ["45"]
      and [o.value for o in selects[2].options if o.default] == ["6"] and [o.value for o in selects[3].options if o.default] == ["3"])
press(view, "Back")

print("/config setup: extras")
press(view, "Extras")
toggles = [c.label for c in view.children]
check("six switches and Back on retail (Confirm deletes since v1.13)", len(toggles) == 7 and toggles[-1] == "Back" and "Raider.IO import: " in " ".join(toggles) and "Confirm deletes: off" in toggles, toggles)
before = db.setting(G, "threads")
press(view, "Discussion threads: off")
check("a switch flips the setting", db.setting(G, "threads") == "1")
check("and its label changes", "Discussion threads: on" in [c.label for c in view.children])
press(view, "Discussion threads: on")
check("clicking again turns it off", db.setting(G, "threads") == "0")
for label, key in (("Utility warnings", "buffs"), ("Weekly digest", "digest"), ("Update notice", "update_notice"), ("Raider.IO import", "raiderio")):
    db.set_setting(G, key, "0")
    press(view, f"{label}: off")
    check(f"{label} switches on under its own key", db.setting(G, key) == "1")
B.wd.PRESET_NAME = "classic_era"
view.build()
check("on Classic Era the Raider.IO switch isn't offered", not any("Raider.IO" in c.label for c in view.children))
B.wd.PRESET_NAME = "retail"
view.build()
ii = press(view, "Back")
ii = press(view, "Done")
check("Done closes the panel and says so", view.is_finished() and ii.response.edited[0]["view"] is None and "saved" in ii.response.edited[0]["content"].lower())

print("/config setup: only the admin who opened it")
view2 = B.SetupView(owner_id=1, guild_id=G)
stranger = inter(member(99))
check("someone else is turned away politely", run(view2.interaction_check(stranger)) is False and "belongs to" in reply(stranger))
check("the owner is allowed", run(view2.interaction_check(inter(admin))) is True)
check("the group itself is admin-only like the rest of /config", isinstance(B.config, B.AdminGroup))

# ------------------------------------------------------------ shortcuts
print("shortcuts")
base = tmp / "bot"
base.mkdir()
(base / "start.bat").write_text("@echo off\n")
desk, startup = tmp / "Desktop", tmp / "Startup"
for d in (desk, startup):
    d.mkdir()
os.environ["RAIDBOT_DESKTOP_DIR"], os.environ["RAIDBOT_STARTUP_DIR"] = str(desk), str(startup)
check("the folders can be pointed elsewhere for tests", checks.special_folder("Desktop") == desk and checks.special_folder("Startup") == startup)
if WINDOWS:
    msg = checks.toggle_shortcut("Desktop", base)
    link = desk / checks.SHORTCUT_NAME
    check("switching it on creates a real Windows shortcut", msg == "Added the desktop shortcut." and link.exists(), msg)
    target, workdir = checks.read_shortcut(link)
    # Windows may hand one path back in its old short form (RUNNER~1) and another in the long form: compare the files, not the text
    check("pointing at start.bat, starting in the bot's folder", same(target, base / "start.bat") and same(workdir, base), (target, workdir))
    check("switching it again removes it", checks.toggle_shortcut("Desktop", base) == "Removed the desktop shortcut." and not link.exists())
    check("the startup shortcut works the same way", checks.toggle_shortcut("Startup", base).startswith("Added") and (startup / checks.SHORTCUT_NAME).exists())
    checks.toggle_shortcut("Startup", base)

    print("shortcuts: the end of the setup wizard")
    S.BASE = base
    asked = []

    def script(answers):
        it = iter(answers)

        def fake_ask(prompt):
            asked.append(prompt)
            return next(it)
        S.ask = fake_ask

    asked.clear()
    script(["", ""])
    S.offer_shortcuts()
    check("pressing Enter twice changes nothing (the answer is no by default)", not (desk / checks.SHORTCUT_NAME).exists()
          and not (startup / checks.SHORTCUT_NAME).exists() and len(asked) == 2)
    script(["y", "n"])
    S.offer_shortcuts()
    check("'y' to the first question adds only the desktop shortcut", (desk / checks.SHORTCUT_NAME).exists() and not (startup / checks.SHORTCUT_NAME).exists())
    checks.toggle_shortcut("Desktop", base)
    script(["N", "YES"])
    S.offer_shortcuts()
    check("'YES' to the second adds only the startup shortcut (case doesn't matter)", (startup / checks.SHORTCUT_NAME).exists() and not (desk / checks.SHORTCUT_NAME).exists())
    checks.toggle_shortcut("Startup", base)
    (base / "start.bat").rename(base / "start.off")
    asked.clear()
    script([])
    S.offer_shortcuts()
    check("without start.bat it asks nothing", asked == [])
    (base / "start.off").rename(base / "start.bat")

    print("shortcuts: Shortcuts.bat menu")
    feed = iter(["1", "2", "1", ""])
    real_input = builtins.input
    builtins.input = lambda prompt="": next(feed)
    out = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = out
    try:
        checks.shortcuts_menu(base)
    finally:
        sys.stdout = real_stdout
        builtins.input = real_input
    text = out.getvalue()
    check("the menu shows the state and switches things", "ON" in text and "Added the desktop shortcut." in text and "Added starting with Windows." in text
          and "Removed the desktop shortcut." in text)
    check("it ended with the desktop one off and the startup one on", not (desk / checks.SHORTCUT_NAME).exists() and (startup / checks.SHORTCUT_NAME).exists())
    checks.toggle_shortcut("Startup", base)
    proc = subprocess.run([sys.executable, "-I", str(proj / "checks.py"), "shortcuts"], input="\n", capture_output=True, text=True,
                          cwd=proj, env={**os.environ})
    check("`python checks.py shortcuts` (what Shortcuts.bat runs) starts and exits cleanly", proc.returncode == 0 and "RAID BOT SHORTCUTS" in proc.stdout, proc.stdout + proc.stderr)
else:
    print("  (Windows-only checks skipped on this system)")
    del os.environ["RAIDBOT_DESKTOP_DIR"], os.environ["RAIDBOT_STARTUP_DIR"]
    check("elsewhere it says it's Windows only", checks.toggle_shortcut("Desktop", base) == "Shortcuts are only available on Windows.")
    check("and the wizard doesn't ask", (S.offer_shortcuts(), True)[1])
bat = (proj / "Shortcuts.bat").read_text(encoding="utf-8")
check("Shortcuts.bat runs the menu with the bot's own Python", "checks.py shortcuts" in bat and ".venv\\Scripts\\python.exe" in bat)
check("and the updater delivers it", '"Shortcuts.bat"' in (proj / "update.ps1").read_text(encoding="utf-8"))

# ------------------------------------------------------------ housekeeping
print("housekeeping")
help_i = inter(admin)
run(B.help_command.callback(help_i))
fields = help_i.response.sent[0][1]["embed"].fields
help_text = " ".join(f.value for f in fields)
check("/help mentions the new commands", all(w in help_text for w in ("/character import", "/config setup", "/config raiderio", "/config updatenotice")))
check("and every help section still fits Discord's 1024-character limit", all(len(f.value) <= 1024 for f in fields), [len(f.value) for f in fields])
check("the bot's address and User-Agent carry this version", f"{B.VERSION})" in checks.USER_AGENT)
check("no new program file was added (older updaters would skip it)",
      {x.name for x in proj.glob("*.py")} == {"bot.py", "checks.py", "db.py", "embeds.py", "setup.py", "wow_data.py"})

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
