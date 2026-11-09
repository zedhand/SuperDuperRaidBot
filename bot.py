"""Single-guild raid signup bot (a small Raid-Helper alternative)."""
import asyncio
import csv
import io
import json
import logging
import logging.handlers
import os
import platform
import re
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date as Date
from datetime import datetime
from datetime import time as Clock
from datetime import timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

VERSION = "1.12.0"
WINDOWS = os.name == "nt"  # only used to word messages for the person reading them
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"  # always the .env next to bot.py, whatever folder you launch from
load_dotenv(ENV_PATH)

import checks  # noqa: E402
import wow_data as wd  # noqa: E402  # noqa: E402  (after load_dotenv so WOW_VERSION is read)
from db import KEEP_CANCELLED_DAYS, NOTE_MAX, caps_dict, caps_text, clean_note, clean_realm, open_database  # noqa: E402
from embeds import build_embed, text_problem  # noqa: E402

def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"{name} in .env must be a number (got '{raw}').")


def _env_tz() -> ZoneInfo:
    name = (os.getenv("TIMEZONE") or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        raise SystemExit(f"TIMEZONE in .env is not valid: '{name}'. Use a name like America/New_York or Europe/London.")


TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
GUILD_ID = _env_int("GUILD_ID", 0)
TZ = _env_tz()
REMINDER_MINUTES = _env_int("REMINDER_MINUTES", 30)

log = logging.getLogger("raidbot")
db = open_database(os.getenv("DB_PATH", "raidbot.db"))
LAST_ERROR: tuple[float, str] | None = None  # (time, short text) of the last problem, shown by /status


def note_error(text: str) -> None:
    global LAST_ERROR
    LAST_ERROR = (time.time(), text[:200])

# Only ping the people we mean to. discord.py's AllowedMentions(users=True) would still let an
# @everyone or role mention typed into a raid title go through, so everything else is switched off.
USERS_ONLY = discord.AllowedMentions(everyone=False, roles=False, users=True)

intents = discord.Intents.default()
intents.members = True  # needed for /raid unsigned; enable "Server Members Intent" in the dev portal
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)  # slash-only; no message-content intent needed


# ---------------------------------------------------------------- helpers
def is_locked(event) -> bool:
    return bool(event["closed"]) or time.time() >= event["start_ts"]


def plural(n: int, word: str) -> str:
    """'1 day', '2 days'."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def guild_tz(guild_id: int) -> ZoneInfo:
    """The server's /config timezone, falling back to TIMEZONE from .env."""
    name = db.setting(guild_id, "timezone")
    try:
        return ZoneInfo(name) if name else TZ
    except Exception:
        return TZ


def int_setting(guild_id: int, key: str, default: int = 0) -> int:
    """A numeric server setting; a missing or damaged value gives the default instead of an error."""
    try:
        return int(db.setting(guild_id, key, default))
    except (TypeError, ValueError):
        return default


def guild_reminder_minutes(guild_id: int) -> int:
    return int_setting(guild_id, "reminder_minutes", REMINDER_MINUTES)


def is_officer(interaction: discord.Interaction) -> bool:
    """Full officer: Manage Events / Administrator, or the server-wide /config officer role.
    Full officers can manage raids in every channel."""
    member = interaction.user
    perms = getattr(member, "guild_permissions", None)
    if perms and (perms.manage_events or perms.administrator):
        return True
    role_id = db.setting(interaction.guild_id, "officer_role")
    return bool(role_id) and any(r.id == int(role_id) for r in getattr(member, "roles", []))


def _scope_ids(channel) -> list[int]:
    """The channel's own id, then its category's. Threads count as their parent channel."""
    if isinstance(channel, int):
        channel = bot.get_channel(channel)
    if channel is None:
        return []
    channel = getattr(channel, "parent", None) or channel
    ids = [channel.id]
    if getattr(channel, "category_id", None):
        ids.append(channel.category_id)
    return ids


def scoped_role_ids(guild_id: int, channel, key: str) -> list[int]:
    """Role ids set with /config channel for this channel (first) and its category (second)."""
    out = []
    for cid in _scope_ids(channel):
        value = db.setting(guild_id, f"{key}@{cid}")
        if value:
            out.append(int(value))
    return out


def can_manage_channel(interaction: discord.Interaction, channel) -> bool:
    """Full officers anywhere; channel officers only in their own channel / category."""
    if is_officer(interaction):
        return True
    mine = {r.id for r in getattr(interaction.user, "roles", [])}
    return any(r in mine for r in scoped_role_ids(interaction.guild_id, channel, "officer_role"))


def holds_scoped_officer_role(interaction: discord.Interaction) -> bool:
    mine = {r.id for r in getattr(interaction.user, "roles", [])}
    return any(int(v) in mine for v in db.settings_with_prefix(interaction.guild_id, "officer_role@").values())


def default_ping_role_id(guild_id: int, channel) -> int | None:
    """Channel's ping role, else its category's, else the server-wide one."""
    scoped = scoped_role_ids(guild_id, channel, "ping_role")
    if scoped:
        return scoped[0]
    server = db.setting(guild_id, "ping_role")
    return int(server) if server else None


def may_sign_up(interaction: discord.Interaction) -> bool:
    """True unless /config raiderrole is set and this member neither has that role nor is an officer."""
    role_id = int_setting(interaction.guild_id, "raider_role")
    if not role_id:
        return True
    if is_officer(interaction) or holds_scoped_officer_role(interaction):
        return True
    return any(r.id == role_id for r in getattr(interaction.user, "roles", []))


def notes_mode(guild_id: int) -> str:
    """/config notes: 'off' (the default), 'officers' (notes only in /raid roster) or 'post' (also on the raid post)."""
    if db.setting(guild_id, "signup_notes") != "1":
        return "off"
    return "post" if db.setting(guild_id, "notes_on_post") == "1" else "officers"


def spec_switch_on(guild_id: int) -> bool:
    """/config specswitch: show a Spec button on raid posts (off by default)."""
    return db.setting(guild_id, "spec_switch") == "1"


def render_embed(event, signups, locked: bool):
    """The raid post, with the optional missing-utility warnings if /config buffs is on, realms after the
    character names if /config realms is on, and players' notes if /config notes shows them on the post."""
    g = event["guild_id"]
    realms = db.realms_for(signups) if db.setting(g, "show_realms") == "1" else None
    notes = ({s["user_id"]: s["note"] for s in signups if s["note"]} if notes_mode(g) == "post" else None)
    return build_embed(event, signups, locked, buffs=db.setting(g, "buffs") == "1", realms=realms, notes=notes)


def autoclose_inside(guild_id: int, start_ts: int) -> bool:
    """True if a raid starting at start_ts is already inside the /config autoclose window."""
    hours = int_setting(guild_id, "autoclose_hours")
    return bool(hours and start_ts - hours * 3600 <= time.time())


def queue_note(event_id: int, user_id: int) -> str:
    """' You're #2 in line for a tank slot.' for someone the bot benched because a role or class was full, else ''."""
    waiting = db.bench_wait(event_id, user_id)
    if not waiting:
        return ""
    pos, key = waiting
    word = wd.ROLE_SINGULAR[key].lower() if key in wd.ROLE_SINGULAR else key
    return f" You're #{pos} in line for a {word} slot."


def apply_cap(event, user_id: int, role: str, status: str, wow_class: str | None = None) -> tuple[str, str | None]:
    """Counted statuses ('in', 'late') overflow to the bench when their role is full, or when the raid limits
    their class (/raid create classes:) and that class is full.

    Returns (status, bench_from). bench_from remembers what they asked for so the bot can
    promote them if a slot opens up."""
    if status in ("in", "late"):
        cap = event[f"cap_{role}"]
        if cap and db.count_role(event["id"], role, user_id) >= cap:
            return "bench", status
        caps = caps_dict(event["class_caps"])
        if wow_class and wow_class in caps and db.count_class(event["id"], wow_class, user_id) >= caps[wow_class]:
            return "bench", status
    return status, None


def bench_reason(event, user_id: int, role: str, wow_class: str | None) -> str:
    """Why someone was benched, for the reply: 'Tank slots are full' or 'only 2 Mages are allowed'."""
    cap = event[f"cap_{role}"]
    if cap and db.count_role(event["id"], role, user_id) >= cap:
        return f"{wd.ROLE_SINGULAR[role]} slots are full"
    caps = caps_dict(event["class_caps"])
    if wow_class in caps:
        return f"only {caps[wow_class]} {wow_class} {'is' if caps[wow_class] == 1 else 'are'} allowed"
    return f"{wd.ROLE_SINGULAR[role]} slots are full"


def parse_class_caps(text: str | None) -> tuple[str, str | None]:
    """'Mage 4, Warlock 2' (or 'Mage:4') -> ('Mage:4,Warlock:2', None); 'off' -> ('', None); bad input -> ('', why)."""
    t = (text or "").strip()
    if t.lower() in ("", "off", "none", "no", "clear", "0"):
        return "", None
    caps: dict[str, int] = {}
    for part in re.split(r"[,;]", t):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"([A-Za-z][A-Za-z ]*?)\s*[: ]\s*(\d{1,2})", part) or re.fullmatch(r"(\d{1,2})\s+([A-Za-z][A-Za-z ]*)", part)
        if not m:
            return "", f"I couldn't read `{part}`. Write class limits like `Mage 4, Warlock 2` (or `off`)."
        first, second = m.groups()
        name, n = (first, second) if first[0].isalpha() else (second, first)
        cls = wd.find_class(name)
        if not cls:
            return "", f"I don't know the class `{name.strip()}`. Options: {', '.join(wd.class_names())}"
        if int(n) > 40:
            return "", "A class limit can be at most 40."
        if int(n) > 0:
            caps[cls] = int(n)
    return caps_text(caps), None


async def promote_bench(event_id: int) -> None:
    """Move people the bot benched (role was full) back in, first come first served, as slots open."""
    event = db.event(event_id)
    if not event or is_locked(event):
        return
    for s in db.signups(event_id):
        if s["status"] != "bench" or not s["bench_from"] or not s["role"]:
            continue
        cap = event[f"cap_{s['role']}"]
        if cap and db.count_role(event_id, s["role"], s["user_id"]) >= cap:
            continue
        class_cap = caps_dict(event["class_caps"]).get(s["wow_class"])
        if class_cap and db.count_class(event_id, s["wow_class"], s["user_id"]) >= class_cap:
            continue
        db.set_signup(event_id, s["user_id"], s["char_name"], s["wow_class"], s["spec"], s["role"], s["bench_from"])
        text = f"🎉 A {wd.ROLE_SINGULAR[s['role']].lower()} slot opened up. You're off the bench for **{event['title']}**!"
        await tell_player(event, s["user_id"], text)


async def tell_player(event, user_id: int, text: str) -> None:
    """A private message to a player; if their DMs are closed, a mention in the raid channel instead. Never raises."""
    try:
        await (await bot.fetch_user(user_id)).send(text)
    except discord.HTTPException:  # DMs closed: mention them in the raid channel instead
        channel = bot.get_channel(event["channel_id"])
        if channel:
            try:
                await channel.send(f"<@{user_id}> {text}", allowed_mentions=USERS_ONLY)
            except discord.HTTPException:
                pass


STARTED_AT = time.time()


async def audit(guild_id: int, text: str) -> None:
    """One line in the log channel (/config logchannel), if one is set. Never raises."""
    channel_id = db.setting(guild_id, "log_channel")
    channel = bot.get_channel(int(channel_id)) if channel_id else None
    if channel is None:
        return
    try:
        await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


async def redraw_open_posts(guild_id: int) -> None:
    """Redraw a server's upcoming raid posts, after a setting that changes how they look."""
    for upcoming in db.upcoming_events(guild_id, int(time.time()))[:25]:
        if upcoming["message_id"]:
            await refresh_message(upcoming["id"])


async def update_event(event_id: int) -> None:
    """After any roster change: promote benched players if there is room, then redraw the post."""
    await promote_bench(event_id)
    await refresh_message(event_id)


_refreshing: dict[int, bool] = {}  # event id -> "changed again while an edit was in flight"


async def refresh_message(event_id: int) -> bool:
    """Redraw the raid post. Returns False only when a redraw failed and is worth retrying.

    Clicks that arrive while an edit is already in flight don't queue one edit each (Discord limits
    message edits): they flag the post as stale and the edit in flight redraws once more from the
    latest data, so a rush of signups costs a couple of edits, not one per click."""
    if event_id in _refreshing:
        _refreshing[event_id] = True
        return True
    ok = True
    try:
        while True:
            _refreshing[event_id] = False
            ok = await _redraw(event_id)
            if not _refreshing[event_id]:
                return ok
    finally:
        _refreshing.pop(event_id, None)


async def _redraw(event_id: int) -> bool:
    event = db.event(event_id)
    if not event or not event["message_id"]:
        return True
    channel = bot.get_channel(event["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(event["channel_id"])
        except discord.NotFound:
            return True  # the channel is gone for good: nothing to redraw
        except discord.HTTPException:
            return False
    locked = is_locked(event)
    embed = render_embed(event, db.signups(event_id), locked)
    try:
        await channel.get_partial_message(event["message_id"]).edit(
            embed=embed, view=ClosedView() if locked else RaidView(spec_switch_on(event["guild_id"])))
    except discord.NotFound:
        return True  # the post was deleted by hand
    except discord.HTTPException as e:
        log.warning("Could not edit event %s message: %s", event_id, e)
        note_error(f"could not edit the post for raid #{event_id}: {e}")
        return False
    schedule_board(event["guild_id"])  # signups, title or time changed: the board's counts follow
    return True


def char_dict(row) -> dict:
    return {"name": row["char_name"], "cls": row["wow_class"], "spec": row["spec"], "role": row["role"]}


# ---------------------------------------------------------------- raid message UI
class CharacterSelect(discord.ui.Select):
    def __init__(self, event_id: int, chars):
        self.event_id = event_id
        # No option is preselected: choosing an already-selected option sends nothing to the bot, which
        # would leave a player with one saved character unable to sign up. The main is starred instead.
        opts = [discord.SelectOption(label=(("⭐ " if c["is_main"] else "") + c["name"])[:100],
                                     description=f"{c['spec']} {c['wow_class']}{' · ' + realm_of(c) if realm_of(c) else ''}"[:100],
                                     value=str(c["id"]))
                for c in chars[:25]]
        super().__init__(placeholder="Pick a character…", options=opts)

    async def callback(self, interaction: discord.Interaction):
        c = db.character(int(self.values[0]), interaction.user.id)
        if not c:
            return await interaction.response.edit_message(content="Character not found.", view=None)
        await finish_signup(interaction, self.event_id, c["name"], c["wow_class"], c["spec"], c["role"])


async def finish_signup(interaction: discord.Interaction, event_id: int, name: str, cls: str,
                        spec: str, role: str):
    event = db.event(event_id)
    if not event or is_locked(event):
        return await interaction.response.edit_message(content="Signups are closed.", view=None)
    status, bench_from = apply_cap(event, interaction.user.id, role, "in", cls)
    db.set_signup(event_id, interaction.user.id, name, cls, spec, role, status, bench_from)
    note = "" if status == "in" else f" ({bench_reason(event, interaction.user.id, role, cls)}, so you were put on the bench)"
    await interaction.response.edit_message(
        content=f"Signed up as **{name}** — {spec} {cls} ({wd.ROLE_LABEL[role]}){note}.{queue_note(event_id, interaction.user.id)}",
        view=None)
    await update_event(event_id)


class ClassSelect(discord.ui.Select):
    def __init__(self, event_id: int):
        self.event_id = event_id
        super().__init__(placeholder="Or choose a class…",
                         options=[discord.SelectOption(label=c, emoji=wd.class_emoji(c))
                                  for c in wd.class_names()])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content=f"**{self.values[0]}** — now pick your spec:",
            view=SpecPickView(self.event_id, self.values[0]))


class SpecSelect(discord.ui.Select):
    def __init__(self, event_id: int, cls: str):
        self.event_id, self.cls = event_id, cls
        opts = [discord.SelectOption(label=s, description=wd.GROUP_LABEL[g], emoji=wd.GROUP_ICON[g])
                for s, g in wd.DATA[cls].items()]
        super().__init__(placeholder="Pick your spec…", options=opts)

    async def callback(self, interaction: discord.Interaction):
        spec = self.values[0]
        await finish_signup(interaction, self.event_id, interaction.user.display_name[:32], self.cls,
                            spec, wd.role_for(self.cls, spec))


class SpecPickView(discord.ui.View):
    def __init__(self, event_id: int, cls: str):
        super().__init__(timeout=120)
        self.add_item(SpecSelect(event_id, cls))


class QuickSignupButton(discord.ui.Button):
    """One click: sign up as the character this person used last time (or their main)."""

    def __init__(self, event_id: int, c: dict):
        self.event_id, self.c = event_id, c
        super().__init__(label=f"Sign up as {c['name']} ({c['spec']} {c['wow_class']})"[:80],
                         style=discord.ButtonStyle.success, emoji="⚡")

    async def callback(self, interaction: discord.Interaction):
        c = self.c
        await finish_signup(interaction, self.event_id, c["name"], c["wow_class"], c["spec"], c["role"])


def quick_character(user_id: int) -> dict | None:
    """Who a one-click sign-up should use: the character they signed up with last, else their main."""
    last = db.last_character_used(user_id)
    if last:
        return {"name": last["char_name"], "wow_class": last["wow_class"], "spec": last["spec"], "role": last["role"]}
    main = db.main_character(user_id)
    if main:
        return {"name": main["name"], "wow_class": main["wow_class"], "spec": main["spec"], "role": main["role"]}
    return None


class CharacterPickView(discord.ui.View):
    """A one-click button for the usual character, saved characters (if any), and a class picker."""

    def __init__(self, event_id: int, chars, quick: dict | None = None):
        super().__init__(timeout=120)
        if quick:
            self.add_item(QuickSignupButton(event_id, quick))
        if chars:
            self.add_item(CharacterSelect(event_id, chars))
        self.add_item(ClassSelect(event_id))


class SpecSwitchSelect(discord.ui.Select):
    """The Spec button's picker: the other specs of the class someone signed up with."""

    def __init__(self, event_id: int, cls: str, specs: list[str]):
        self.event_id, self.cls = event_id, cls
        super().__init__(placeholder="Pick your new spec…", options=[
            discord.SelectOption(label=s, description=wd.GROUP_LABEL[wd.DATA[cls][s]], emoji=wd.GROUP_ICON[wd.DATA[cls][s]])
            for s in specs[:25]])

    async def callback(self, interaction: discord.Interaction):
        await change_spec(interaction, self.event_id, self.cls, self.values[0])


async def change_spec(interaction: discord.Interaction, event_id: int, cls: str, spec: str):
    """Change the spec (and so maybe the role) of someone's signup, keeping their character, status and note.
    Someone who is in or late, or waiting on the bench for a full slot, is checked against the new role's limit
    again: they may move in, or onto the bench. A bench or tentative choice of their own stays as it was."""
    event = db.event(event_id)
    if not event or is_locked(event):
        return await interaction.response.edit_message(content="Signups are closed.", view=None)
    uid = interaction.user.id
    current = db.signup(event_id, uid)
    if not current or current["wow_class"] != cls or current["status"] == "absent":
        return await interaction.response.edit_message(
            content="Your signup changed in the meantime. Press **Spec** again.", view=None)
    role = wd.role_for(cls, spec)
    wanted = current["bench_from"] or current["status"]  # an auto-benched player still wants their slot
    if wanted in ("in", "late"):
        status, bench_from = apply_cap(event, uid, role, wanted, cls)
    else:
        status, bench_from = current["status"], None
    db.set_signup(event_id, uid, current["char_name"], cls, spec, role, status, bench_from)
    if status == "bench" and bench_from:
        extra = f" {bench_reason(event, uid, role, cls)}, so you're on the bench.{queue_note(event_id, uid)}"
    elif current["status"] == "bench" and current["bench_from"]:
        extra = f" There was room, so you're **{status}** now."
    else:
        extra = ""
    await interaction.response.edit_message(
        content=f"Switched to **{spec}** {cls} ({wd.ROLE_LABEL[role]}).{extra}", view=None)
    await update_event(event_id)


class SpecSwitchView(discord.ui.View):
    def __init__(self, event_id: int, cls: str, specs: list[str]):
        super().__init__(timeout=120)
        self.add_item(SpecSwitchSelect(event_id, cls, specs))


class ClosedView(discord.ui.View):
    """Replaces the buttons with a greyed-out bar once an event is closed."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Select(placeholder="This event is closed.", disabled=True,
                                        options=[discord.SelectOption(label="Closed", value="closed")]))


class RaidView(discord.ui.View):
    """Persistent buttons under every raid post. Event is found via the message id.

    spec_button: also show the Spec button (/config specswitch). The view registered at startup has it, so clicks on
    any post are answered; the posts themselves only get it when the server turned the switch on."""

    def __init__(self, spec_button: bool = False):
        super().__init__(timeout=None)
        if not spec_button:
            self.remove_item(self.switch_spec)

    async def _event(self, interaction: discord.Interaction):
        event = db.event_by_message(interaction.message.id)
        if not event:
            await interaction.response.send_message("This event no longer exists.", ephemeral=True)
            return None
        if is_locked(event):
            await interaction.response.send_message("Signups are closed for this event.", ephemeral=True)
            return None
        if not may_sign_up(interaction):
            await interaction.response.send_message(
                f"Sign-ups here are for members with <@&{int_setting(interaction.guild_id, 'raider_role')}>. "
                "Ask an officer if you should have it.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return None
        return event

    async def _status(self, interaction: discord.Interaction, status: str):
        event = await self._event(interaction)
        if not event:
            return
        uid = interaction.user.id
        if status == "absent":
            db.set_signup(event["id"], uid, None, None, None, None, "absent")
        else:
            existing = db.signup(event["id"], uid)
            row = existing if existing and existing["char_name"] else None
            if row is None:
                main = db.main_character(uid)
                if main:
                    row = {"char_name": main["name"], "wow_class": main["wow_class"],
                           "spec": main["spec"], "role": main["role"]}
            if row is None:
                return await interaction.response.send_message(
                    "Press **Sign Up** first to choose your class and spec (or save a character "
                    "with `/character add`), then you can use this button.", ephemeral=True)
            status, bench_from = apply_cap(event, uid, row["role"], status, row["wow_class"])
            db.set_signup(event["id"], uid, row["char_name"], row["wow_class"], row["spec"],
                          row["role"], status, bench_from)
        await interaction.response.send_message(
            f"You are now marked as **{status}**.{queue_note(event['id'], uid)}", ephemeral=True)
        await update_event(event["id"])

    @discord.ui.button(label="Sign Up", style=discord.ButtonStyle.success, custom_id="raid:signup")
    async def signup(self, interaction: discord.Interaction, _: discord.ui.Button):
        event = await self._event(interaction)
        if not event:
            return
        chars = db.characters(interaction.user.id)
        quick = quick_character(interaction.user.id)
        prompt = ("Pick a saved character, or choose a class to sign up without one:" if chars else
                  "Choose your class to sign up. (Tip: save characters with `/character add` to skip this next time.)")
        if quick:
            prompt = "One click with the button below, or pick someone else:"
        current = db.signup(event["id"], interaction.user.id)
        if current:
            who = f" as **{current['char_name']}**" if current["char_name"] else ""
            prompt = f"You're currently **{current['status']}**{who}. " + prompt
        await interaction.response.send_message(prompt, view=CharacterPickView(event["id"], chars, quick),
                                                ephemeral=True)

    @discord.ui.button(label="Late", style=discord.ButtonStyle.primary, custom_id="raid:late")
    async def late(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._status(interaction, "late")

    @discord.ui.button(label="Tentative", style=discord.ButtonStyle.secondary, custom_id="raid:tentative")
    async def tentative(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._status(interaction, "tentative")

    @discord.ui.button(label="Bench", style=discord.ButtonStyle.secondary, custom_id="raid:bench")
    async def bench(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._status(interaction, "bench")

    @discord.ui.button(label="Absent", style=discord.ButtonStyle.danger, custom_id="raid:absent")
    async def absent(self, interaction: discord.Interaction, _: discord.ui.Button):
        await self._status(interaction, "absent")

    @discord.ui.button(label="Spec", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="raid:spec")
    async def switch_spec(self, interaction: discord.Interaction, _: discord.ui.Button):
        event = await self._event(interaction)
        if not event:
            return
        if not spec_switch_on(interaction.guild_id):
            return await interaction.response.send_message(
                "Switching spec from the post is turned off. Press **Sign Up** to sign up again with another spec.",
                ephemeral=True)
        current = db.signup(event["id"], interaction.user.id)
        if not current or not current["wow_class"] or current["status"] == "absent":
            return await interaction.response.send_message(
                "You aren't signed up with a character for this raid. Press **Sign Up** first.", ephemeral=True)
        cls = current["wow_class"]
        others = [s for s in wd.DATA.get(cls, {}) if s != current["spec"]]
        if not others:
            return await interaction.response.send_message(
                f"There's no other {cls} spec to switch to. Press **Sign Up** to pick another character.", ephemeral=True)
        await interaction.response.send_message(
            f"You're signed up as **{current['char_name']}**, {current['spec']} {cls}. Switch to:",
            view=SpecSwitchView(event["id"], cls, others), ephemeral=True)

    @discord.ui.button(label="Calendar", emoji="📅", style=discord.ButtonStyle.secondary, custom_id="raid:ics")
    async def calendar(self, interaction: discord.Interaction, _: discord.ui.Button):
        event = db.event_by_message(interaction.message.id)
        if not event:
            return await interaction.response.send_message("This event no longer exists.", ephemeral=True)
        data = build_ics(event, interaction.message.jump_url).encode("utf-8")
        await interaction.response.send_message(
            "Open this file to add the raid to your calendar (Google, Apple, Outlook). It is in your own timezone.",
            file=discord.File(io.BytesIO(data), filename=f"raid-{event['id']}.ics"), ephemeral=True)


# ---------------------------------------------------------------- add to calendar (.ics)
RAID_HOURS = 3  # raids have no end time; calendars show this long


def _ics_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _ics_fold(line: str) -> str:
    """Calendar files may not have lines over 75 bytes: continue on the next line after a space."""
    parts, cur, limit = [], "", 75
    for ch in line:
        if len((cur + ch).encode("utf-8")) > limit:
            parts.append(cur)
            cur, limit = ch, 74
        else:
            cur += ch
    parts.append(cur)
    return "\r\n ".join(parts)


def build_ics(event, url: str, now: int | None = None) -> str:
    """An iCalendar file for one raid. The UID is stable, so re-opening it after the raid moves updates the
    calendar entry instead of adding a second one."""
    stamp = "%Y%m%dT%H%M%SZ"
    start = datetime.fromtimestamp(event["start_ts"], timezone.utc)
    end = start + timedelta(hours=RAID_HOURS)
    made = datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc)
    description = (event["description"] + "\n" if event["description"] else "") + url
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SuperDuperRaidBot//Raid//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:raid-{event['id']}@superduperraidbot",
             f"DTSTAMP:{made.strftime(stamp)}", f"DTSTART:{start.strftime(stamp)}", f"DTEND:{end.strftime(stamp)}",
             f"SUMMARY:{_ics_escape(event['title'])}", f"DESCRIPTION:{_ics_escape(description)}", f"URL:{url}",
             "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_ics_fold(x) for x in lines) + "\r\n"


# ---------------------------------------------------------------- the two optional outside lookups
REPO = "zedhand/SuperDuperRaidBot"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RAIDERIO_API = "https://raider.io/api/v1/characters/profile"
RAIDERIO_REGIONS = ("us", "eu", "kr", "tw", "cn")


def http_get_json(url: str, timeout: float = 8):
    """GET a URL and parse the JSON. Raises LookupError if the site says 'not found', ConnectionError for anything
    else that goes wrong (no internet, a timeout, an error page, text that isn't JSON). Blocking: run it in a thread."""
    if not url.startswith("https://"):
        raise ConnectionError("only https addresses are used")
    request = urllib.request.Request(url, headers={"User-Agent": f"SuperDuperRaidBot/{VERSION} (+https://github.com/{REPO})",
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 (https only, checked above)
            return json.loads(response.read(2_000_000).decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code in (400, 404):
            raise LookupError("not found") from err
        raise ConnectionError(f"the site answered HTTP {err.code}") from err
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        raise ConnectionError(str(err)) from err


def raiderio_url(region: str, realm: str, name: str) -> str:
    return RAIDERIO_API + "?" + urlencode({"region": region, "realm": realm, "name": name, "fields": "gear"})


async def raiderio_lookup(region: str, realm: str, name: str) -> dict:
    """Class, spec and item level of a retail character, from Raider.IO. Raises LookupError / ConnectionError."""
    data = await asyncio.to_thread(http_get_json, raiderio_url(region, realm, name))
    if not isinstance(data, dict) or not data.get("class"):
        raise ConnectionError("Raider.IO's answer wasn't what I expected")
    cls = wd.find_class(data.get("class"))
    gear = data.get("gear") if isinstance(data.get("gear"), dict) else {}
    ilvl = gear.get("item_level_equipped")
    link = data.get("profile_url")
    return {"name": str(data.get("name") or name).strip()[:32], "class": cls,
            "spec": wd.find_spec(cls, data.get("active_spec_name")),
            "realm": clean_realm(data.get("realm") if isinstance(data.get("realm"), str) else ""),
            "ilvl": int(ilvl) if isinstance(ilvl, (int, float)) else None,
            "url": link if isinstance(link, str) and link.startswith("https://raider.io/") else None}


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """'v1.12.0' -> (1, 12, 0, 0). Anything that isn't plain numbers (a beta tag, junk) -> None."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})", (text or "").strip())
    if not m:
        return None
    parts = [int(x) for x in m.group(1).split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def release_is_newer(tag: str | None) -> bool:
    new, current = parse_version(tag), parse_version(VERSION)
    return bool(new and current and new > current)


# ---------------------------------------------------------------- /character
character = app_commands.Group(name="character", description="Manage your WoW characters")
MAX_CHARACTERS = 25  # a Discord select menu shows at most 25 options


async def class_autocomplete(_: discord.Interaction, current: str):
    return [app_commands.Choice(name=c, value=c) for c in wd.class_names()
            if current.lower() in c.lower()][:25]


async def spec_autocomplete(interaction: discord.Interaction, current: str):
    cls = getattr(interaction.namespace, "wowclass", None)
    return [app_commands.Choice(name=s, value=s) for s in wd.spec_names(cls)
            if current.lower() in s.lower()][:25]


def realm_of(c) -> str:
    """A saved character's realm ('' if none). Works for database rows and plain dicts."""
    return (c["realm"] if "realm" in c.keys() else "") or ""


def char_label(c) -> str:
    """'Thrall-Area 52 (Restoration Shaman)' for menus; no realm part when none is saved."""
    return f"{c['name']}{'-' + realm_of(c) if realm_of(c) else ''} ({c['spec']} {c['wow_class']})"[:100]


async def own_char_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=char_label(c), value=c["name"])
            for c in db.characters(interaction.user.id) if current.lower() in c["name"].lower()][:25]


@character.command(name="add", description="Add a character (or change the spec of an existing one)")
@app_commands.describe(name="Character name", wowclass="Class", spec="Spec", main="Make this your main",
                       realm="Realm (optional, e.g. Area 52). Leave it out to keep the one already saved")
@app_commands.autocomplete(wowclass=class_autocomplete, spec=spec_autocomplete)
async def character_add(interaction: discord.Interaction, name: str, wowclass: str, spec: str,
                        main: bool = False, realm: Optional[str] = None):
    cls = wd.find_class(wowclass)
    if not cls:
        return await interaction.response.send_message(
            f"Unknown class. Options: {', '.join(wd.class_names())}", ephemeral=True)
    sp = wd.find_spec(cls, spec)
    if not sp:
        return await interaction.response.send_message(
            f"Unknown spec for {cls}. Options: {', '.join(wd.spec_names(cls))}", ephemeral=True)
    name = name.strip()[:32]
    if not db.character_by_name(interaction.user.id, name) and len(db.characters(interaction.user.id)) >= MAX_CHARACTERS:
        return await interaction.response.send_message(
            f"You already have {MAX_CHARACTERS} characters, which is the most the sign-up menu can show. "
            "Remove one with `/character remove` first.", ephemeral=True)
    if realm is not None and realm.strip() and not clean_realm(realm):
        return await interaction.response.send_message(
            "That doesn't look like a realm name. Use letters, numbers, spaces, ' and -, e.g. `Area 52`.", ephemeral=True)
    db.upsert_character(interaction.user.id, name, cls, sp, wd.role_for(cls, sp), main, realm)
    saved = db.character_by_name(interaction.user.id, name)
    where = f" on {saved['realm']}" if saved["realm"] else ""
    await interaction.response.send_message(f"Saved **{saved['name']}**{where} — {sp} {cls}.", ephemeral=True)


@character.command(name="list", description="Show your characters")
async def character_list(interaction: discord.Interaction):
    chars = db.characters(interaction.user.id)
    if not chars:
        return await interaction.response.send_message("No characters yet. Use `/character add`.",
                                                       ephemeral=True)
    lines = [f"{'⭐ ' if c['is_main'] else ''}**{c['name']}**{' · ' + c['realm'] if c['realm'] else ''} — "
             f"{c['spec']} {c['wow_class']} ({c['role']})" for c in chars]
    if not any(c["realm"] for c in chars):
        lines.append("-# Add a realm with `/character realm` if you like (optional).")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@character.command(name="realm", description="Set or clear the realm of one of your characters")
@app_commands.describe(name="Your character", realm="Realm, e.g. Area 52. Leave it out to clear it")
@app_commands.autocomplete(name=own_char_autocomplete)
async def character_realm(interaction: discord.Interaction, name: str, realm: Optional[str] = None):
    c = db.character_by_name(interaction.user.id, name)
    if not c:
        return await interaction.response.send_message("Character not found.", ephemeral=True)
    if realm and realm.strip() and not clean_realm(realm):
        return await interaction.response.send_message(
            "That doesn't look like a realm name. Use letters, numbers, spaces, ' and -, e.g. `Area 52`.", ephemeral=True)
    db.set_realm(interaction.user.id, c["id"], realm)
    new = clean_realm(realm)
    await interaction.response.send_message(
        f"**{c['name']}** is now on **{new}**." if new else f"Cleared the realm of **{c['name']}**.", ephemeral=True)


@character.command(name="main", description="Set your main character")
@app_commands.autocomplete(name=own_char_autocomplete)
async def character_main(interaction: discord.Interaction, name: str):
    c = db.character_by_name(interaction.user.id, name)
    if not c:
        return await interaction.response.send_message("Character not found.", ephemeral=True)
    db.set_main(interaction.user.id, c["id"])
    await interaction.response.send_message(f"**{c['name']}** is now your main.", ephemeral=True)


@character.command(name="remove", description="Delete one of your characters")
@app_commands.autocomplete(name=own_char_autocomplete)
async def character_remove(interaction: discord.Interaction, name: str):
    c = db.character_by_name(interaction.user.id, name)
    if not c:
        return await interaction.response.send_message("Character not found.", ephemeral=True)
    db.delete_character(interaction.user.id, c["id"])
    await interaction.response.send_message(f"Removed **{c['name']}**.", ephemeral=True)


@character.command(name="import", description="Look a retail character up on Raider.IO and save it (if your admin turned this on)")
@app_commands.describe(name="Character name", realm="Realm (default: the server's, see /config raiderio)",
                       region="us, eu, kr, tw or cn (default: the server's)", main="Make this your main")
@app_commands.choices(region=[app_commands.Choice(name=r.upper(), value=r) for r in RAIDERIO_REGIONS])
async def character_import(interaction: discord.Interaction, name: str, realm: Optional[str] = None,
                           region: Optional[app_commands.Choice[str]] = None, main: bool = False):
    g = interaction.guild_id
    if g is None:
        return await interaction.response.send_message("Use this in your server, not in a private message.", ephemeral=True)
    if db.setting(g, "raiderio") != "1":
        return await interaction.response.send_message(
            "Importing from Raider.IO is switched off on this server. Ask an admin (`/config raiderio`), or save the "
            "character yourself with `/character add`.", ephemeral=True)
    if wd.PRESET_NAME != "retail":
        return await interaction.response.send_message("Raider.IO only covers retail. Use `/character add`.", ephemeral=True)
    name = name.strip()
    realm = (realm or db.setting(g, "raiderio_realm") or "").strip()
    reg = region.value if region else (db.setting(g, "raiderio_region") or "us")
    if not name or not realm:
        return await interaction.response.send_message(
            "I need the character's name and its realm, e.g. `realm: Area 52`. (An admin can set a default realm "
            "with `/config raiderio`.)", ephemeral=True)
    if len(name) > 32 or len(realm) > 40:
        return await interaction.response.send_message("That name or realm is too long.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        found = await raiderio_lookup(reg, realm, name)
    except LookupError:
        return await interaction.followup.send(
            f"Raider.IO has no character called **{name}** on **{realm}** ({reg.upper()}). Check the spelling and the "
            "realm, or save it yourself with `/character add`.", ephemeral=True)
    except ConnectionError:
        return await interaction.followup.send(
            "I couldn't reach Raider.IO just now. Try again in a minute, or use `/character add`.", ephemeral=True)
    if not found["class"] or not found["spec"]:
        return await interaction.followup.send(
            f"Raider.IO found **{found['name']}** but didn't say which spec they play. Save them with "
            "`/character add` and pick the spec yourself.", ephemeral=True)
    if not db.character_by_name(interaction.user.id, found["name"]) and len(db.characters(interaction.user.id)) >= MAX_CHARACTERS:
        return await interaction.followup.send(
            f"You already have {MAX_CHARACTERS} characters, which is the most the sign-up menu can show. "
            "Remove one with `/character remove` first.", ephemeral=True)
    db.upsert_character(interaction.user.id, found["name"], found["class"], found["spec"],
                        wd.role_for(found["class"], found["spec"]), main, found.get("realm") or realm)
    extra = f", item level {found['ilvl']}" if found["ilvl"] else ""
    link = f" <{found['url']}>" if found["url"] else ""
    await interaction.followup.send(
        f"Saved **{found['name']}** on {found.get('realm') or clean_realm(realm)} — {found['spec']} {found['class']}{extra}.{link}\n"
        "(To look it up I sent the name and realm you typed to raider.io. Nothing else is shared.)", ephemeral=True)


# ---------------------------------------------------------------- /raid (officers)
class RaidGroup(app_commands.Group):
    """/raid commands are for officers: Manage Events, the /config officer role, or a channel's
    officer role (/config channel). Each command then checks the specific channel."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_officer(interaction) or holds_scoped_officer_role(interaction):
            return True
        await interaction.response.send_message(
            "Only raid officers can use `/raid` commands. Ask an admin to give you the officer role.",
            ephemeral=True)
        return False


raid = RaidGroup(name="raid", description="Create and manage raid events", guild_only=True)


async def event_autocomplete(interaction: discord.Interaction, current: str):
    """Raids this person can manage, as '#channel · Title · Nov 14 20:00 (#id)'. This channel first."""
    tz = guild_tz(interaction.guild_id)
    rows = db.upcoming_events(interaction.guild_id, int(time.time()) - 6 * 3600)
    rows = sorted(rows, key=lambda e: (e["channel_id"] != interaction.channel_id, e["start_ts"]))
    out = []
    for e in rows:
        if not can_manage_channel(interaction, e["channel_id"]):
            continue
        ch = bot.get_channel(e["channel_id"])
        when = datetime.fromtimestamp(e["start_ts"], tz).strftime("%b %d %H:%M")
        label = f"#{ch.name if ch else 'deleted-channel'} · {e['title']} · {when} (#{e['id']})"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=e["id"]))
    return out[:25]


async def _get_event(interaction: discord.Interaction, event_id: int):
    """The event, None if it doesn't exist here. Replies and stops if you can't manage its channel."""
    event = db.event(event_id)
    if not event or event["guild_id"] != interaction.guild_id:
        return None
    if not can_manage_channel(interaction, event["channel_id"]):
        await interaction.response.send_message(
            "You can only manage raids in channels you're an officer for.", ephemeral=True)
        raise app_commands.CheckFailure("not an officer for that channel")
    return event


REPEAT_CHOICES = [app_commands.Choice(name="Off", value=0),
                  app_commands.Choice(name="Every week", value=7),
                  app_commands.Choice(name="Every 2 weeks", value=14)]


def local_to_ts(naive: datetime, tz: ZoneInfo) -> tuple[int | None, str | None]:
    """A wall-clock time in `tz` -> (unix time, None). Around a daylight-saving change some clock times
    don't exist (spring) or happen twice (autumn); those give (None, reason) instead of silently
    landing an hour off."""
    first, second = naive.replace(tzinfo=tz, fold=0), naive.replace(tzinfo=tz, fold=1)
    if first.utcoffset() == second.utcoffset():
        return int(first.timestamp()), None
    if datetime.fromtimestamp(int(first.timestamp()), tz).replace(tzinfo=None) != naive:
        return None, (f"{naive:%H:%M} doesn't exist on {naive:%Y-%m-%d}: the clocks in {tz.key} jump forward "
                      "that night. Pick a different time.")
    return None, (f"{naive:%H:%M} happens twice on {naive:%Y-%m-%d}: the clocks in {tz.key} go back that "
                  "night, so I can't tell which one you mean. Pick a different time.")


# ---------------------------------------------------------------- typing dates and times
BAD_TIME = ("Couldn't read that date/time. Try `thu 8pm`, `tomorrow 19:30`, `nov 14 8pm`, `+7d 20:00` "
            "or `2026-11-14 20:00`.")
NEED_TIME = "Add a time as well, for example `thu 8pm` or `2026-11-14 20:00`."
WEEKDAYS = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "weds": 2, "wednesday": 2,
            "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5,
            "sun": 6, "sunday": 6}
MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5,
          "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
          "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
_TIME_END = re.compile(r"(?:^|\s)((?:[01]?\d|2[0-3]):[0-5]\d|(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*[ap]\.?m\.?"
                       r"|noon|midnight)$")


class WhenError(ValueError):
    """Text the officer typed couldn't be turned into a date and time; str() is what to tell them."""


def parse_time_text(text: str) -> Clock | None:
    """'20:00', '8pm', '8:30 pm', 'noon', 'midnight' -> a time of day, or None if unreadable.
    A bare number like '8' is refused: it could mean morning or evening."""
    t = " ".join(text.lower().split())
    if t == "noon":
        return Clock(12, 0)
    if t == "midnight":
        return Clock(0, 0)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
    if m and int(m[1]) <= 23 and int(m[2]) <= 59:
        return Clock(int(m[1]), int(m[2]))
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", t)
    if m and 1 <= int(m[1]) <= 12 and int(m[2] or 0) <= 59:
        hour = int(m[1]) % 12 + (12 if m[3] == "p" else 0)
        return Clock(hour, int(m[2] or 0))
    return None


def parse_date_text(text: str, now: datetime, at: Clock) -> Date:
    """The day named by `text`, in the server's timezone. `now` is the local wall-clock time and `at` the
    time of day the raid will start (a bare weekday means today only if that time hasn't passed yet)."""
    today = now.date()
    t = " ".join(text.lower().replace(",", " ").split())
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", t)
    if m:
        try:
            return Date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            raise WhenError(f"`{text.strip()}` isn't a real date.") from None
    if t in ("today", "tonight"):
        return today
    if t in ("tomorrow", "tmrw", "tmr"):
        return today + timedelta(days=1)
    m = re.fullmatch(r"(?:(next|this) )?([a-z]+)", t)
    if m and m[2] in WEEKDAYS:
        delta = (WEEKDAYS[m[2]] - today.weekday()) % 7
        if delta == 0 and (m[1] == "next" or datetime.combine(today, at) <= now):
            delta = 7  # "next thu" on a Thursday, or a Thursday evening that has already started
        return today + timedelta(days=delta)
    m = re.fullmatch(r"\+\s*(\d{1,3})\s*(d|w|days?|weeks?)|in (\d{1,3}) (days?|weeks?)", t)
    if m:
        n, unit = int(m[1] or m[3]), (m[2] or m[4])
        return today + timedelta(days=n * (7 if unit.startswith("w") else 1))
    m = re.fullmatch(rf"(?:({_MONTH_RE}) (\d{{1,2}})(?:st|nd|rd|th)?|(\d{{1,2}})(?:st|nd|rd|th)? ({_MONTH_RE}))(?: (\d{{4}}))?", t)
    if m:
        month, day = MONTHS[m[1] or m[4]], int(m[2] or m[3])
        year = int(m[5]) if m[5] else today.year
        try:
            found = Date(year, month, day)
            if not m[5] and datetime.combine(found, at) <= now:
                found = Date(year + 1, month, day)  # "nov 14" typed on Nov 20 means next year
            return found
        except ValueError:
            raise WhenError(f"`{text.strip()}` isn't a real date.") from None
    raise WhenError(BAD_TIME)


def parse_when(date_text: str | None, time_text: str | None, now: datetime,
               base: datetime | None = None) -> datetime:
    """Date and time as typed -> a naive local datetime. The time may be in its own box or at the end of
    the date text ('thu 8pm'). `base` (when editing) supplies whatever wasn't typed: the current day and/or
    time. Raises WhenError with a plain-English message."""
    d = " ".join((date_text or "").lower().split())
    at = None
    if time_text and time_text.strip():
        at = parse_time_text(time_text)
        if at is None:
            raise WhenError(f"Couldn't read the time `{time_text.strip()}`. Use `20:00` or `8pm`.")
    else:
        m = _TIME_END.search(d)
        if m:
            at = parse_time_text(m[1])
            d = d[:m.start(1)].strip()
            if d.endswith(" at") or d == "at":
                d = d[:-2].strip()
            d = d.removesuffix("@").strip()
    if at is None and base is not None:
        at = base.time()
    if at is None:
        raise WhenError(NEED_TIME)
    if not d:
        if base is None:
            raise WhenError(BAD_TIME)
        return datetime.combine(base.date(), at)
    return datetime.combine(parse_date_text(d, now, at), at)


def parse_start_checked(guild_id: int, date: str, at: str | None = None,
                        base_ts: int | None = None) -> tuple[int | None, str]:
    """What an officer typed -> (unix time, ""), or (None, what to tell them). Understands '2026-11-14' with
    '20:00', but also 'thu', 'tomorrow', 'nov 14', '+7d' and '8pm'. `base_ts` is the raid's current start
    when editing, so you can change just the day or just the time."""
    tz = guild_tz(guild_id)
    now = datetime.now(tz).replace(tzinfo=None)
    base = datetime.fromtimestamp(base_ts, tz).replace(tzinfo=None) if base_ts else None
    try:
        naive = parse_when(date, at, now, base)
    except WhenError as e:
        return None, str(e)
    ts, problem = local_to_ts(naive, tz)
    return ts, problem or ""


def parse_start(guild_id: int, date: str, at: str | None = None) -> int | None:
    """Like parse_start_checked but only the time (None when unreadable or ambiguous)."""
    return parse_start_checked(guild_id, date, at)[0]


async def date_autocomplete(interaction: discord.Interaction, current: str):
    """The next two weeks as 'Thu Nov 12', for picking a day. Anything else typed ('thu 8pm') still works."""
    today = datetime.now(guild_tz(interaction.guild_id)).date()
    out = []
    for i in range(15):
        day = today + timedelta(days=i)
        label = f"{day:%a %b %d}" + (" (today)" if i == 0 else " (tomorrow)" if i == 1 else "")
        if current.lower().strip() in label.lower() or current.strip() in day.isoformat():
            out.append(app_commands.Choice(name=label, value=day.isoformat()))
    return out


DATE_HELP = "Day: thu, tomorrow, nov 14, +7d or 2026-11-14. It can include the time: thu 8pm"
TIME_HELP = "Time, e.g. 20:00 or 8pm (not needed if the date includes it)"


def threads_default(guild_id: int) -> bool:
    return db.setting(guild_id, "threads", "0") == "1"


async def post_event(channel, guild, creator_id: int, title: str, description: str, ts: int,
                     tanks: int, healers: int, dps: int, ping=None, repeat_days: int = 0,
                     thread: bool = False, series_id: int = 0, class_caps: str = ""):
    """Create the event row and its post. Returns (event_id, message, notes). Raises HTTPException
    (and leaves nothing behind) if the post itself can't be sent."""
    event_id = db.create_event(guild.id, channel.id, title, description, ts, creator_id,
                               tanks, healers, dps, repeat_days, class_caps)
    if series_id or repeat_days:  # a repeating raid starts a series; each next one carries the same series id
        db.update_event(event_id, series_id=series_id or event_id)
    event = db.event(event_id)
    if ping and ping.is_default():
        mentions = discord.AllowedMentions(everyone=True)
    elif ping:
        mentions = discord.AllowedMentions(roles=[ping])
    else:
        mentions = discord.AllowedMentions.none()
    try:
        msg = await channel.send(content=ping.mention if ping else None, embed=render_embed(event, [], False),
                                 view=RaidView(spec_switch_on(guild.id)), allowed_mentions=mentions)
    except discord.HTTPException:
        db.delete_event(event_id)
        raise
    db.set_message_id(event_id, msg.id)
    schedule_board(guild.id)
    sched_soon(event_id)
    if autoclose_inside(guild.id, ts):
        db.update_event(event_id, autoclosed=1)  # posted inside the window: don't close it a minute later
    if lowsignup_inside(guild.id, ts):
        db.update_event(event_id, low_alerted=1)  # posted inside the window: the officer posting it knows it's empty
    notes = []
    if ping and not ping.is_default() and not (
            ping.mentionable or channel.permissions_for(guild.me).mention_everyone):
        notes.append(f"{ping.name} isn't mentionable, so nobody was notified. "
                     "Turn on 'Allow anyone to @mention this role' for it.")
    if thread:
        when = datetime.fromtimestamp(ts, guild_tz(guild.id))
        try:
            th = await msg.create_thread(name=f"{title} · {when:%b %d}"[:100], auto_archive_duration=10080)
            db.update_event(event_id, thread_id=th.id)
        except discord.DiscordException:
            notes.append("I couldn't start a thread. Give the bot 'Create Public Threads' permission in this channel.")
    return event_id, msg, notes


async def delete_event_post(event) -> None:
    """Delete the raid's post, its thread and its Discord Scheduled Event, ignoring anything already gone."""
    schedule_board(event["guild_id"])
    if event["sched_event_id"] > 0:
        await remove_sched_event(bot.get_guild(event["guild_id"]), event["sched_event_id"])
    channel = bot.get_channel(event["channel_id"])
    if channel and event["message_id"]:
        try:
            await channel.get_partial_message(event["message_id"]).delete()
        except discord.HTTPException:
            pass
    if event["thread_id"]:
        thread = bot.get_channel(event["thread_id"])
        if thread:
            try:
                await thread.delete()
            except discord.HTTPException:
                pass


async def template_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=t["name"], value=t["name"]) for t in db.templates(interaction.guild_id)
            if current.lower() in t["name"].lower()][:25]


@raid.command(name="create", description="Post a new raid event")
@app_commands.rename(at="time")
@app_commands.describe(date=DATE_HELP, at=TIME_HELP,
                       title="Raid name (optional if you pick a template)",
                       template="Start from a saved template (see /raid template save)",
                       description="Extra info (optional)", tanks="Max tanks (0 = no limit)",
                       healers="Max healers (0 = no limit)", dps="Max DPS (0 = no limit)",
                       ping="Role to ping (default: this channel's, category's or the server's ping role)",
                       repeat="Post the next one automatically", thread="Start a discussion thread under the post",
                       classes="Limit classes, e.g. Mage 4, Warlock 2 (off = no limits)")
@app_commands.choices(repeat=REPEAT_CHOICES)
@app_commands.autocomplete(template=template_autocomplete, date=date_autocomplete)
async def raid_create(interaction: discord.Interaction, date: str, at: Optional[str] = None,
                      title: Optional[str] = None,
                      template: Optional[str] = None, description: Optional[str] = None,
                      tanks: Optional[app_commands.Range[int, 0, 40]] = None,
                      healers: Optional[app_commands.Range[int, 0, 40]] = None,
                      dps: Optional[app_commands.Range[int, 0, 40]] = None,
                      ping: Optional[discord.Role] = None,
                      repeat: Optional[app_commands.Choice[int]] = None, thread: Optional[bool] = None,
                      classes: Optional[str] = None):
    if not can_manage_channel(interaction, interaction.channel):
        return await interaction.response.send_message(
            "You can only post raids in channels you're an officer for. Run this in your own signup channel.",
            ephemeral=True)
    tpl = None
    if template:
        tpl = db.template(interaction.guild_id, template.strip())
        if not tpl:
            return await interaction.response.send_message(
                "No template with that name. See `/raid template list`.", ephemeral=True)
    title = title or (tpl["title"] if tpl else None)
    if not title:
        return await interaction.response.send_message("Give the raid a title, or pick a template.", ephemeral=True)
    ts, problem = parse_start_checked(interaction.guild_id, date, at)
    if ts is None:
        return await interaction.response.send_message(problem, ephemeral=True)
    if ts <= int(time.time()):
        return await interaction.response.send_message("That time is in the past.", ephemeral=True)
    if problem := text_problem(title, description):
        return await interaction.response.send_message(problem, ephemeral=True)
    caps_value = tpl["class_caps"] if (tpl and classes is None) else ""
    if classes is not None:
        caps_value, problem = parse_class_caps(classes)
        if problem:
            return await interaction.response.send_message(problem, ephemeral=True)

    def pick(value, key, default=0):
        return value if value is not None else (tpl[key] if tpl else default)

    await interaction.response.defer(ephemeral=True)
    if ping is None and (default_ping := default_ping_role_id(interaction.guild_id, interaction.channel)):
        ping = interaction.guild.get_role(default_ping)
    try:
        event_id, msg, notes = await post_event(
            interaction.channel, interaction.guild, interaction.user.id, title,
            description if description is not None else (tpl["description"] if tpl else ""), ts,
            pick(tanks, "cap_tank"), pick(healers, "cap_healer"), pick(dps, "cap_dps"), ping=ping,
            repeat_days=repeat.value if repeat is not None else pick(None, "repeat_days"),
            thread=thread if thread is not None else threads_default(interaction.guild_id), class_caps=caps_value)
    except discord.HTTPException:
        return await interaction.followup.send(
            "I couldn't post in this channel. Give the bot View Channel, Send Messages and Embed Links here.",
            ephemeral=True)
    await audit(interaction.guild_id, f"📅 {interaction.user.mention} created **{title}** (#{event_id}) in {interaction.channel.mention}")
    await interaction.followup.send("\n".join([f"Created event #{event_id}: {msg.jump_url}"] +
                                              [f"⚠️ {n}" for n in notes]), ephemeral=True)


@raid.command(name="copy", description="Copy a raid to a new date (same limits and description, no signups)")
@app_commands.rename(at="time")
@app_commands.describe(date=DATE_HELP, at=TIME_HELP, title="New title (default: same)")
@app_commands.autocomplete(event_id=event_autocomplete, date=date_autocomplete)
async def raid_copy(interaction: discord.Interaction, event_id: int, date: str, at: Optional[str] = None,
                    title: Optional[str] = None):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    ts, problem = parse_start_checked(interaction.guild_id, date, at, base_ts=event["start_ts"])
    if ts is None:
        return await interaction.response.send_message(problem, ephemeral=True)
    if ts <= int(time.time()):
        return await interaction.response.send_message("That time is in the past.", ephemeral=True)
    if problem := text_problem(title, None):
        return await interaction.response.send_message(problem, ephemeral=True)
    channel = bot.get_channel(event["channel_id"])
    if channel is None:
        return await interaction.response.send_message("The original channel no longer exists.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    default_ping = default_ping_role_id(interaction.guild_id, channel)
    try:
        new_id, msg, notes = await post_event(
            channel, interaction.guild, interaction.user.id, title or event["title"], event["description"], ts,
            event["cap_tank"], event["cap_healer"], event["cap_dps"],
            ping=interaction.guild.get_role(default_ping) if default_ping else None,
            thread=threads_default(interaction.guild_id), class_caps=event["class_caps"])
    except discord.HTTPException:
        return await interaction.followup.send("I couldn't post in that channel.", ephemeral=True)
    await audit(interaction.guild_id, f"📋 {interaction.user.mention} copied **{event['title']}** (#{event_id}) to #{new_id}")
    await interaction.followup.send("\n".join([f"Copied to event #{new_id}: {msg.jump_url}"] +
                                              [f"⚠️ {n}" for n in notes]), ephemeral=True)


@raid.command(name="repeat", description="Make a raid repeat automatically, or stop it repeating")
@app_commands.describe(every="How often the next raid is posted (it appears when this one starts)")
@app_commands.choices(every=REPEAT_CHOICES)
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_repeat(interaction: discord.Interaction, event_id: int, every: app_commands.Choice[int]):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    if event["repeated"]:
        return await interaction.response.send_message(
            "That raid has already posted its next one. Change the repeat on the upcoming raid instead.",
            ephemeral=True)
    db.update_event(event_id, repeat_days=every.value)
    if every.value and not event["series_id"]:
        db.update_event(event_id, series_id=event_id)
    await interaction.response.send_message(
        f"Event #{event_id} will repeat {every.name.lower()}: the next one is posted when this one starts."
        if every.value else f"Event #{event_id} will no longer repeat.", ephemeral=True)
    await refresh_message(event_id)


async def seed_series(event_id: int, series_id: int) -> None:
    """Sign everyone who joined this series with /always join up to a freshly posted occurrence."""
    members = db.series_members(series_id)
    if not members:
        return
    event = db.event(event_id)
    for m in members:
        status, bench_from = apply_cap(event, m["user_id"], m["role"], "in", m["wow_class"])
        db.set_signup(event_id, m["user_id"], m["char_name"], m["wow_class"], m["spec"], m["role"], status, bench_from)
    await refresh_message(event_id)


async def spawn_next(event) -> bool:
    """Post the next occurrence of a recurring raid, keeping the same wall-clock time across DST.
    Returns False if posting failed and should be tried again, True when done (or hopeless)."""
    tz = guild_tz(event["guild_id"])
    now = int(time.time())
    step = timedelta(days=event["repeat_days"])
    nxt = datetime.fromtimestamp(event["start_ts"], tz) + step
    while int(nxt.timestamp()) <= now:  # bot was off for a while: skip past occurrences
        nxt += step
    channel = bot.get_channel(event["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(event["channel_id"])
        except discord.NotFound:
            log.warning("Recurring raid #%s: its channel is gone, not posting the next one", event["id"])
            return True
        except discord.HTTPException as e:
            log.warning("Recurring raid #%s: couldn't reach its channel (%s), will retry", event["id"], e)
            return False
    role_id = default_ping_role_id(event["guild_id"], channel)
    series = event["series_id"] or event["id"]
    try:
        new_id, _msg, _notes = await post_event(
            channel, channel.guild, event["creator_id"], event["title"], event["description"],
            int(nxt.timestamp()), event["cap_tank"], event["cap_healer"], event["cap_dps"],
            ping=channel.guild.get_role(role_id) if role_id else None,
            repeat_days=event["repeat_days"], thread=threads_default(event["guild_id"]), series_id=series,
            class_caps=event["class_caps"])
        log.info("Posted the next occurrence of recurring raid #%s", event["id"])
        try:
            await seed_series(new_id, series)
        except Exception as err:  # the raid is posted: never report failure (a retry would post it twice)
            log.exception("Couldn't sign up the 'always in' members for raid #%s", new_id)
            note_error(f"'always in' sign-ups for raid #{new_id}: {err!r}")
    except discord.HTTPException as e:
        log.warning("Couldn't post the next occurrence of raid #%s: %s", event["id"], e)
        note_error(f"couldn't post the next occurrence of raid #{event['id']}: {e}")
        return False
    return True


# ---- templates: saved raid setups
template = RaidGroup(name="template", description="Saved raid setups", parent=raid)


@template.command(name="save", description="Save an existing raid's title, description, limits and repeat as a template")
@app_commands.describe(name="Template name, e.g. Naxx", event_id="The raid to copy the setup from")
@app_commands.autocomplete(event_id=event_autocomplete)
async def template_save(interaction: discord.Interaction, name: str, event_id: int):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    name = name.strip()[:32]
    if not name:
        return await interaction.response.send_message("Give the template a name.", ephemeral=True)
    if db.template(interaction.guild_id, name) and not is_officer(interaction):
        return await interaction.response.send_message(
            "A template with that name already exists, and templates are shared by the whole server. "
            "Pick another name, or ask a server-wide officer to replace it.", ephemeral=True)
    db.save_template(interaction.guild_id, name, event["title"], event["description"], event["cap_tank"],
                     event["cap_healer"], event["cap_dps"], event["repeat_days"], event["class_caps"])
    await interaction.response.send_message(
        f"Saved template **{name}**. Use it with `/raid create template: {name}` (just the date and time).",
        ephemeral=True)


@template.command(name="list", description="Show the saved templates")
async def template_list(interaction: discord.Interaction):
    rows = db.templates(interaction.guild_id)
    if not rows:
        return await interaction.response.send_message(
            "No templates yet. Make a raid, then `/raid template save`.", ephemeral=True)
    lines = []
    for t in rows:
        caps = "/".join(str(t[k]) if t[k] else "any" for k in ("cap_tank", "cap_healer", "cap_dps"))
        rep = {7: " 🔁 weekly", 14: " 🔁 every 2 weeks"}.get(t["repeat_days"], "")
        by_class = ", ".join(f"{c} {n}" for c, n in sorted(caps_dict(t["class_caps"]).items()))
        lines.append(f"**{t['name']}**: {t['title']} (tanks/healers/dps {caps}){rep}" + (f" · max {by_class}" if by_class else ""))
    await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True)


@template.command(name="delete", description="Delete a saved template")
@app_commands.autocomplete(name=template_autocomplete)
async def template_delete(interaction: discord.Interaction, name: str):
    if not is_officer(interaction):
        return await interaction.response.send_message(
            "Templates are shared by the whole server, so only server-wide officers can delete them.", ephemeral=True)
    ok = db.delete_template(interaction.guild_id, name.strip())
    await interaction.response.send_message(
        f"Deleted template **{name}**." if ok else "No template with that name.", ephemeral=True)


@raid.command(name="edit", description="Change an event's title, time, description or role limits")
@app_commands.rename(at="time")
@app_commands.describe(title="New title", description="New description", date="New day: " + DATE_HELP[5:],
                       at="New time, e.g. 20:00 or 8pm", tanks="New tank limit (0 = no limit)",
                       healers="New healer limit (0 = no limit)", dps="New DPS limit (0 = no limit)",
                       notify="If the time changed, tell everyone who signed up (default yes)",
                       classes="New class limits, e.g. Mage 4, Warlock 2 (off = no limits)")
@app_commands.autocomplete(event_id=event_autocomplete, date=date_autocomplete)
async def raid_edit(interaction: discord.Interaction, event_id: int, title: Optional[str] = None,
                    description: Optional[str] = None, date: Optional[str] = None,
                    at: Optional[str] = None, tanks: Optional[app_commands.Range[int, 0, 40]] = None,
                    healers: Optional[app_commands.Range[int, 0, 40]] = None,
                    dps: Optional[app_commands.Range[int, 0, 40]] = None, notify: bool = True,
                    classes: Optional[str] = None):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    if problem := text_problem(title, description):
        return await interaction.response.send_message(problem, ephemeral=True)
    fields: dict = {}
    if title:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    for key, val in (("cap_tank", tanks), ("cap_healer", healers), ("cap_dps", dps)):
        if val is not None:
            fields[key] = val
    if classes is not None:
        fields["class_caps"], problem = parse_class_caps(classes)
        if problem:
            return await interaction.response.send_message(problem, ephemeral=True)
    time_changed = False
    if date or at:
        ts, problem = parse_start_checked(interaction.guild_id, date or "", at, base_ts=event["start_ts"])
        if ts is None:
            return await interaction.response.send_message(problem, ephemeral=True)
        if ts <= int(time.time()):
            return await interaction.response.send_message("That time is in the past.", ephemeral=True)
        if ts != event["start_ts"]:
            fields.update(start_ts=ts, reminded=0, lock_shown=0, nudged=0,  # re-arm the reminder, nudge and lock
                          autoclosed=1 if autoclose_inside(event["guild_id"], ts) else 0,
                          low_alerted=1 if lowsignup_inside(event["guild_id"], ts) else 0)
            if event["autoclosed"] and event["closed"]:
                db.set_closed(event_id, False)  # it was closed automatically for the old time: reopen
            time_changed = True
    if not fields:
        return await interaction.response.send_message("Nothing to change. Fill in at least one option.",
                                                       ephemeral=True)
    db.update_event(event_id, **fields)
    await audit(interaction.guild_id, f"✏️ {interaction.user.mention} edited **{event['title']}** (#{event_id}): "
                                      + ", ".join(sorted(k for k in fields if k not in ("reminded", "lock_shown", "nudged", "autoclosed", "low_alerted"))))
    await interaction.response.send_message(f"Updated event #{event_id}.", ephemeral=True)
    await update_event(event_id)  # a raised limit may free bench slots
    sched_soon(event_id)  # the Scheduled Event (/alerts events) follows a new title, time or description
    if time_changed and notify:
        users = [x["user_id"] for x in db.signups(event_id) if x["status"] in ("in", "late", "tentative", "bench")]
        channel = bot.get_channel(event["channel_id"])
        if channel and users:
            try:
                await channel.send(f"📅 **{fields.get('title', event['title'])}** moved to <t:{fields['start_ts']}:F> "
                                   f"(<t:{fields['start_ts']}:R>). " + " ".join(f"<@{u}>" for u in users),
                                   allowed_mentions=USERS_ONLY)
            except discord.HTTPException:
                pass


def raid_line(e, show_id: bool = True) -> str:
    """Two lines about one raid: linked title, time, channel, then who's coming per role (for /raid list and the board)."""
    signups = db.signups(e["id"])
    attending = [x for x in signups if x["status"] in ("in", "late")]
    by_role = {r: sum(1 for x in attending if x["role"] == r) for r in wd.ROLES}

    def part(letter: str, role: str) -> str:
        cap = e[f"cap_{role}"]
        return f"{letter} {by_role[role]}/{cap}" if cap else f"{letter} {by_role[role]}"

    others = {st: sum(1 for x in signups if x["status"] == st) for st in ("tentative", "bench")}
    extra = "".join(f" · {n} {st}" for st, n in others.items() if n) + (" · 🔒 closed" if e["closed"] else "")
    link = f"https://discord.com/channels/{e['guild_id']}/{e['channel_id']}/{e['message_id']}"
    name = f"#{e['id']} {e['title']}" if show_id else e["title"]
    return (f"[**{name}**]({link}) · <t:{e['start_ts']}:f> (<t:{e['start_ts']}:R>) · <#{e['channel_id']}>\n"
            f"👥 {len(attending)} in · {part('🛡️', 'tank')} · {part('💚', 'healer')} · {part('⚔️', 'dps')}{extra}")


@raid.command(name="list", description="Upcoming raids you can manage, with sign-up counts and links")
async def raid_list(interaction: discord.Interaction):
    now = int(time.time())
    rows = [e for e in db.upcoming_events(interaction.guild_id, now)
            if e["message_id"] and can_manage_channel(interaction, e["channel_id"])]
    if not rows:
        return await interaction.response.send_message("No upcoming raids that you can manage.", ephemeral=True)
    text = "\n".join(raid_line(e) for e in rows[:15])
    if len(rows) > 15:
        text += f"\n…and {len(rows) - 15} more"
    await interaction.response.send_message(text[:2000], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


STATUS_CHOICES = [app_commands.Choice(name="In", value="in"), app_commands.Choice(name="Late", value="late"),
                  app_commands.Choice(name="Tentative", value="tentative"), app_commands.Choice(name="Bench", value="bench"),
                  app_commands.Choice(name="Absent", value="absent")]


async def member_character_autocomplete(interaction: discord.Interaction, current: str):
    """The saved characters of the member chosen in this command (so an officer can pick one of theirs)."""
    uid = getattr(getattr(interaction.namespace, "member", None), "id", None)
    if not uid:
        return []
    return [app_commands.Choice(name=char_label(c), value=c["name"])
            for c in db.characters(uid) if current.lower() in c["name"].lower()][:25]


@raid.command(name="add", description="Sign someone up, or change their status or character")
@app_commands.describe(member="Who to sign up (or whose signup to change)", status="Default: in",
                       character="One of their saved characters (default: the one they signed up with, else their main)",
                       wowclass="Class, if they have no saved character", spec="Spec, if they have no saved character")
@app_commands.autocomplete(event_id=event_autocomplete, character=member_character_autocomplete,
                           wowclass=class_autocomplete, spec=spec_autocomplete)
@app_commands.choices(status=STATUS_CHOICES)
async def raid_add(interaction: discord.Interaction, event_id: int, member: discord.Member,
                   status: Optional[app_commands.Choice[str]] = None, character: Optional[str] = None,
                   wowclass: Optional[str] = None, spec: Optional[str] = None):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    if time.time() >= event["start_ts"]:
        return await interaction.response.send_message("That raid has already started.", ephemeral=True)
    if member.bot:
        return await interaction.response.send_message("Bots can't sign up.", ephemeral=True)
    st = status.value if status else "in"
    quiet = discord.AllowedMentions.none()
    if st == "absent":
        db.set_signup(event_id, member.id, None, None, None, None, "absent")
        text = f"Marked {member.mention} as absent for **{event['title']}**."
        final = "absent"
    else:
        row = None
        if wowclass or spec:
            cls = wd.find_class(wowclass)
            sp = wd.find_spec(cls, spec)
            if not cls or not sp:
                return await interaction.response.send_message(
                    "Give both a valid class and a spec (or pick one of their saved characters).", ephemeral=True)
            row = {"char_name": (character or member.display_name)[:32], "wow_class": cls, "spec": sp,
                   "role": wd.role_for(cls, sp)}
        elif character:
            c = db.character_by_name(member.id, character)
            if not c:
                return await interaction.response.send_message(
                    f"{member.display_name} has no saved character called {character}.", ephemeral=True)
            row = {"char_name": c["name"], "wow_class": c["wow_class"], "spec": c["spec"], "role": c["role"]}
        else:
            existing = db.signup(event_id, member.id)
            if existing and existing["char_name"]:
                row = existing
            elif db.main_character(member.id):
                m = db.main_character(member.id)
                row = {"char_name": m["name"], "wow_class": m["wow_class"], "spec": m["spec"], "role": m["role"]}
        if row is None:
            return await interaction.response.send_message(
                f"{member.display_name} has no saved character. Give a `wowclass` and `spec`, or ask them to use "
                "`/character add`.", ephemeral=True)
        final, bench_from = apply_cap(event, member.id, row["role"], st, row["wow_class"])
        db.set_signup(event_id, member.id, row["char_name"], row["wow_class"], row["spec"], row["role"], final, bench_from)
        text = f"{member.mention} is now **{final}** for **{event['title']}** as **{row['char_name']}** — {row['spec']} {row['wow_class']}."
        if final != st:
            text += f" ({bench_reason(event, member.id, row['role'], row['wow_class'])}, so they went on the bench.)"
    await audit(interaction.guild_id, f"➕ {interaction.user.mention} set {member.mention} to {final} for **{event['title']}** (#{event_id})")
    await interaction.response.send_message(text, ephemeral=True, allowed_mentions=quiet)
    await update_event(event_id)


@raid.command(name="promote", description="Move a benched player into the raid, ahead of the line")
@app_commands.describe(member="The benched player to move in",
                       over_limit="Move them in even if their role or class is already full (default: no)")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_promote(interaction: discord.Interaction, event_id: int, member: discord.Member,
                       over_limit: bool = False):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    if time.time() >= event["start_ts"]:
        return await interaction.response.send_message("That raid has already started.", ephemeral=True)
    quiet = discord.AllowedMentions.none()
    row = db.signup(event_id, member.id)
    if not row or row["status"] != "bench":
        now = f"is **{row['status']}**" if row else "isn't signed up"
        return await interaction.response.send_message(
            f"{member.mention} {now} for **{event['title']}**, so there's nothing to promote. "
            "`/raid promote` is for players on the bench; `/raid add` changes anyone's status.",
            ephemeral=True, allowed_mentions=quiet)
    if not row["role"] or not row["char_name"]:
        return await interaction.response.send_message(
            f"{member.mention} is on the bench without a character. Use `/raid add` to sign them up as one.",
            ephemeral=True, allowed_mentions=quiet)
    full = ""
    if apply_cap(event, member.id, row["role"], "in", row["wow_class"])[0] == "bench":  # their role or class is full
        full = bench_reason(event, member.id, row["role"], row["wow_class"])
    if full and not over_limit:
        return await interaction.response.send_message(
            f"Nothing was changed: {full}. Move someone out first, or run it again with `over_limit: True` "
            "to take them in anyway.", ephemeral=True, allowed_mentions=quiet)
    status = row["bench_from"] if row["bench_from"] in ("in", "late") else "in"  # a benched 'late' stays late
    db.set_signup(event_id, member.id, row["char_name"], row["wow_class"], row["spec"], row["role"], status, None)
    text = f"Moved {member.mention} off the bench: **{row['char_name']}** is now **{status}** for **{event['title']}**."
    if full:
        text += f" ({full[0].upper()}{full[1:]}, so this goes over the limit.)"
    await audit(interaction.guild_id, f"⬆️ {interaction.user.mention} promoted {member.mention} from the bench for "
                f"**{event['title']}** (#{event_id}){' over the limit' if full else ''}")
    await interaction.response.send_message(text, ephemeral=True, allowed_mentions=quiet)
    await tell_player(event, member.id, f"🎉 An officer moved you off the bench for **{event['title']}**. You're {status}!")
    await update_event(event_id)


@raid.command(name="roster", description="Private, copy-friendly list of who is coming")
@app_commands.describe(invites="True = /invite Name-Realm lines for everyone who is in, to paste into WoW")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_roster(interaction: discord.Interaction, event_id: int, invites: bool = False):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    signups = db.signups(event_id)
    if invites:
        return await interaction.response.send_message(invite_export(event, signups), ephemeral=True)
    groups = {g: [] for g in wd.GROUPS}
    for x in signups:
        if x["status"] in ("in", "late") and x["char_name"] and not x["no_show"]:  # no-shows aren't "coming"
            groups[wd.group_for(x["wow_class"], x["spec"])].append(x["char_name"])
    lines = [f"{event['title']}  -  {sum(map(len, groups.values()))} attending"]
    for g in wd.GROUPS:
        lines.append(f"{wd.GROUP_LABEL[g]} ({len(groups[g])}): {', '.join(groups[g]) or '-'}")
    bench = [x["char_name"] for x in signups if x["status"] == "bench" and x["char_name"]]
    if bench:
        lines.append(f"Bench ({len(bench)}): {', '.join(bench)}")
    noted = [x for x in signups if x["note"] and x["status"] != "absent"] if notes_mode(event["guild_id"]) != "off" else []
    if noted:  # /config notes: the players' own notes, e.g. "late 15 min"
        lines.append("")
        lines.append(f"Notes ({len(noted)}):")
        member = getattr(interaction.guild, "get_member", lambda _: None)
        for x in noted:
            who = x["char_name"] or getattr(member(x["user_id"]), "display_name", None) or f"user {x['user_id']}"
            lines.append(f"  {who} ({x['status']}): {x['note']}")
    await interaction.response.send_message(_clip_block(lines), ephemeral=True)


MACRO_LIMIT = 255  # characters in one WoW macro


def invite_name(name: str, realm: str | None) -> str:
    """How WoW wants a name for /invite: Name-Realm, with the spaces taken out of the realm ("Area 52" -> "Area52")."""
    realm = "".join((realm or "").split())
    return f"{name}-{realm}" if realm else name


def invite_export(event, signups) -> str:
    """/raid roster invites: one /invite line per player who is in (or late), grouped into blocks that each fit in a
    WoW macro. The realm comes from the saved character (always, not only with /config realms), so players from
    another realm get the Name-Realm form that WoW needs."""
    coming = [x for x in signups if x["status"] in ("in", "late") and x["char_name"] and not x["no_show"]]
    missing = sum(1 for x in signups if x["status"] in ("in", "late") and not x["char_name"] and not x["no_show"])
    if not coming:
        return f"Nobody with a character is in for **{event['title']}** yet, so there's nobody to invite."
    realms = db.realms_for(coming)
    lines, size = [], 0
    for x in coming:
        line = f"/invite {invite_name(x['char_name'], realms.get(x['user_id']))}"
        if lines and size + len(line) + 1 > MACRO_LIMIT:
            lines.append("")  # a blank line starts the next macro-sized block
            size = 0
        lines.append(line)
        size += len(line) + 1
    text = (f"**{event['title']}**: {len(coming)} to invite. Paste a line into WoW chat, or each block into its own "
            "macro (a macro holds 255 characters).")
    if missing:
        text += f" {missing} signed up without a character, so they aren't listed."
    return text + "\n" + _clip_block(lines, 1980 - len(text))


def _clip_block(lines: list[str], room: int = 1980) -> str:
    """The roster as one code block, cut to fit in a Discord message (2000 characters, or `room` for the block)."""
    body = "\n".join(lines)
    if len(body) > room:
        body = body[:room - 10].rsplit("\n", 1)[0] + "\n…"
    return "```\n" + body + "\n```"


@raid.command(name="close", description="Close or reopen signups for an event")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_close(interaction: discord.Interaction, event_id: int):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    db.set_closed(event_id, not event["closed"])
    await audit(interaction.guild_id, f"🔒 {interaction.user.mention} {'closed' if not event['closed'] else 'reopened'} signups for **{event['title']}** (#{event_id})")
    await interaction.response.send_message(
        "Signups closed." if not event["closed"] else "Signups reopened.", ephemeral=True)
    await refresh_message(event_id)


class ConfirmView(discord.ui.View):
    """Private "are you sure?" buttons for a step that deletes something (/config confirm turns them on for officers'
    deletes, off by default; /forgetme always asks). Only the person who ran the command sees them (the reply is
    ephemeral). No answer within a minute means nothing happens."""

    def __init__(self, interaction: discord.Interaction, confirm_label: str, action):
        super().__init__(timeout=60)
        self.interaction, self.action = interaction, action  # action(click, reply) does the work, reply(text) answers
        self.yes.label = confirm_label

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.action(interaction, lambda text: interaction.response.edit_message(content=text, view=None))

    @discord.ui.button(label="No, keep it", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Nothing was changed.", view=None)

    async def on_timeout(self):
        try:
            await self.interaction.edit_original_response(content="No answer, so nothing was changed.", view=None)
        except discord.HTTPException:
            pass


async def confirm_then(interaction: discord.Interaction, question: str, label: str, action) -> None:
    """Ask first if /config confirm is on, otherwise do it straight away. `action(interaction, reply)` does the work;
    `reply(text)` answers privately either way."""
    if db.setting(interaction.guild_id, "confirm_deletes", "0") != "1":
        return await action(interaction, lambda text: interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()))
    await interaction.response.send_message(question, view=ConfirmView(interaction, label, action), ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


def _signup_count(event_id: int) -> str:
    rows = db.signups(event_id)
    coming = sum(1 for r in rows if r["status"] in ("in", "late"))
    return f"{plural(len(rows), 'signup')} ({coming} coming)" if rows else "no signups"


@raid.command(name="cancel", description="Delete an event and its post (/raid restore undoes it)")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_cancel(interaction: discord.Interaction, event_id: int):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    when = f"<t:{event['start_ts']}:f>"

    async def do_cancel(click: discord.Interaction, reply):
        current = db.event(event_id)
        if not current:
            return await reply(f"Event #{event_id} is already gone.")
        db.cancel_event(event_id, click.user.id)
        await reply(f"Event #{event_id} (**{current['title']}**) cancelled. Changed your mind? "
                    f"`/raid restore` brings it back with its signups for the next {KEEP_CANCELLED_DAYS} days.")
        await delete_event_post(current)
        await audit(click.guild_id, f"🗑️ {click.user.mention} cancelled **{current['title']}** (#{event_id})")

    await confirm_then(
        interaction, f"Cancel **{event['title']}** ({when}, #{event_id})? Its post goes away and it has "
                     f"{_signup_count(event_id)}.", "Yes, cancel it", do_cancel)


async def cancelled_autocomplete(interaction: discord.Interaction, current: str):
    out = []
    for c in db.cancelled_events(interaction.guild_id):
        when = datetime.fromtimestamp(c["start_ts"], guild_tz(interaction.guild_id)).strftime("%a %b %d %H:%M")
        label = f"{c['title']} · {when} (#{c['event_id']})"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=c["event_id"]))
    return out[:25]


@raid.command(name="restore", description="Bring back a raid cancelled in the last 14 days, with its signups")
@app_commands.describe(event_id="The cancelled raid (the list shows this server's recent cancellations)")
@app_commands.autocomplete(event_id=cancelled_autocomplete)
async def raid_restore(interaction: discord.Interaction, event_id: int):
    copy = db.cancelled_event(event_id)
    if not copy or copy["guild_id"] != interaction.guild_id:
        return await interaction.response.send_message(
            f"There's no cancelled raid #{event_id} to restore. Raids can be restored for "
            f"{KEEP_CANCELLED_DAYS} days after they're cancelled.", ephemeral=True)
    if not can_manage_channel(interaction, copy["channel_id"]):
        return await interaction.response.send_message(
            "You can only manage raids in channels you're an officer for.", ephemeral=True)
    channel = bot.get_channel(copy["channel_id"])
    if channel is None:
        return await interaction.response.send_message(
            "The raid's channel no longer exists, so there's nowhere to post it again.", ephemeral=True)
    restored = db.restore_event(event_id)
    if restored is None:
        return await interaction.response.send_message(f"Raid #{event_id} has already been restored.", ephemeral=True)
    event = db.event(event_id)
    locked = is_locked(event)
    try:
        msg = await channel.send(embed=render_embed(event, db.signups(event_id), locked),
                                 view=ClosedView() if locked else RaidView(spec_switch_on(event["guild_id"])),
                                 allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        db.undo_restore(copy)  # put it back the way it was, so it can still be restored later
        return await interaction.response.send_message(
            f"I couldn't post in {channel.mention}, so the raid is still cancelled. Check my permissions there.",
            ephemeral=True)
    db.set_message_id(event_id, msg.id)
    schedule_board(interaction.guild_id)
    sched_soon(event_id)
    notes = ""
    if json.loads(copy["event_json"]).get("thread_id"):  # it had a discussion thread: start a new one
        when = datetime.fromtimestamp(event["start_ts"], guild_tz(interaction.guild_id))
        try:
            th = await msg.create_thread(name=f"{event['title']} · {when:%b %d}"[:100], auto_archive_duration=10080)
            db.update_event(event_id, thread_id=th.id)
        except discord.DiscordException:
            notes = " (I couldn't start its discussion thread again.)"
    await audit(interaction.guild_id, f"♻️ {interaction.user.mention} restored **{event['title']}** (#{event_id})")
    await interaction.response.send_message(
        f"Restored **{event['title']}** with {plural(restored, 'signup')}: {getattr(msg, 'jump_url', '')}{notes}",
        ephemeral=True)  # signups come back exactly as they were, so nobody is promoted or messaged here


@raid.command(name="remove", description="Remove a member's signup")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_remove(interaction: discord.Interaction, event_id: int, member: discord.Member):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    row = db.signup(event_id, member.id)
    if not row:
        return await interaction.response.send_message(
            f"{member.mention} isn't signed up for **{event['title']}**, so there's nothing to remove.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    what = f"{row['char_name']}, {row['status']}" if row["char_name"] else row["status"]

    async def do_remove(click: discord.Interaction, reply):
        db.remove_signup(event_id, member.id)
        await reply(f"Removed {member.mention} from event #{event_id}.")
        await audit(click.guild_id, f"🚫 {click.user.mention} removed {member.mention} from #{event_id}")
        await update_event(event_id)

    await confirm_then(interaction, f"Remove {member.mention} ({what}) from **{event['title']}** (#{event_id})?",
                       "Yes, remove", do_remove)


@raid.command(name="unsigned", description="List members of a role who haven't responded")
@app_commands.describe(role="Raider role to check", ping="Post publicly and ping them")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_unsigned(interaction: discord.Interaction, event_id: int, role: discord.Role,
                        ping: bool = False):
    if not await _get_event(interaction, event_id):
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    answered = {s["user_id"] for s in db.signups(event_id)}
    missing = [m for m in role.members if not m.bot and m.id not in answered]
    if not missing:
        return await interaction.response.send_message("Everyone in that role has responded. ✅",
                                                       ephemeral=True)
    text = f"Haven't responded to event #{event_id}: " + " ".join(m.mention for m in missing)
    await interaction.response.send_message(text[:2000], ephemeral=not ping)


# ---------------------------------------------------------------- reminders
async def nudge_unanswered(event) -> None:
    """Ping members of the raid's ping role who haven't signed up or declined yet."""
    channel = bot.get_channel(event["channel_id"])
    role_id = default_ping_role_id(event["guild_id"], channel) if channel else None
    role = channel.guild.get_role(role_id) if role_id else None
    if role is None:
        return
    answered = {x["user_id"] for x in db.signups(event["id"])}
    missing = [m for m in role.members if not m.bot and m.id not in answered]
    if not missing:
        return
    head = f"👋 **{event['title']}** starts <t:{event['start_ts']}:R>. Still waiting to hear from: "
    mentions = ""
    for m in missing:
        if len(head) + len(mentions) + len(m.mention) + 20 > 2000:
            mentions += f" …and {len(missing) - mentions.count('<@')} more"
            break
        mentions += m.mention + " "
    try:
        await channel.send(head + mentions, allowed_mentions=USERS_ONLY)
    except discord.HTTPException as e:
        log.warning("Nudge failed for event %s: %s", event["id"], e)


MAX_TRIES = 5  # a job that keeps failing is retried once a minute this many times, then given up
WAIT = "wait"  # a job's way of saying "not due yet": nothing failed, nothing is marked done
_tries: dict[tuple[str, int], int] = {}


async def _attempt(kind: str, event_id: int, work) -> bool:
    """Run one piece of scheduled work for one raid. `work` returns True when finished, False to try
    again next minute, or WAIT when it isn't due. An exception counts as False and never escapes, so
    one bad raid can't stop the others. Returns True once the work is finished or has been given up on
    (after MAX_TRIES failures), meaning the caller should mark it done."""
    key = (kind, event_id)
    try:
        result = await work()
    except Exception as e:
        log.exception("Scheduled %s for raid #%s failed", kind, event_id)
        note_error(f"{kind} for raid #{event_id}: {e!r}")
        result = False
    if result == WAIT:
        return False
    if result is not False:
        _tries.pop(key, None)
        return True
    _tries[key] = _tries.get(key, 0) + 1
    if _tries[key] >= MAX_TRIES:
        _tries.pop(key, None)
        log.error("Giving up on %s for raid #%s after %s tries", kind, event_id, MAX_TRIES)
        note_error(f"gave up on {kind} for raid #{event_id} after {MAX_TRIES} tries")
        return True
    return False


def mention_list(head: str, user_ids, limit: int = 2000) -> str:
    """head followed by @mentions, cut between mentions (never through one) to fit one message."""
    out = head
    for n, uid in enumerate(user_ids):
        piece = f"<@{uid}> "
        if len(out) + len(piece) > limit - 25:
            return out + f"…and {len(user_ids) - n} more"
        out += piece
    return out.rstrip()


_dm_sent: set[tuple[int, int]] = set()  # (event, user) already sent a reminder DM, so a retry doesn't repeat it


def wants_dm(guild_id: int, user_id: int) -> bool:
    return db.setting(guild_id, f"dm_remind@{user_id}") == "1"


async def _remind_one(event, now: int):
    if event["start_ts"] - guild_reminder_minutes(event["guild_id"]) * 60 > now:
        return WAIT
    users = [x["user_id"] for x in db.signups(event["id"]) if x["status"] in ("in", "late", "tentative")]
    if not users:
        return WAIT  # nobody to remind yet: keep the reminder for when the first person signs up
    link = f"https://discord.com/channels/{event['guild_id']}/{event['channel_id']}/{event['message_id']}"
    ping_users = []
    for uid in users:
        if not wants_dm(event["guild_id"], uid):
            ping_users.append(uid)
        elif (event["id"], uid) not in _dm_sent:
            try:
                await (await bot.fetch_user(uid)).send(f"⏰ **{event['title']}** starts <t:{event['start_ts']}:R>! {link}")
                _dm_sent.add((event["id"], uid))
            except discord.HTTPException:
                ping_users.append(uid)  # their DMs are closed: ping them in the channel like everyone else
    if len(_dm_sent) > 5000:
        _dm_sent.clear()
    if not ping_users:
        return True
    channel = bot.get_channel(event["channel_id"])
    if channel is None:
        return True
    try:
        await channel.send(mention_list(f"⏰ **{event['title']}** starts <t:{event['start_ts']}:R>! ", ping_users),
                           allowed_mentions=USERS_ONLY)
    except discord.HTTPException as err:
        log.warning("Reminder failed for event %s: %s", event["id"], err)
        note_error(f"reminder for raid #{event['id']}: {err}")
        return False
    return True


async def _archive_one(event, now: int):
    days = int_setting(event["guild_id"], "archive_days")
    if not days or event["start_ts"] > now - days * 86400:
        return WAIT
    await delete_event_post(event)
    return True


async def _nudge_one(event, now: int):
    hours = int_setting(event["guild_id"], "nudge_hours")
    if not hours or event["start_ts"] - hours * 3600 > now:
        return WAIT
    await nudge_unanswered(event)
    return True


_UPDATE = {"next_check": 0.0, "tag": None, "url": None}  # the newest release GitHub told us about, and when to ask again


async def latest_release() -> tuple[str, str] | None:
    """(tag, page address) of the newest GitHub release, or None if it couldn't be found out."""
    try:
        data = await asyncio.to_thread(http_get_json, RELEASES_API)
    except (LookupError, ConnectionError):
        return None
    tag, url = data.get("tag_name") if isinstance(data, dict) else None, data.get("html_url") if isinstance(data, dict) else None
    if not isinstance(tag, str) or not parse_version(tag) or not isinstance(url, str) or not url.startswith("https://github.com/"):
        return None
    return tag, url


async def _notice_one(guild_id: int, tag: str, url: str):
    if not release_is_newer(tag) or db.setting(guild_id, "update_announced") == tag:
        return True
    log_id = int_setting(guild_id, "log_channel")
    channel = bot.get_channel(log_id) if log_id else None
    if channel is None:
        return True  # nowhere to say it
    try:
        await channel.send(f"🆕 **SuperDuperRaidBot {tag}** is available (this bot is v{VERSION}). On Windows, close the bot "
                           f"and double-click `Update.bat`. What's new: <{url}>", allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as err:
        log.warning("Update notice failed for guild %s: %s", guild_id, err)
        return False
    db.set_setting(guild_id, "update_announced", tag)
    return True


async def _run_updatecheck(now: int) -> None:
    """Optional (/config updatenotice): about once a day, ask GitHub for the newest release and say so once per version."""
    guilds = db.guilds_with_setting("update_notice", "1")
    if not guilds or now < _UPDATE["next_check"]:
        return
    found = await latest_release()
    _UPDATE["next_check"] = now + (86400 if found else 3600)  # a failed check is retried in an hour
    if not found:
        return
    _UPDATE["tag"], _UPDATE["url"] = found
    for g in guilds:
        await _attempt("updatenotice", g, partial(_notice_one, g, found[0], found[1]))


async def _autoclose_one(event, now: int):
    hours = int_setting(event["guild_id"], "autoclose_hours")
    if not hours or event["start_ts"] - hours * 3600 > now:
        return WAIT
    db.set_closed(event["id"], True)
    if await refresh_message(event["id"]) is False:
        return False
    await audit(event["guild_id"], f"🔒 Signups for **{event['title']}** (#{event['id']}) closed automatically, "
                                   f"{plural(hours, 'hour')} before the start")
    return True


async def _run_autoclose(now: int) -> None:
    for event in db.pending_autoclose(now):  # optional: close sign-ups N hours before the start (/config autoclose)
        if await _attempt("autoclose", event["id"], partial(_autoclose_one, event, now)):
            db.update_event(event["id"], autoclosed=1)


def scoped_setting(guild_id: int, channel, key: str) -> str | None:
    """A setting for this channel, else its category, else the server-wide one (same order as the ping role)."""
    for cid in _scope_ids(channel):
        value = db.setting(guild_id, f"{key}@{cid}")
        if value:
            return value
    return db.setting(guild_id, key)


def voice_channel_id(guild_id: int, channel) -> int | None:
    value = scoped_setting(guild_id, channel, "voice_channel")
    try:
        return int(value) if value else None
    except ValueError:
        return None


async def _voicecheck_one(event, now: int):
    """Some time after the start: anyone signed up as 'in' who isn't in the raid's voice channel is marked as a
    no-show (officers can undo with /raid noshow undo: true). Skipped if nobody at all is in the channel, since
    the raid is probably being run somewhere else."""
    g = event["guild_id"]
    if now < event["start_ts"] + int_setting(g, "voice_check_minutes", 15) * 60:
        return WAIT
    channel = bot.get_channel(event["channel_id"])
    vid = voice_channel_id(g, channel) if channel else voice_channel_id(g, None)
    if not vid:
        return True  # the feature is off: nothing to do, and don't act retroactively if it is turned on later
    voice = bot.get_channel(vid)
    members = getattr(voice, "members", None)
    if members is None:
        return True
    present = {m.id for m in members}
    expected = [x for x in db.signups(event["id"]) if x["status"] == "in" and not x["no_show"]]
    if not present:
        await audit(g, f"🎙️ Voice check for **{event['title']}** (#{event['id']}) skipped: nobody was in <#{vid}>. "
                       "Use `/raid noshow` if you want to mark people by hand.")
        return True
    missing = [x for x in expected if x["user_id"] not in present]
    for x in missing:
        db.set_no_show(event["id"], x["user_id"], True)
    if missing:
        await audit(g, f"🎙️ Voice check for **{event['title']}** (#{event['id']}): {len(expected) - len(missing)} of "
                       f"{len(expected)} signed-up members were in <#{vid}>. Marked as no-shows: "
                       + " ".join(f"<@{x['user_id']}>" for x in missing) + ". Undo with `/raid noshow` (undo: True).")
    return True


async def _run_voicecheck(now: int) -> None:
    for event in db.pending_voicecheck(now):
        if await _attempt("voicecheck", event["id"], partial(_voicecheck_one, event, now)):
            db.update_event(event["id"], voice_checked=1)


def _name(guild, uid: int) -> str:
    member = guild.get_member(uid) if guild else None
    return member.display_name if member else f"<@{uid}>"


async def _digest_one(guild_id: int, now: int):
    """The optional weekly attendance summary, posted in the log channel."""
    log_id = int_setting(guild_id, "log_channel")
    channel = bot.get_channel(log_id) if log_id else None
    if channel is None:
        return True  # nowhere to post it: give up for this week
    rows = db.stats(guild_id, now - 7 * 86400, now)
    raids = db.raids_between(guild_id, now - 7 * 86400, now)
    guild = getattr(channel, "guild", None)
    lines = [f"📊 **Weekly attendance** (last 7 days): {raids} raid{'s' if raids != 1 else ''}"
             f" · {sum(r['attended'] for r in rows)} attended · {sum(r['no_show'] for r in rows)} no-shows"]
    flakes = sorted((r for r in rows if r["no_show"]), key=lambda r: (-r["no_show"], r["user_id"]))[:5]
    if flakes:
        lines.append("Most no-shows: " + ", ".join(f"{_name(guild, r['user_id'])} ({r['no_show']})" for r in flakes))
    solid = [r for r in rows if r["attended"] and not r["no_show"]][:3]
    if solid:
        lines.append("Most attended, no no-shows: " + ", ".join(f"{_name(guild, r['user_id'])} ({r['attended']})" for r in solid))
    if not rows:
        lines.append("No signups on raids that started this week.")
    try:
        await channel.send("\n".join(lines)[:2000], allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as err:
        log.warning("Weekly digest failed for guild %s: %s", guild_id, err)
        return False
    return True


async def _run_digest(now: int) -> None:
    for g in db.guilds_with_setting("digest", "1"):
        local = datetime.fromtimestamp(now, guild_tz(g))
        week = f"{local.isocalendar()[0]}-W{local.isocalendar()[1]:02d}"
        if db.setting(g, "digest_last") == week or (local.weekday() == 0 and local.hour < 9):
            continue  # already posted this week, or it is Monday before 09:00
        if await _attempt("digest", g, partial(_digest_one, g, now)):
            db.set_setting(g, "digest_last", week)


BACKUP_POST_HOUR = 4  # the daily backup post goes out at the first check after 04:00 server time
BACKUP_POSTS_KEPT = 7  # older backup posts are deleted, like the backups folder
BACKUP_POST_MAX = 8 * 1024 * 1024  # Discord's upload limit for bots, with room to spare


def backup_channel_problem(channel) -> Optional[str]:
    """Why this channel can't take the daily backup post, or None if it can. Everyone in the server being able
    to see it is a problem: the file holds members' Discord IDs."""
    guild = channel.guild
    if channel.permissions_for(guild.default_role).view_channel:
        return (f"{channel.mention} can be seen by everyone in the server. The backup holds your members' Discord IDs, "
                "so pick a channel only admins can see (turn off View Channel for @everyone).")
    mine = channel.permissions_for(guild.me)
    missing = [name for name, ok in (("View Channel", mine.view_channel), ("Send Messages", mine.send_messages),
                                     ("Attach Files", mine.attach_files)) if not ok]
    if missing:
        return f"I need {', '.join(missing)} in {channel.mention} to post the backup there."
    return None


async def _backup_post_one(guild_id: int, day: str):
    """The optional daily backup post (/config backupchannel): a copy of this server's data, as a file."""
    channel = bot.get_channel(int_setting(guild_id, "backup_channel"))
    if channel is None:
        return True  # channel deleted or out of reach: skip today, /status and the folder backups still work
    problem = backup_channel_problem(channel)
    if problem:  # e.g. the channel was made public after it was chosen: never post the file then
        log.warning("Daily backup post skipped for guild %s: %s", guild_id, problem)
        note_error(f"daily backup post skipped: {problem}")
        return True
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"raidbot-{day}.db"
        db.snapshot_for_guild(path, guild_id)
        size = path.stat().st_size
        try:
            if size > BACKUP_POST_MAX:
                msg = await channel.send(f"🗄️ Daily backup for {day}: the database is {size // 1024 // 1024} MB, too big "
                                         "to attach. The bot's `backups` folder still has a copy.")
            else:
                msg = await channel.send(
                    f"🗄️ Daily backup for {day}: this server's raids, signups, characters and settings. It holds members' "
                    "Discord IDs, so keep this channel private. To restore, stop the bot and put this file in place of "
                    "`raidbot.db` (it only holds this server's data, so if the bot runs in other servers too, use the "
                    "`backups` folder instead).",
                    file=discord.File(io.BytesIO(path.read_bytes()), filename=path.name))
        except discord.HTTPException as err:
            log.warning("Daily backup post failed for guild %s: %s", guild_id, err)
            return False
    posts = [p for p in (db.setting(guild_id, "backup_posts") or "").split(",") if p] + [f"{channel.id}:{msg.id}"]
    for old in posts[:-BACKUP_POSTS_KEPT]:  # keep the newest few, like the backups folder
        cid, mid = (int(x) for x in old.split(":"))
        old_channel = bot.get_channel(cid)
        try:
            if old_channel is not None:
                await old_channel.get_partial_message(mid).delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as err:
            log.warning("Couldn't delete an old backup post in guild %s: %s", guild_id, err)
    db.set_setting(guild_id, "backup_posts", ",".join(posts[-BACKUP_POSTS_KEPT:]))
    return True


async def _run_backuppost(now: int) -> None:
    for g in db.guilds_with_key("backup_channel"):
        local = datetime.fromtimestamp(now, guild_tz(g))
        day = local.date().isoformat()
        last = db.setting(g, "backup_post_last")
        if last == day or (last and local.hour < BACKUP_POST_HOUR):
            continue  # already posted today, or before 04:00 (the very first copy goes out straight away)
        if await _attempt("backuppost", g, partial(_backup_post_one, g, day)):
            db.set_setting(g, "backup_post_last", day)


# ---------------------------------------------------------------- the Upcoming raids board (/config board)
BOARD_MAX = 15  # raids listed on the board; the rest are counted
BOARD_DELAY = 3  # seconds to wait after a change, so a rush of signups costs one edit of the board
_board_busy: dict[int, bool] = {}  # guild id -> "changed again while the board was being drawn"
_board_drawn: dict[int, tuple] = {}  # guild id -> (message id, text) last shown, so an unchanged board isn't edited
_board_soon: set[int] = set()  # guilds with a redraw already waiting
_board_tasks: set = set()  # keeps the waiting redraws alive until they run


def board_can_show(board_channel, raid_channel_id: int) -> bool:
    """A raid goes on the board only if everyone who can read the board can also see the raid's channel (checked
    role by role), so a raid in an officers-only or team-only channel never shows up on a public board. The
    low-signup alert uses the same rule for its channel."""
    if raid_channel_id == board_channel.id:
        return True
    raid_channel = bot.get_channel(raid_channel_id)
    if raid_channel is None:
        return False
    roles = getattr(board_channel.guild, "roles", None) or [board_channel.guild.default_role]
    return not any(board_channel.permissions_for(r).view_channel and not raid_channel.permissions_for(r).view_channel
                   for r in roles)


def board_raids(guild_id: int, board_channel, now: int) -> tuple[list, int]:
    """(raids to list, how many upcoming raids were left off because some board readers can't see their channel)."""
    rows = [e for e in db.upcoming_events(guild_id, now) if e["message_id"]]
    shown = [e for e in rows if board_can_show(board_channel, e["channel_id"])]
    return shown, len(rows) - len(shown)


def board_embed(guild_id: int, board_channel, now: int):
    raids, _ = board_raids(guild_id, board_channel, now)
    if raids:
        text = "\n\n".join(raid_line(e, show_id=False) for e in raids[:BOARD_MAX])
        if len(raids) > BOARD_MAX:
            text += f"\n\n…and {plural(len(raids) - BOARD_MAX, 'more raid')} after these."
    else:
        text = "No raids planned right now. New raids show up here as soon as they're posted."
    embed = discord.Embed(title="📅 Upcoming raids", description=text[:4000], colour=discord.Colour.blurple())
    embed.set_footer(text="Updates by itself. Click a raid's name to go to its post and sign up.")
    return embed


def schedule_board(guild_id: int) -> None:
    """Redraw the board a few seconds from now (once, however many changes come in meanwhile). Does nothing when
    the board is off."""
    if not guild_id or guild_id in _board_soon or not db.setting(guild_id, "board_channel"):
        return

    async def later():
        try:
            await asyncio.sleep(BOARD_DELAY)
        finally:
            _board_soon.discard(guild_id)
        await draw_board(guild_id)

    _board_soon.add(guild_id)
    task = asyncio.get_running_loop().create_task(later())
    _board_tasks.add(task)
    task.add_done_callback(_board_tasks.discard)


async def draw_board(guild_id: int) -> bool:
    """Bring the board up to date. Like refresh_message, a change during a draw makes that draw run once more
    instead of starting a second one (which could post two boards). False means try again later."""
    if guild_id in _board_busy:
        _board_busy[guild_id] = True
        return True
    try:
        while True:
            _board_busy[guild_id] = False
            ok = await _draw_board(guild_id)
            if not _board_busy[guild_id]:
                return ok
    finally:
        _board_busy.pop(guild_id, None)


async def _draw_board(guild_id: int) -> bool:
    channel = bot.get_channel(int_setting(guild_id, "board_channel"))
    if channel is None:
        return True  # board off, or its channel was deleted: nothing to draw
    embed = board_embed(guild_id, channel, int(time.time()))
    message_id = int_setting(guild_id, "board_message")
    if message_id:
        if _board_drawn.get(guild_id) == (message_id, embed.description):
            return True
        try:
            await channel.get_partial_message(message_id).edit(content=None, embed=embed)
            _board_drawn[guild_id] = (message_id, embed.description)
            return True
        except discord.NotFound:
            pass  # deleted by hand: post a new one below
        except discord.HTTPException as e:
            log.warning("Could not edit the raid board in guild %s: %s", guild_id, e)
            note_error(f"could not update the Upcoming raids board: {e}")
            return False
    try:
        msg = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as e:
        log.warning("Could not post the raid board in guild %s: %s", guild_id, e)
        note_error(f"could not post the Upcoming raids board: {e}")
        return False
    db.set_setting(guild_id, "board_message", msg.id)
    _board_drawn[guild_id] = (msg.id, embed.description)
    await pin_board(msg)
    return True


async def pin_board(msg) -> bool:
    try:
        await msg.pin(reason="Upcoming raids board (/config board)")
        return True
    except discord.HTTPException:
        return False


async def remove_board(guild_id: int) -> None:
    """Delete the current board message, if there is one (ignoring one that's already gone)."""
    channel = bot.get_channel(int_setting(guild_id, "board_channel"))
    message_id = int_setting(guild_id, "board_message")
    db.set_setting(guild_id, "board_message", None)
    _board_drawn.pop(guild_id, None)
    if channel is not None and message_id:
        try:
            await channel.get_partial_message(message_id).delete()
        except discord.HTTPException:
            pass


async def _run_board(now: int) -> None:
    for g in db.guilds_with_key("board_channel"):  # raids that started drop off; an unchanged board isn't touched
        await _attempt("board", g, partial(draw_board, g))


# ---------------------------------------------------------------- low-signup alert (/alerts lowsignup)
def lowsignup_inside(guild_id: int, start_ts: int) -> bool:
    """True if a raid starting at start_ts is already inside the /config lowsignup window."""
    hours = int_setting(guild_id, "lowsignup_hours")
    return bool(hours and int_setting(guild_id, "lowsignup_channel") and start_ts - hours * 3600 <= time.time())


def lowsignup_short(event, minimum: int) -> list[str]:
    """What the raid is short of: open slots in each role that has a limit, and people below `minimum` (0 = no
    minimum). Empty means the raid looks fine."""
    attending = [x for x in db.signups(event["id"]) if x["status"] in ("in", "late")]
    short = []
    if minimum and len(attending) < minimum:
        short.append(f"{len(attending)}/{minimum} people in")
    names = {"tank": "tank", "healer": "healer", "dps": "DPS"}
    for role in wd.ROLES:
        cap = event[f"cap_{role}"]
        have = sum(1 for x in attending if x["role"] == role)
        if cap and have < cap:
            short.append(f"{cap - have} {names.get(role, role)} slot{'s' if cap - have != 1 else ''} open ({have}/{cap})")
    return short


def lowsignup_ping_role(event, raid_channel):
    """The officer role to ping for this raid: its channel's (or category's) raid leaders, else the server's."""
    ids = scoped_role_ids(event["guild_id"], raid_channel, "officer_role") if raid_channel else []
    role_id = ids[0] if ids else int_setting(event["guild_id"], "officer_role")
    guild = getattr(raid_channel, "guild", None)
    return guild.get_role(role_id) if guild is not None and role_id else None


async def _lowsignup_one(event, now: int):
    g = event["guild_id"]
    hours = int_setting(g, "lowsignup_hours")
    channel = bot.get_channel(int_setting(g, "lowsignup_channel"))
    if not hours or channel is None:
        return WAIT  # off (or its channel is gone): the raid stays unchecked, in case it's turned on later
    if event["start_ts"] - hours * 3600 > now:
        return WAIT
    short = lowsignup_short(event, int_setting(g, "lowsignup_min"))
    if not short:
        return True  # enough people: the check is done for this raid
    if not board_can_show(channel, event["channel_id"]):
        log.info("Low-signup alert for raid %s skipped: some readers of the alert channel can't see the raid", event["id"])
        return True  # never tell a wider audience about a raid in a more private channel
    link = f"https://discord.com/channels/{g}/{event['channel_id']}/{event['message_id']}"
    text = (f"⚠️ **Low signups:** [**{event['title']}**]({link}) in <#{event['channel_id']}> starts "
            f"<t:{event['start_ts']}:R> and is short: " + "; ".join(short) + ".")
    role = lowsignup_ping_role(event, bot.get_channel(event["channel_id"])) if db.setting(g, "lowsignup_ping") == "1" else None
    if role is not None:
        text = f"{role.mention} {text}"
    try:
        await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[role])
                           if role is not None
                           else discord.AllowedMentions.none())
    except discord.HTTPException as e:
        log.warning("Low-signup alert failed for event %s: %s", event["id"], e)
        note_error(f"low-signup alert for raid #{event['id']}: {e}")
        return False
    return True


async def _run_lowsignup(now: int) -> None:
    on = set(db.guilds_with_key("lowsignup_channel"))
    for event in (e for e in db.pending_lowsignup(now) if e["guild_id"] in on):  # optional: tell officers a raid is short (/alerts lowsignup)
        if await _attempt("lowsignup", event["id"], partial(_lowsignup_one, event, now)):
            db.update_event(event["id"], low_alerted=1)


# ---------------------------------------------------------------- Discord Scheduled Events (/alerts events)
SCHED_LENGTH_DEFAULT = 3  # hours a raid's Scheduled Event lasts unless /alerts events length says otherwise
SCHED_KEEP_CHECKING = 2 * 86400  # how long after the start a raid's event is still looked after (to end it)
SCHED_MAX_FAILS = 5
_sched_fails: dict[int, int] = {}
_sched_tasks: set = set()


def sched_events_on(guild_id: int) -> bool:
    return db.setting(guild_id, "sched_events") == "1"


def sched_length(guild_id: int) -> int:
    return int_setting(guild_id, "sched_events_hours", SCHED_LENGTH_DEFAULT) or SCHED_LENGTH_DEFAULT


def sched_can_show(guild, raid_channel_id: int) -> bool:
    """Every member of the server can see its Scheduled Events, so a raid gets one only if every role (@everyone
    included) can see the raid's channel. A raid in an officers-only or team-only channel never gets one."""
    channel = bot.get_channel(raid_channel_id) if guild is not None else None
    if channel is None:
        return False
    roles = getattr(guild, "roles", None) or [guild.default_role]
    return all(channel.permissions_for(r).view_channel for r in roles)


def sched_can_manage(guild) -> bool:
    perms = getattr(getattr(guild, "me", None), "guild_permissions", None)
    return bool(perms and (perms.manage_events or perms.administrator))


def sched_fields(event) -> dict:
    """What the raid's Scheduled Event should say: its title, times, a link to the post as the location, and the
    raid's description."""
    g, start = event["guild_id"], datetime.fromtimestamp(event["start_ts"], timezone.utc)
    link = f"https://discord.com/channels/{g}/{event['channel_id']}/{event['message_id']}"
    channel = bot.get_channel(event["channel_id"])
    where = f"#{channel.name}" if channel is not None else "the raid channel"
    text = (event["description"] or "").strip()
    text = (text[:850] + "\n\n" if text else "") + f"Sign up on the raid post in {where}: {link}"
    return {"name": event["title"][:100], "start_time": start, "end_time": start + timedelta(hours=sched_length(g)),
            "location": link[:100], "description": text[:1000]}


async def _sched_get(guild, sid: int):
    """The Scheduled Event with this id, or None if it was deleted (by hand, or by Discord after it ended)."""
    found = guild.get_scheduled_event(sid)
    if found is not None:
        return found
    try:
        return await guild.fetch_scheduled_event(sid)
    except discord.NotFound:
        return None


async def remove_sched_event(guild, sid: int) -> bool:
    """Delete one Scheduled Event, ignoring one that's already gone. False means it could not be deleted."""
    if guild is None or not sid or sid < 0:
        return True
    target = guild.get_scheduled_event(sid)
    try:
        if target is None:
            target = await guild.fetch_scheduled_event(sid)
        await target.delete(reason="Raid cancelled or no longer shown (/alerts events)")
    except discord.NotFound:
        return True
    except discord.HTTPException as e:
        log.warning("Could not delete Scheduled Event %s in guild %s: %s", sid, guild.id, e)
        return False
    return True


async def sync_sched_event(event_id: int, now: Optional[int] = None):
    """Bring one raid's Discord Scheduled Event in line with the raid: create it, update its title, time or
    description, start it when the raid starts and end it when the raid is over, or delete it when it shouldn't
    exist (switched off, private channel, raid gone). True when done, False to try again, WAIT without Manage Events."""
    now = int(time.time()) if now is None else now
    event = db.event(event_id)
    if not event:
        return True  # cancelled: delete_event_post took its Scheduled Event with it
    g, sid = event["guild_id"], event["sched_event_id"]
    if sid < 0:
        return True  # ended, or deleted by hand: hands off
    guild = bot.get_guild(g)
    if guild is None:
        return WAIT
    if not sched_can_manage(guild):
        return WAIT  # without Manage Events nothing can be done; it starts working once the permission is given
    end_ts = event["start_ts"] + sched_length(g) * 3600
    wanted = sched_events_on(g) and event["message_id"] and sched_can_show(guild, event["channel_id"])
    try:
        if not wanted:
            if sid and not await remove_sched_event(guild, sid):
                return _sched_failed(event_id)
            if sid:
                db.update_event(event_id, sched_event_id=0)
            return True
        if not sid:
            if event["start_ts"] <= now + 60:
                return True  # already started (or about to): too late for a Scheduled Event
            made = await guild.create_scheduled_event(entity_type=discord.EntityType.external,
                                                      privacy_level=discord.PrivacyLevel.guild_only,
                                                      reason=f"Raid #{event_id} (/alerts events)",
                                                      **sched_fields(event))
            db.update_event(event_id, sched_event_id=made.id)
            return _sched_ok(event_id)
        se = await _sched_get(guild, sid)
        if se is None or se.status in (discord.EventStatus.completed, discord.EventStatus.cancelled):
            db.update_event(event_id, sched_event_id=-1)  # deleted by hand, or over: never recreate it
            return _sched_ok(event_id)
        want = sched_fields(event)
        if se.status == discord.EventStatus.scheduled:
            if now >= end_ts:
                await se.cancel(reason="The raid is over")
                db.update_event(event_id, sched_event_id=-1)
            elif event["start_ts"] <= now:
                await se.start(reason="The raid is starting")
            else:
                changed = {k: v for k, v in want.items() if (getattr(se, k, None) or "") != v}
                if changed:
                    await se.edit(**changed, reason="Raid edited")
        elif se.status == discord.EventStatus.active:
            if now >= end_ts:
                await se.end(reason="The raid is over")
                db.update_event(event_id, sched_event_id=-1)
            else:
                changed = {k: want[k] for k in ("name", "description") if (getattr(se, k, None) or "") != want[k]}
                if changed:
                    await se.edit(**changed, reason="Raid edited")
    except discord.HTTPException as e:
        log.warning("Scheduled Event for raid %s: %s", event_id, e)
        note_error(f"Discord Scheduled Event for raid #{event_id}: {e}")
        return _sched_failed(event_id)
    return _sched_ok(event_id)


def _sched_ok(event_id: int) -> bool:
    _sched_fails.pop(event_id, None)
    return True


def _sched_failed(event_id: int) -> bool:
    """False (try again next minute), until a raid has failed SCHED_MAX_FAILS times in a row: then its Scheduled
    Event is left alone for good, so a stuck one doesn't log an error every minute."""
    _sched_fails[event_id] = _sched_fails.get(event_id, 0) + 1
    if _sched_fails[event_id] < SCHED_MAX_FAILS:
        return False
    _sched_fails.pop(event_id, None)
    log.error("Giving up on the Scheduled Event for raid #%s", event_id)
    db.update_event(event_id, sched_event_id=-1)
    return True


def sched_soon(event_id: int) -> None:
    """Sync a raid's Scheduled Event in the background now, instead of waiting for the next minute. Does nothing
    when the feature is off and the raid has no Scheduled Event."""
    event = db.event(event_id)
    if not event or not (sched_events_on(event["guild_id"]) or event["sched_event_id"] > 0):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_attempt("scheduled event", event_id, partial(sync_sched_event, event_id)))
    _sched_tasks.add(task)
    task.add_done_callback(_sched_tasks.discard)


async def _run_sched(now: int) -> None:
    on = set(db.guilds_with_key("sched_events"))
    for event in db.sched_candidates(now, now - SCHED_KEEP_CHECKING):
        if event["sched_event_id"] > 0 or event["guild_id"] in on:
            await _attempt("scheduled event", event["id"], partial(sync_sched_event, event["id"], now))


async def _run_locks(now: int) -> None:
    for event in db.due_lock(now):  # start time passed: show "This event is closed."
        if await _attempt("lock", event["id"], partial(refresh_message, event["id"])):
            db.mark_lock_shown(event["id"])


async def _run_repeats(now: int) -> None:
    for event in db.due_repeat(now):  # recurring raid started: post the next one (once)
        if await _attempt("repeat", event["id"], partial(spawn_next, event)):
            db.update_event(event["id"], repeated=1)


async def _run_archive(now: int) -> None:
    for event in db.archivable(now - 86400):  # optional cleanup of old posts (/config archive)
        if await _attempt("cleanup", event["id"], partial(_archive_one, event, now)):
            db.update_event(event["id"], archived=1)


async def _run_reminders(now: int) -> None:
    for event in db.pending_reminders(now):
        if await _attempt("reminder", event["id"], partial(_remind_one, event, now)):
            db.mark_reminded(event["id"])


async def _run_nudges(now: int) -> None:
    for event in db.pending_nudges(now):  # optional "who hasn't answered" ping (/config nudge)
        if await _attempt("nudge", event["id"], partial(_nudge_one, event, now)):
            db.update_event(event["id"], nudged=1)


@tasks.loop(minutes=1)
async def reminder_loop():
    now = int(time.time())
    for step in (_run_autoclose, _run_locks, _run_voicecheck, _run_repeats, _run_archive, _run_reminders, _run_nudges,
                 _run_lowsignup, _run_sched, _run_digest, _run_backuppost, _run_board, _run_updatecheck):
        try:
            await step(now)
        except Exception as e:  # e.g. a database error: log it, and still run the other steps
            log.exception("Scheduler step %s failed", step.__name__)
            note_error(f"{step.__name__.removeprefix('_run_')}: {e!r}")


def _revive_scheduler() -> None:
    if not reminder_loop.is_running():
        log.warning("Restarting the scheduler")
        reminder_loop.start()


@reminder_loop.error
async def _scheduler_crashed(exc: BaseException):
    """discord.py stops a loop for good on an unexpected error, which would silently end locking,
    reminders and recurring raids. Log it and start the loop again."""
    log.error("The scheduler stopped unexpectedly: %r", exc, exc_info=exc)
    note_error(f"scheduler stopped: {exc!r}")
    asyncio.get_running_loop().call_later(5, _revive_scheduler)


@tasks.loop(hours=6)
async def backup_loop():
    """One copy of raidbot.db per day in the 'backups' folder; the newest 7 are kept."""
    try:
        made = db.backup_daily()
        if made:
            log.info("Backed up the database to %s", made.name)
    except Exception as e:  # a failed backup must never stop the bot
        log.warning("Database backup failed: %s", e)
    try:
        db.purge_cancelled()  # cancelled raids can be restored for 14 days, then they're gone for good
    except Exception as e:
        log.warning("Clearing old cancelled raids failed: %s", e)


@reminder_loop.before_loop
async def _wait_ready():
    await bot.wait_until_ready()


# ---------------------------------------------------------------- attendance
@raid.command(name="noshow", description="Mark someone who signed up but didn't turn up (counts in /stats)")
@app_commands.describe(member="Who didn't show", undo="Take the mark back off")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_noshow(interaction: discord.Interaction, event_id: int, member: discord.Member, undo: bool = False):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    row = db.signup(event_id, member.id)
    if not row or row["status"] not in ("in", "late"):
        return await interaction.response.send_message(
            f"{member.display_name} wasn't signed up as attending, so there's nothing to mark.", ephemeral=True)
    db.set_no_show(event_id, member.id, not undo)
    await audit(interaction.guild_id, f"{'↩️' if undo else '🚷'} {interaction.user.mention} "
                                      f"{'cleared the no-show for' if undo else 'marked a no-show:'} {member.mention} in **{event['title']}** (#{event_id})")
    await interaction.response.send_message(
        f"{'Cleared the no-show mark for' if undo else 'Marked'} {member.mention} "
        f"{'' if undo else 'as a no-show '}for **{event['title']}**.", ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none())


stats = app_commands.Group(name="stats", description="Raid attendance", guild_only=True)


def _stats_text(r) -> str:
    done = r["attended"] + r["no_show"]
    rate = f" · **{round(100 * r['attended'] / done)}%** showed up" if done else ""
    return (f"{r['attended']} attended ({r['late']} late) · {r['no_show']} no-show · "
            f"{r['absent']} declined · {r['total']} signups{rate}")


@stats.command(name="me", description="Your own attendance")
@app_commands.describe(days="How far back to look (default 90)")
async def stats_me(interaction: discord.Interaction, days: app_commands.Range[int, 7, 730] = 90):
    now = int(time.time())
    rows = db.stats(interaction.guild_id, now - days * 86400, now, interaction.user.id)
    await interaction.response.send_message(
        f"**Your last {days} days:** " + (_stats_text(rows[0]) if rows else "no finished raids yet."),
        ephemeral=True)


@stats.command(name="team", description="Everyone's attendance (officers only)")
@app_commands.describe(days="How far back to look (default 90)")
async def stats_team(interaction: discord.Interaction, days: app_commands.Range[int, 7, 730] = 90):
    if not (is_officer(interaction) or holds_scoped_officer_role(interaction)):
        return await interaction.response.send_message("Only raid officers can see everyone's attendance. "
                                                       "Use `/stats me` for your own.", ephemeral=True)
    now = int(time.time())
    rows = db.stats(interaction.guild_id, now - days * 86400, now)
    if not rows:
        return await interaction.response.send_message("No finished raids with signups in that period.", ephemeral=True)
    def who(uid: int) -> str:  # a name always renders; a mention shows "unknown-user" if the viewer's client hasn't loaded them
        member = interaction.guild.get_member(uid)
        return f"**{member.display_name}**" if member else f"<@{uid}>"

    lines = [f"{who(r['user_id'])} {_stats_text(r)}" for r in rows]
    text = f"**Attendance, last {days} days** (most attended first)\n" + "\n".join(lines)
    if len(text) > 1990:
        text = text[:1950].rsplit("\n", 1)[0] + f"\n…and {len(rows) - text[:1950].count(chr(10))} more"
    await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def csv_safe(text: str) -> str:
    """Stop a spreadsheet from treating a name that starts with = + - @ as a formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@stats.command(name="export", description="Attendance as a spreadsheet file (officers only)")
@app_commands.describe(days="How far back to look (default 90)")
async def stats_export(interaction: discord.Interaction, days: app_commands.Range[int, 7, 730] = 90):
    if not (is_officer(interaction) or holds_scoped_officer_role(interaction)):
        return await interaction.response.send_message("Only raid officers can export attendance.", ephemeral=True)
    now = int(time.time())
    rows = db.stats(interaction.guild_id, now - days * 86400, now)
    if not rows:
        return await interaction.response.send_message("No finished raids with signups in that period.", ephemeral=True)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["discord_id", "name", "signups", "attended", "late", "no_show", "declined", "showed_up_percent"])
    for r in rows:
        member = interaction.guild.get_member(r["user_id"])
        done = r["attended"] + r["no_show"]
        writer.writerow([r["user_id"], csv_safe(member.display_name) if member else "", r["total"], r["attended"],
                         r["late"], r["no_show"], r["absent"], round(100 * r["attended"] / done) if done else ""])
    data = ("\ufeff" + out.getvalue()).encode("utf-8")  # the BOM makes Excel read accented names correctly
    await interaction.response.send_message(
        f"Attendance for the last {days} days, {len(rows)} people. It has Discord ids and names, so keep it private.",
        file=discord.File(io.BytesIO(data), filename=f"attendance-{days}d.csv"), ephemeral=True)


@bot.tree.command(name="myraids", description="The upcoming raids you've answered, with links")
@app_commands.guild_only()
async def myraids(interaction: discord.Interaction):
    rows = db.user_upcoming(interaction.guild_id, interaction.user.id, int(time.time()))
    if not rows:
        return await interaction.response.send_message("You haven't answered any upcoming raids.", ephemeral=True)
    label = {"in": "✅ in", "late": "⏰ late", "tentative": "❔ tentative", "bench": "🪑 bench", "absent": "❌ absent"}
    lines = [f"{label.get(r['my_status'], r['my_status'])} · <t:{r['start_ts']}:f> · "
             f"[{r['title']}](https://discord.com/channels/{r['guild_id']}/{r['channel_id']}/{r['message_id']})"
             + (f" · *{r['my_note']}*" if r["my_note"] and notes_mode(interaction.guild_id) != "off" else "")
             for r in rows[:15]]
    await interaction.response.send_message("\n".join(lines), ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


async def my_signup_autocomplete(interaction: discord.Interaction, current: str):
    """Upcoming raids this player has answered, as 'Title · Nov 14 20:00 (#id)'."""
    tz = guild_tz(interaction.guild_id)
    out = []
    for e in db.user_upcoming(interaction.guild_id, interaction.user.id, int(time.time()) - NOTE_GRACE):
        label = f"{e['title']} · {datetime.fromtimestamp(e['start_ts'], tz).strftime('%b %d %H:%M')} (#{e['id']})"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=e["id"]))
    return out[:25]


NOTE_GRACE = 6 * 3600  # a note can still be changed this long after the raid started


@bot.tree.command(name="note", description="Add a short note to your signup, e.g. 'late 15 min' (leave it out to clear it)")
@app_commands.describe(raid="A raid you've signed up for",
                       text=f"Up to {NOTE_MAX} characters, e.g. late 15 min. Leave it out to clear your note")
@app_commands.autocomplete(raid=my_signup_autocomplete)
@app_commands.guild_only()
async def note(interaction: discord.Interaction, raid: int, text: Optional[str] = None):
    g, uid = interaction.guild_id, interaction.user.id
    mode = notes_mode(g)
    if mode == "off":
        return await interaction.response.send_message(
            "Signup notes are off on this server. An admin can turn them on with `/config notes`.", ephemeral=True)
    event = db.event(raid)
    if not event or event["guild_id"] != g:
        return await interaction.response.send_message("Raid not found. Pick one from the list.", ephemeral=True)
    if not db.signup(raid, uid):
        return await interaction.response.send_message(
            f"You aren't signed up for **{event['title']}**. Sign up on the post first, then add a note.", ephemeral=True)
    if event["archived"] or event["start_ts"] < time.time() - NOTE_GRACE:
        return await interaction.response.send_message(f"**{event['title']}** is over, so its notes can't change.",
                                                       ephemeral=True)
    new = clean_note(text)
    if text and text.strip() and not new:
        return await interaction.response.send_message(
            "That note is empty once formatting, mentions and links are taken out. Try plain words, e.g. `late 15 min`.",
            ephemeral=True)
    db.set_note(raid, uid, new)
    seen = "on the raid post" if mode == "post" else "to the raid officers"
    cut = f" (cut to {NOTE_MAX} characters)" if text and len(" ".join(text.split())) > NOTE_MAX else ""
    await interaction.response.send_message(
        f"Your note on **{event['title']}**: *{new}*{cut}. It's shown {seen}, and stays if you change your status."
        if new else f"Cleared your note on **{event['title']}**.", ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none())
    if mode == "post" and event["message_id"]:
        await refresh_message(raid)


@bot.tree.command(name="remindme", description="Get raid reminders as a private message instead of a ping in the channel")
@app_commands.describe(on="True: message me. False: ping me in the channel like everyone else")
@app_commands.guild_only()
async def remindme(interaction: discord.Interaction, on: bool):
    db.set_setting(interaction.guild_id, f"dm_remind@{interaction.user.id}", "1" if on else None)
    await interaction.response.send_message(
        "Done. Before each raid you're signed up for I'll message you privately instead of pinging you in the channel. "
        "(If your private messages from this server are closed I'll ping you in the channel as usual.)" if on else
        "Done. You'll be pinged in the channel before raids, like everyone else.", ephemeral=True)


def _forget_summary(counts: dict) -> str:
    parts = [plural(counts["characters"], "character"), plural(counts["signups"], "raid signup"),
             plural(counts["series"], "repeating-raid sign-up")]
    if counts["settings"]:
        parts.append("your /remindme setting")
    if counts["cancelled"]:
        parts.append(f"your place in {plural(counts['cancelled'], 'cancelled raid')}")
    return ", ".join(parts)


@bot.tree.command(name="forgetme", description="Delete your characters and your signup history from the bot (asks first)")
async def forgetme(interaction: discord.Interaction):
    counts = db.user_data_counts(interaction.user.id)
    if not any(counts.values()):
        return await interaction.response.send_message("The bot has nothing stored about you.", ephemeral=True)

    async def do_forget(click: discord.Interaction, reply):
        counts, event_ids = db.forget_user(click.user.id)
        await reply(f"Done. Deleted {_forget_summary(counts)}. The bot no longer has anything stored about you.")
        for event_id in event_ids:  # take their name off the posts, and move someone off the bench if a slot opened
            event = db.event(event_id)
            if event and event["message_id"] and not event["archived"]:
                await update_event(event_id)

    # Always asks, whatever /config confirm says: the player can't get this back.
    await interaction.response.send_message(
        f"Delete everything the bot stores about you, in every server it's in? That's {_forget_summary(counts)}. "
        "Your attendance history goes with your signups and your name comes off the raid posts. "
        "This can't be undone. (Raids you created stay. The bot's daily backups still hold the old data "
        "until they're replaced, within 7 days.)",
        view=ConfirmView(interaction, "Yes, delete my data", do_forget), ephemeral=True)


always = app_commands.Group(name="always", description="Stay signed up to a repeating raid", guild_only=True)


def repeat_label(days: int) -> str:
    return {7: "every week", 14: "every 2 weeks"}.get(days, f"every {days} days")


async def recurring_autocomplete(interaction: discord.Interaction, current: str):
    """Upcoming repeating raids in this server, as '#channel · Title · Nov 14 20:00 (#id)'."""
    tz = guild_tz(interaction.guild_id)
    out = []
    for e in db.upcoming_recurring(interaction.guild_id, int(time.time())):
        ch = bot.get_channel(e["channel_id"])
        when = datetime.fromtimestamp(e["start_ts"], tz).strftime("%b %d %H:%M")
        label = f"#{ch.name if ch else 'deleted-channel'} · {e['title']} · {when} ({repeat_label(e['repeat_days'])})"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=e["id"]))
    return out[:25]


async def my_series_autocomplete(interaction: discord.Interaction, current: str):
    out = []
    for m in db.user_series(interaction.guild_id, interaction.user.id):
        latest = db.latest_in_series(m["series_id"])
        label = f"{latest['title'] if latest else 'a raid that no longer exists'} ({m['char_name']})"
        if current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=m["series_id"]))
    return out[:25]


@always.command(name="join", description="Sign up to a repeating raid now, and to every future one automatically")
@app_commands.describe(event_id="A repeating raid", character="Which character (default: your last one, else your main)")
@app_commands.autocomplete(event_id=recurring_autocomplete, character=own_char_autocomplete)
async def always_join(interaction: discord.Interaction, event_id: int, character: Optional[str] = None):
    event = db.event(event_id)
    if not event or event["guild_id"] != interaction.guild_id or not event["repeat_days"]:
        return await interaction.response.send_message(
            "That isn't a repeating raid. Pick one from the list.", ephemeral=True)
    if not may_sign_up(interaction):
        return await interaction.response.send_message(
            f"Sign-ups here are for members with <@&{int_setting(interaction.guild_id, 'raider_role')}>.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    uid = interaction.user.id
    existing = db.signup(event_id, uid)
    if character:
        c = db.character_by_name(uid, character)
        who = {"name": c["name"], "wow_class": c["wow_class"], "spec": c["spec"], "role": c["role"]} if c else None
        if who is None:
            return await interaction.response.send_message("You don't have a saved character with that name.", ephemeral=True)
    elif existing and existing["char_name"]:
        who = {"name": existing["char_name"], "wow_class": existing["wow_class"], "spec": existing["spec"],
               "role": existing["role"]}
    else:
        who = quick_character(uid)
    if who is None:
        return await interaction.response.send_message(
            "Save a character first with `/character add`, then try again.", ephemeral=True)
    series = event["series_id"] or event["id"]
    db.join_series(interaction.guild_id, series, uid, who["name"], who["wow_class"], who["spec"], who["role"])
    extra = ""
    if existing is None and not is_locked(event):
        status, bench_from = apply_cap(event, uid, who["role"], "in", who["wow_class"])
        db.set_signup(event_id, uid, who["name"], who["wow_class"], who["spec"], who["role"], status, bench_from)
        extra = f" You're {status} for **{event['title']}** now."
        await update_event(event_id)
    await interaction.response.send_message(
        f"You'll be signed up as **{who['name']}** ({who['spec']} {who['wow_class']}) for **{event['title']}** "
        f"{repeat_label(event['repeat_days'])}, automatically.{extra} You can still press **Absent** on any date, "
        "and stop with `/always leave`.", ephemeral=True)


@always.command(name="leave", description="Stop being signed up automatically to a repeating raid")
@app_commands.describe(series="The repeating raid to leave")
@app_commands.autocomplete(series=my_series_autocomplete)
async def always_leave(interaction: discord.Interaction, series: int):
    ok = db.leave_series(series, interaction.user.id)
    await interaction.response.send_message(
        "Done. You won't be signed up automatically any more (dates you're already on stay as they are)." if ok else
        "You weren't signed up automatically to that one.", ephemeral=True)


@always.command(name="list", description="The repeating raids you're signed up to automatically")
async def always_list(interaction: discord.Interaction):
    rows = db.user_series(interaction.guild_id, interaction.user.id)
    if not rows:
        return await interaction.response.send_message(
            "You aren't signed up automatically to anything. Use `/always join` on a repeating raid.", ephemeral=True)
    lines = []
    for m in rows:
        latest = db.latest_in_series(m["series_id"])
        title = f"**{latest['title']}** ({repeat_label(latest['repeat_days'])})" if latest and latest["repeat_days"] else (
            f"**{latest['title']}**" if latest else "a raid that no longer exists")
        lines.append(f"{title} as **{m['char_name']}** ({m['spec']} {m['wow_class']})")
    await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True)


@bot.tree.command(name="status", description="Bot health: version, uptime, permissions and backups")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def status(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message(
            "Only server admins (Manage Server) can use `/status`.", ephemeral=True)
    up = int(time.time() - STARTED_AT)
    lines = [f"**Raid Bot v{VERSION}** · up {up // 86400}d {up % 86400 // 3600}h {up % 3600 // 60}m · "
             f"ping {f'{round(bot.latency * 1000)} ms' if bot.latency == bot.latency else 'n/a'} · Python {platform.python_version()} · discord.py {discord.__version__}"]
    now = int(time.time())
    upcoming = [e for e in db.upcoming_events(interaction.guild_id, now) if e["message_id"]]
    lines.append(f"**Upcoming raids:** {len(upcoming)}")
    needed = ("view_channel", "send_messages", "embed_links", "read_message_history")
    problems = []
    for cid in sorted({e["channel_id"] for e in upcoming}):
        ch = bot.get_channel(cid)
        if ch is None:
            problems.append(f"<#{cid}>: the bot can't see this channel")
            continue
        perms = ch.permissions_for(interaction.guild.me)
        missing = [p.replace("_", " ") for p in needed if not getattr(perms, p)]
        if missing:
            problems.append(f"<#{cid}>: missing {', '.join(missing)}")
    lines.append("**Channel permissions:** " + ("all good ✅" if not problems else "\n" + "\n".join("⚠️ " + p for p in problems)))
    backups = sorted((db.path.parent / "backups").glob("raidbot-*.db")) if (db.path.parent / "backups").is_dir() else []
    lines.append(f"**Last backup:** {backups[-1].name if backups else 'none yet'} · database {db.path.stat().st_size // 1024} KB")
    lines.append(f"**Timezone:** {guild_tz(interaction.guild_id).key} · **Reminder:** {guild_reminder_minutes(interaction.guild_id)} min"
                 f" · **Nudge:** {int(db.setting(interaction.guild_id, 'nudge_hours', 0)) or 'off'}"
                 f" · **Log channel:** {'set' if db.setting(interaction.guild_id, 'log_channel') else 'none'}")
    if db.setting(interaction.guild_id, "update_notice") == "1" and _UPDATE["tag"]:
        newer = release_is_newer(_UPDATE["tag"])
        lines.append(f"**Latest release:** {_UPDATE['tag']}" + (" (an update is available: close the bot and run `Update.bat`)" if newer else " (you're up to date)"))
    if LAST_ERROR:
        lines.append(f"**Last problem:** <t:{int(LAST_ERROR[0])}:R> · {LAST_ERROR[1]} (details in raidbot.log)")
    else:
        lines.append("**Last problem:** none since the bot started")
    if db.recovered_from:
        lines.append(f"⚠️ The database was damaged when the bot started, so it was restored from `{db.recovered_from}`. "
                     "Raids created after that backup are missing; the damaged file was kept next to it.")
    await interaction.response.send_message("\n".join(lines), ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


# ---------------------------------------------------------------- /config (admins)
def is_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and (perms.administrator or getattr(perms, "manage_guild", False)))


class AdminGroup(app_commands.Group):
    """Discord hides these commands from non-admins by default, but a server owner can change that in
    Integration settings, so the bot checks too."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_admin(interaction):
            return True
        await interaction.response.send_message(
            "Only server admins (Manage Server) can change the bot's settings.", ephemeral=True)
        return False


config = AdminGroup(name="config", description="Bot settings for this server",
                    default_permissions=discord.Permissions(manage_guild=True), guild_only=True)
# /config holds Discord's maximum of 25 subcommands, so newer admin settings live in groups of their own
alerts = AdminGroup(name="alerts", description="Optional alerts for officers",
                    default_permissions=discord.Permissions(manage_guild=True), guild_only=True)


COMMON_ZONES = ["America/Vancouver", "America/Edmonton", "America/Los_Angeles", "America/Denver", "America/Chicago",
                "America/Toronto", "America/New_York", "America/Halifax", "Europe/London", "Europe/Berlin", "Europe/Paris",
                "Australia/Sydney", "Pacific/Auckland"]
REMINDER_STEPS = (10, 15, 30, 45, 60, 90, 120)
NUDGE_STEPS = (0, 1, 2, 3, 6, 12, 24, 48)
AUTOCLOSE_STEPS = (0, 1, 2, 3, 6, 12, 24)


class SetupView(discord.ui.View):
    """/config setup: one private panel with a page each for roles, channels, timing and the optional extras.
    Every control saves straight away, using the same settings as the /config commands."""

    def __init__(self, owner_id: int, guild_id: int):
        super().__init__(timeout=900)
        self.owner_id, self.guild_id, self.page = owner_id, guild_id, "home"
        self.build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This panel belongs to the admin who opened it. Run `/config setup` yourself.",
                                                ephemeral=True)
        return False

    # ---- what it says
    def _role(self, key: str) -> str:
        value = int_setting(self.guild_id, key)
        return f"<@&{value}>" if value else "none"

    def _chan(self, key: str) -> str:
        value = int_setting(self.guild_id, key)
        return f"<#{value}>" if value else "none"

    def _on(self, key: str) -> bool:
        return db.setting(self.guild_id, key) == "1"

    def text(self) -> str:
        g = self.guild_id
        head = {"home": "**Raid Bot setup.** Pick a section. Everything saves as soon as you choose it.",
                "roles": "**Roles.** Officers manage raids, the ping role is pinged for new raids, and if you set a sign-up role "
                         "only its members can sign up. Clear a choice to remove it.",
                "channels": "**Channels.** The log channel gets a line for each raid action. The voice channel is checked for "
                            "attendance after a raid starts.",
                "timing": "**Timing.** The server timezone, how long before a raid the reminder goes out, the 'who hasn't answered' "
                          "nudge, and when sign-ups close.",
                "extras": "**Optional extras.** Click to switch each one on or off."}[self.page]
        autoclose = int_setting(g, "autoclose_hours")
        nudge = int_setting(g, "nudge_hours")
        body = {
            "home": [],
            "roles": [f"Officer role: {self._role('officer_role')}", f"Ping role: {self._role('ping_role')}",
                      f"Sign-up role: {self._role('raider_role')}"],
            "channels": [f"Log channel: {self._chan('log_channel')}", f"Voice channel: {self._chan('voice_channel')}"],
            "timing": [f"Timezone: {guild_tz(g).key}", f"Reminder: {plural(guild_reminder_minutes(g), 'minute')} before",
                       f"Unanswered nudge: {plural(nudge, 'hour') + ' before' if nudge else 'off'}",
                       f"Auto-close sign-ups: {plural(autoclose, 'hour') + ' before' if autoclose else 'off'}"],
            "extras": ["Raider.IO import also needs a default realm: `/config raiderio`."] if wd.PRESET_NAME == "retail" else [],
        }[self.page]
        return "\n".join([head] + body)[:2000]

    # ---- the controls of each page
    def build(self) -> None:
        self.clear_items()
        getattr(self, f"_page_{self.page}")()

    def _button(self, label: str, callback, style=discord.ButtonStyle.secondary, row: int | None = None) -> None:
        button = discord.ui.Button(label=label, style=style, row=row)
        button.callback = callback
        self.add_item(button)

    def _goto(self, page: str):
        async def go(interaction: discord.Interaction):
            self.page = page
            self.build()
            await interaction.response.edit_message(content=self.text(), view=self)
        return go

    async def _close(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(content="Setup saved. You can change any of this later with `/config show` and the `/config` commands.", view=None)

    async def _again(self, interaction: discord.Interaction):
        self.build()
        await interaction.response.edit_message(content=self.text(), view=self)

    def _page_home(self) -> None:
        for label, page in (("Roles", "roles"), ("Channels", "channels"), ("Timing", "timing"), ("Extras", "extras")):
            self._button(label, self._goto(page))
        self._button("Done", self._close, discord.ButtonStyle.success)

    def _back(self, row: int) -> None:
        self._button("Back", self._goto("home"), row=row)

    def _role_select(self, key: str, placeholder: str, row: int) -> None:
        current = int_setting(self.guild_id, key)
        select = discord.ui.RoleSelect(placeholder=placeholder, min_values=0, max_values=1, row=row,
                                       default_values=[discord.Object(id=current)] if current else [])

        async def chosen(interaction: discord.Interaction):
            picked = select.values
            value = picked[0] if picked else None
            if value is not None and value.is_default():
                value = None  # @everyone means "no restriction"
            db.set_setting(self.guild_id, key, value.id if value else None)
            await self._again(interaction)
        select.callback = chosen
        self.add_item(select)

    def _page_roles(self) -> None:
        self._role_select("officer_role", "Officer role", 0)
        self._role_select("ping_role", "Role to ping for new raids", 1)
        self._role_select("raider_role", "Sign-up role (only these members can sign up)", 2)
        self._back(3)

    def _channel_select(self, key: str, placeholder: str, types: list, row: int) -> None:
        current = int_setting(self.guild_id, key)
        select = discord.ui.ChannelSelect(placeholder=placeholder, channel_types=types, min_values=0, max_values=1, row=row,
                                          default_values=[discord.Object(id=current)] if current else [])

        async def chosen(interaction: discord.Interaction):
            picked = select.values
            db.set_setting(self.guild_id, key, picked[0].id if picked else None)
            await self._again(interaction)
        select.callback = chosen
        self.add_item(select)

    def _page_channels(self) -> None:
        self._channel_select("log_channel", "Log channel", [discord.ChannelType.text], 0)
        self._channel_select("voice_channel", "Voice channel for the attendance check",
                             [discord.ChannelType.voice, discord.ChannelType.stage_voice], 1)
        self._back(2)

    def _choice_select(self, key: str, placeholder: str, options: list[tuple[str, str]], current: str, row: int) -> None:
        select = discord.ui.Select(placeholder=placeholder, row=row, options=[
            discord.SelectOption(label=label, value=value, default=(value == current)) for label, value in options])

        async def chosen(interaction: discord.Interaction):
            db.set_setting(self.guild_id, key, select.values[0])
            await self._again(interaction)
        select.callback = chosen
        self.add_item(select)

    def _page_timing(self) -> None:
        g = self.guild_id
        zone = guild_tz(g).key
        zones = COMMON_ZONES if zone in COMMON_ZONES else [zone] + COMMON_ZONES
        self._choice_select("timezone", "Timezone", [(z, z) for z in zones[:25]], zone, 0)
        self._choice_select("reminder_minutes", "Reminder before a raid",
                            [(f"{m} minutes", str(m)) for m in REMINDER_STEPS], str(guild_reminder_minutes(g)), 1)
        self._choice_select("nudge_hours", "Ping people who haven't answered",
                            [("Never" if h == 0 else f"{plural(h, 'hour')} before", str(h)) for h in NUDGE_STEPS],
                            str(int_setting(g, "nudge_hours")), 2)
        self._choice_select("autoclose_hours", "Close sign-ups before the start",
                            [("Never" if h == 0 else f"{plural(h, 'hour')} before", str(h)) for h in AUTOCLOSE_STEPS],
                            str(int_setting(g, "autoclose_hours")), 3)
        self._back(4)

    def _toggle(self, key: str, label: str) -> None:
        on = self._on(key)

        async def flip(interaction: discord.Interaction):
            db.set_setting(self.guild_id, key, "0" if self._on(key) else "1")
            await self._again(interaction)
            if key in ("show_realms", "signup_notes", "spec_switch"):  # these change how the posts look
                await redraw_open_posts(self.guild_id)
        self._button(f"{label}: {'on' if on else 'off'}", flip,
                     discord.ButtonStyle.success if on else discord.ButtonStyle.secondary)

    def _page_extras(self) -> None:
        self._toggle("threads", "Discussion threads")
        self._toggle("buffs", "Utility warnings")
        self._toggle("digest", "Weekly digest")
        self._toggle("update_notice", "Update notice")
        self._toggle("confirm_deletes", "Confirm deletes")
        self._toggle("show_realms", "Realms on posts")
        self._toggle("signup_notes", "Signup notes")
        self._toggle("spec_switch", "Spec button")
        if wd.PRESET_NAME == "retail":
            self._toggle("raiderio", "Raider.IO import")
        self._back(1)


@config.command(name="show", description="Show the current settings")
async def config_show(interaction: discord.Interaction):
    g = interaction.guild_id
    officer = db.setting(g, "officer_role")
    ping = db.setting(g, "ping_role")
    lines = [f"**Timezone:** {guild_tz(g).key}",
             f"**Reminder:** {plural(guild_reminder_minutes(g), 'minute')} before the raid",
             f"**Officer role:** {f'<@&{officer}>' if officer else 'none (Manage Events permission only)'}",
             f"**Default ping role:** {f'<@&{ping}>' if ping else 'none'}"]
    lines.append(f"**Discussion threads:** {'on' if threads_default(g) else 'off'}")
    raider = int_setting(g, "raider_role")
    lines.append(f"**Sign-up role:** {f'<@&{raider}>' if raider else 'anyone'}")
    autoclose = int_setting(g, "autoclose_hours")
    autoclose_text = f"{plural(autoclose, 'hour')} before the start" if autoclose else "off"
    lines.append(f"**Auto-close sign-ups:** {autoclose_text}")
    voice = db.setting(g, "voice_channel")
    voice_text = (f"<#{voice}>, {plural(int_setting(g, 'voice_check_minutes', 15), 'minute')} after the start"
                  if voice else "off")
    lines.append(f"**Voice attendance check:** {voice_text}")
    lines.append(f"**Missing-utility warnings:** {'on' if db.setting(g, 'buffs') == '1' else 'off'}")
    lines.append(f"**Weekly attendance digest:** {'on' if db.setting(g, 'digest') == '1' else 'off'}")
    lines.append(f"**Ask before cancel / remove:** {'on' if db.setting(g, 'confirm_deletes') == '1' else 'off'}")
    lines.append(f"**Realms on raid posts:** {'on' if db.setting(g, 'show_realms') == '1' else 'off'}")
    lines.append("**Signup notes:** " + {"off": "off", "officers": "on (officers, in /raid roster)",
                                          "post": "on (also on the raid post)"}[notes_mode(g)])
    lines.append(f"**Spec button on raid posts:** {'on' if spec_switch_on(g) else 'off'}")
    board_ch = db.setting(g, "board_channel")
    lines.append(f"**Upcoming raids board:** {f'<#{board_ch}>' if board_ch else 'off'}")
    backup_ch = db.setting(g, "backup_channel")
    lines.append(f"**Daily backup post:** {f'<#{backup_ch}>' if backup_ch else 'off'}")
    nudge = int(db.setting(g, "nudge_hours", 0))
    nudge_text = f"{plural(nudge, 'hour')} before" if nudge else "off"
    lines.append(f"**Unanswered reminder:** {nudge_text}")
    lines.append(f"**Low-signup alert:** {lowsignup_text(g)}")
    lines.append(f"**Discord events for raids:** {sched_text(g)}")
    log_ch = db.setting(g, "log_channel")
    lines.append(f"**Log channel:** {f'<#{log_ch}>' if log_ch else 'none'}")
    archive = int(db.setting(g, "archive_days", 0))
    archive_text = f"{plural(archive, 'day')} after they start" if archive else "never"
    lines.append(f"**Delete old raid posts:** {archive_text}")
    scoped: dict[int, dict] = {}
    for key, val in db.settings_with_prefix(g).items():
        for prefix, label in (("ping_role@", "ping"), ("officer_role@", "officers"), ("voice_channel@", "voice")):
            if key.startswith(prefix):
                scoped.setdefault(int(key[len(prefix):]), {})[label] = val
    if scoped:
        lines.append("**Channel / category settings:**")
        for cid, d in scoped.items():
            parts = [f"ping <@&{d['ping']}>" if "ping" in d else "", f"officers <@&{d['officers']}>" if "officers" in d else "",
                     f"voice <#{d['voice']}>" if "voice" in d else ""]
            lines.append(f"• <#{cid}>: " + ", ".join(p for p in parts if p))
    await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


@config.command(name="channel", description="Defaults for one channel or category: ping role and raid leaders")
@app_commands.describe(target="Channel or category (default: the channel you run this in)",
                       ping="Role pinged for raids posted there",
                       officer="Role that can manage raids there, and only there",
                       voice="Voice channel for the attendance check of raids posted there (see /config voicechannel)",
                       clear="Remove this channel's settings")
async def config_channel(interaction: discord.Interaction,
                         target: Optional[Union[discord.TextChannel, discord.CategoryChannel]] = None,
                         ping: Optional[discord.Role] = None, officer: Optional[discord.Role] = None,
                         clear: bool = False,
                         voice: Optional[Union[discord.VoiceChannel, discord.StageChannel]] = None):
    tgt = target or interaction.channel
    if isinstance(tgt, discord.Thread):
        tgt = tgt.parent
    if not isinstance(tgt, (discord.TextChannel, discord.CategoryChannel)):
        return await interaction.response.send_message("Pick a text channel or a category.", ephemeral=True)
    g = interaction.guild_id
    quiet = discord.AllowedMentions.none()
    if clear:
        db.set_setting(g, f"ping_role@{tgt.id}", None)
        db.set_setting(g, f"officer_role@{tgt.id}", None)
        db.set_setting(g, f"voice_channel@{tgt.id}", None)
        return await interaction.response.send_message(f"Cleared the settings for {tgt.mention}.",
                                                       ephemeral=True, allowed_mentions=quiet)
    if ping is None and officer is None and voice is None:
        cur_ping = db.setting(g, f"ping_role@{tgt.id}")
        cur_off = db.setting(g, f"officer_role@{tgt.id}")
        cur_voice = db.setting(g, f"voice_channel@{tgt.id}")
        return await interaction.response.send_message(
            f"{tgt.mention}\nPing role: {f'<@&{cur_ping}>' if cur_ping else 'none'}\n"
            f"Officer role: {f'<@&{cur_off}>' if cur_off else 'none'}\n"
            f"Voice channel: {f'<#{cur_voice}>' if cur_voice else 'none'}\n"
            "Set them with the `ping`, `officer` and `voice` options, or `clear: True` to remove them all.",
            ephemeral=True, allowed_mentions=quiet)
    done = []
    if ping is not None:
        db.set_setting(g, f"ping_role@{tgt.id}", ping.id)
        done.append(f"raids posted in {tgt.mention} will ping {ping.mention}")
    if officer is not None:
        db.set_setting(g, f"officer_role@{tgt.id}", officer.id)
        done.append(f"{officer.mention} can manage raids in {tgt.mention} only")
    if voice is not None:
        db.set_setting(g, f"voice_channel@{tgt.id}", voice.id)
        done.append(f"raids posted in {tgt.mention} are attendance-checked in {voice.mention}")
    await interaction.response.send_message("Saved: " + "; ".join(done) + ".", ephemeral=True, allowed_mentions=quiet)


@config.command(name="threads", description="Start a discussion thread under every new raid post")
@app_commands.describe(on="True = every new raid gets a thread (needs the 'Create Public Threads' permission)")
async def config_threads(interaction: discord.Interaction, on: bool):
    db.set_setting(interaction.guild_id, "threads", "1" if on else "0")
    await interaction.response.send_message(
        "New raids will get a discussion thread. The bot needs 'Create Public Threads' in the signup channels."
        if on else "New raids won't get a thread (you can still ask for one with `thread: True` on `/raid create`).",
        ephemeral=True)


@config.command(name="archive", description="Delete raid posts this many days after they start (0 = never)")
@app_commands.describe(days="0 keeps posts forever. Your signup records are kept either way")
async def config_archive(interaction: discord.Interaction, days: app_commands.Range[int, 0, 365]):
    db.set_setting(interaction.guild_id, "archive_days", days)
    await interaction.response.send_message(
        f"Raid posts will be deleted {plural(days, 'day')} after they start." if days else
        "Raid posts will be kept forever.", ephemeral=True)


@config.command(name="nudge", description="Ping people who haven't answered, this many hours before a raid (0 = off)")
@app_commands.describe(hours="0 turns it off. Pings members of the raid's ping role who haven't signed up or declined")
async def config_nudge(interaction: discord.Interaction, hours: app_commands.Range[int, 0, 168]):
    db.set_setting(interaction.guild_id, "nudge_hours", hours)
    await interaction.response.send_message(
        f"{plural(hours, 'hour')} before a raid, I'll ping anyone in its ping role who hasn't answered. "
        "(Needs a ping role: `/config pingrole` or `/config channel`.)" if hours else
        "No reminders for people who haven't answered.", ephemeral=True)

def lowsignup_text(g: int) -> str:
    hours, channel = int_setting(g, "lowsignup_hours"), db.setting(g, "lowsignup_channel")
    if not (hours and channel):
        return "off"
    minimum = int_setting(g, "lowsignup_min")
    return (f"{plural(hours, 'hour')} before, in <#{channel}>" + (f", fewer than {minimum} in" if minimum else "")
            + (", pings officers" if db.setting(g, "lowsignup_ping") == "1" else ""))


@alerts.command(name="lowsignup", description="Tell officers when a raid is short of people, hours before it starts (0 = off)")
@app_commands.describe(hours="How long before the start to check. 0 turns it off",
                       channel="Where the alert goes, e.g. an officers' channel (kept from last time if left empty)",
                       minimum="Also short with fewer than this many in (0 = only role limits; kept if left empty)",
                       ping_officers="Mention the raid's officer role in the alert (default: no; kept from last time if left empty)")
async def alerts_lowsignup(interaction: discord.Interaction, hours: app_commands.Range[int, 0, 168],
                           channel: Optional[discord.TextChannel] = None,
                           minimum: Optional[app_commands.Range[int, 0, 100]] = None,
                           ping_officers: Optional[bool] = None):
    g = interaction.guild_id
    if not hours:
        db.set_setting(g, "lowsignup_hours", 0)
        return await interaction.response.send_message("No more low-signup alerts.", ephemeral=True)
    target = channel or bot.get_channel(int_setting(g, "lowsignup_channel"))
    if target is None:
        return await interaction.response.send_message(
            "Pick a channel for the alerts, ideally one only officers can see. Nothing was changed.", ephemeral=True)
    mine = target.permissions_for(target.guild.me)
    missing = [name for name, ok in (("View Channel", mine.view_channel), ("Send Messages", mine.send_messages)) if not ok]
    if missing:
        return await interaction.response.send_message(
            f"I need {', '.join(missing)} in {target.mention} to post alerts there. Nothing was changed.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    db.set_setting(g, "lowsignup_hours", hours)
    db.set_setting(g, "lowsignup_channel", target.id)
    if minimum is not None:
        db.set_setting(g, "lowsignup_min", minimum or None)
    if ping_officers is not None:
        db.set_setting(g, "lowsignup_ping", "1" if ping_officers else None)
    minimum = int_setting(g, "lowsignup_min")
    ping_officers = db.setting(g, "lowsignup_ping") == "1"
    rule = (f"fewer than {minimum} people are in, or a role with a limit still has open slots" if minimum
            else "a role with a limit (tanks, healers, DPS) still has open slots")
    await interaction.response.send_message(
        f"{plural(hours, 'hour')} before each raid I'll check it once, and if {rule}, say so in {target.mention}"
        f"{' and mention its officer role' if ping_officers else ''}. Raids in a channel that some readers of "
        f"{target.mention} can't see are never mentioned there, and a raid posted less than {plural(hours, 'hour')} "
        "ahead isn't checked.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def sched_text(g: int) -> str:
    if not sched_events_on(g):
        return "off"
    return f"on, {plural(sched_length(g), 'hour')} long"


@alerts.command(name="events", description="Add each raid to the server's Events list as a Discord Scheduled Event (default: off)")
@app_commands.describe(on="True: every new or upcoming raid gets an event. False: the bot deletes the events it made",
                       length="How many hours each event lasts (default 3; kept from last time if left empty)")
async def alerts_events(interaction: discord.Interaction, on: bool,
                        length: Optional[app_commands.Range[int, 1, 12]] = None):
    g = interaction.guild_id
    if not on:
        db.set_setting(g, "sched_events", None)
        made = db.sched_events_of(g)
        await interaction.response.send_message(
            "Raids won't get Discord events any more." + (f" I'm deleting the {plural(len(made), 'event')} I made."
                                                          if made else ""), ephemeral=True)
        for event in made:  # the reply comes first: deleting many events can take longer than Discord waits
            sched_soon(event["id"])
        return
    db.set_setting(g, "sched_events", "1")
    if length is not None:
        db.set_setting(g, "sched_events_hours", length)
    hours = sched_length(g)
    lacking = "" if sched_can_manage(interaction.guild) else (
        "\n⚠️ I don't have the **Manage Events** permission yet, so nothing will appear until a server admin gives it "
        "to my role (Server Settings → Roles). Everything else keeps working without it.")
    await interaction.response.send_message(
        f"Every upcoming raid will appear in the server's **Events** list within a minute, as a {plural(hours, 'hour')} "
        "event linking to its post, and follows the raid when it's edited, cancelled, restored or starts. Members can press "
        "**Interested** there to get Discord's own reminder. Only raids in a channel that everyone in the server can see "
        "get an event, because every member can see the Events list." + lacking, ephemeral=True)


@config.command(name="raiderrole", description="Only members with this role can sign up (leave empty: anyone can)")
@app_commands.describe(role="Officers can always sign people up with /raid add, whatever this is set to")
async def config_raiderrole(interaction: discord.Interaction, role: Optional[discord.Role] = None):
    if role is not None and role.is_default():
        role = None  # @everyone means "no restriction"
    db.set_setting(interaction.guild_id, "raider_role", role.id if role else None)
    await interaction.response.send_message(
        f"Only members with {role.mention} (and officers) can sign up to raids now." if role else
        "Anyone who can see the signup channel can sign up.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@config.command(name="autoclose", description="Close sign-ups this many hours before a raid starts (0 = off)")
@app_commands.describe(hours="0 turns it off. Officers can reopen a raid with /raid close")
async def config_autoclose(interaction: discord.Interaction, hours: app_commands.Range[int, 0, 168]):
    db.set_setting(interaction.guild_id, "autoclose_hours", hours)
    await interaction.response.send_message(
        f"Sign-ups will close {plural(hours, 'hour')} before a raid starts (raids already inside that window are left open)."
        if hours else "Sign-ups stay open until the raid starts.", ephemeral=True)


@config.command(name="voicechannel", description="Voice channel for the automatic attendance check (leave empty to turn it off)")
@app_commands.describe(channel="Where raiders gather. People signed up as 'in' who aren't in it are marked no-show",
                       minutes="How long after the start to check (default 15)")
async def config_voicechannel(interaction: discord.Interaction,
                              channel: Optional[Union[discord.VoiceChannel, discord.StageChannel]] = None,
                              minutes: Optional[app_commands.Range[int, 5, 120]] = None):
    g = interaction.guild_id
    quiet = discord.AllowedMentions.none()
    if channel is None:
        db.set_setting(g, "voice_channel", None)
        return await interaction.response.send_message(
            "The automatic attendance check is off (a channel with its own voice setting from `/config channel` still uses it).",
            ephemeral=True)
    db.set_setting(g, "voice_channel", channel.id)
    if minutes is not None:
        db.set_setting(g, "voice_check_minutes", minutes)
    await interaction.response.send_message(
        f"{plural(int_setting(g, 'voice_check_minutes', 15), 'minute')} after a raid starts, I'll check {channel.mention}. "
        "Anyone signed up as **in** who isn't there is marked as a no-show (undo with `/raid noshow`). "
        "If nobody is in the channel at all, I skip the check. Use `/config channel voice:` for a different channel per raid team.",
        ephemeral=True, allowed_mentions=quiet)


@config.command(name="buffs", description="Warn on the raid post when nobody can provide Bloodlust or a battle res")
@app_commands.describe(on="True = show a warning once 8 or more people are signed up and none of them can provide it")
async def config_buffs(interaction: discord.Interaction, on: bool):
    db.set_setting(interaction.guild_id, "buffs", "1" if on else "0")
    for upcoming in db.upcoming_events(interaction.guild_id, int(time.time()))[:25]:  # redraw the open posts now
        if upcoming["message_id"]:
            await refresh_message(upcoming["id"])
    await interaction.response.send_message(
        "Raid posts will warn about missing Bloodlust / battle res (it depends on the game version in your settings)."
        if on else "Raid posts won't warn about missing utility.", ephemeral=True)


@config.command(name="digest", description="Post a weekly attendance summary in the log channel")
@app_commands.describe(on="True = every Monday (after 09:00, server time) in the channel from /config logchannel")
async def config_digest(interaction: discord.Interaction, on: bool):
    db.set_setting(interaction.guild_id, "digest", "1" if on else "0")
    await interaction.response.send_message(
        "I'll post a weekly attendance summary in the log channel. (Needs `/config logchannel`.)" if on else
        "No weekly summary.", ephemeral=True)


@config.command(name="raiderio", description="Let players import retail characters from Raider.IO with /character import (off by default)")
@app_commands.describe(on="True = allow it. It sends the name and realm a player types to raider.io, nothing else",
                       region="Default region for lookups", realm="Default realm (players can type another)")
@app_commands.choices(region=[app_commands.Choice(name=r.upper(), value=r) for r in RAIDERIO_REGIONS])
async def config_raiderio(interaction: discord.Interaction, on: bool, region: Optional[app_commands.Choice[str]] = None,
                          realm: Optional[str] = None):
    g = interaction.guild_id
    if on and wd.PRESET_NAME != "retail":
        return await interaction.response.send_message(
            "Raider.IO only covers retail, and this server uses the Classic Era class list, so it stays off.", ephemeral=True)
    db.set_setting(g, "raiderio", "1" if on else "0")
    if region is not None:
        db.set_setting(g, "raiderio_region", region.value)
    if realm is not None:
        db.set_setting(g, "raiderio_realm", realm.strip()[:40] or None)
    shown = (f" Default: {(db.setting(g, 'raiderio_region') or 'us').upper()}"
             f"{' / ' + db.setting(g, 'raiderio_realm') if db.setting(g, 'raiderio_realm') else ', no default realm yet'}.")
    await interaction.response.send_message(
        "Players can now use `/character import`. To look a character up I send the name and realm they type to raider.io "
        "(nothing else, no accounts)." + shown if on else "`/character import` is off.", ephemeral=True)


@config.command(name="confirm", description="Ask \"are you sure?\" before /raid cancel and /raid remove (off by default)")
@app_commands.describe(on="True = officers get a private Yes / No question first")
async def config_confirm(interaction: discord.Interaction, on: bool):
    db.set_setting(interaction.guild_id, "confirm_deletes", "1" if on else "0")
    await interaction.response.send_message(
        "`/raid cancel` and `/raid remove` will ask first." if on else
        "`/raid cancel` and `/raid remove` act straight away. A cancelled raid can still be brought back with "
        f"`/raid restore` for {KEEP_CANCELLED_DAYS} days.", ephemeral=True)


@config.command(name="realms", description="Show each character's realm after the name on raid posts (off by default)")
@app_commands.describe(on="True = e.g. Thrall (Area 52), for characters whose player saved a realm")
async def config_realms(interaction: discord.Interaction, on: bool):
    db.set_setting(interaction.guild_id, "show_realms", "1" if on else "0")
    for upcoming in db.upcoming_events(interaction.guild_id, int(time.time()))[:25]:  # redraw the open posts now
        if upcoming["message_id"]:
            await refresh_message(upcoming["id"])
    await interaction.response.send_message(
        "Raid posts now show the realm after a character's name, when its player saved one (`/character realm`)."
        if on else "Raid posts show character names only. Players' saved realms are kept.", ephemeral=True)


@config.command(name="notes", description="Let players add a short note to their signup with /note (off by default)")
@app_commands.describe(on="True = players can use /note, e.g. 'late 15 min'. Officers see notes in /raid roster",
                       onpost="True = also show the notes on the raid post, for everyone (default: officers only)")
async def config_notes(interaction: discord.Interaction, on: bool, onpost: bool = False):
    g = interaction.guild_id
    db.set_setting(g, "signup_notes", "1" if on else "0")
    db.set_setting(g, "notes_on_post", "1" if on and onpost else "0")
    for upcoming in db.upcoming_events(g, int(time.time()))[:25]:  # redraw the open posts now
        if upcoming["message_id"]:
            await refresh_message(upcoming["id"])
    if not on:
        text = "Signup notes are off: `/note` is turned off and notes are hidden. Notes already written are kept."
    elif onpost:
        text = (f"Players can add a note of up to {NOTE_MAX} characters to their signup with `/note`. "
                "It shows on the raid post after their name, and in `/raid roster`.")
    else:
        text = (f"Players can add a note of up to {NOTE_MAX} characters to their signup with `/note`. "
                "Officers see the notes in `/raid roster`; the raid post doesn't show them (`onpost: True` would).")
    await interaction.response.send_message(text, ephemeral=True)


@config.command(name="specswitch", description="Put a Spec button on raid posts to switch spec without signing up again (off by default)")
@app_commands.describe(on="True = players who signed up can pick another spec of the same character from the post")
async def config_specswitch(interaction: discord.Interaction, on: bool):
    g = interaction.guild_id
    db.set_setting(g, "spec_switch", "1" if on else "0")
    await redraw_open_posts(g)  # add or take away the button on the open posts now
    await interaction.response.send_message(
        "Raid posts now have a **🔄 Spec** button. A signed-up player picks another spec of the same class; their "
        "character, status and note stay, and role limits are checked again." if on else
        "The Spec button is gone from raid posts. Players change spec by pressing **Sign Up** again.", ephemeral=True)


@config.command(name="updatenotice", description="Say in the log channel when a newer version of the bot is released (off by default)")
@app_commands.describe(on="True = check GitHub about once a day. It sends no information about your server")
async def config_updatenotice(interaction: discord.Interaction, on: bool):
    db.set_setting(interaction.guild_id, "update_notice", "1" if on else "0")
    await interaction.response.send_message(
        "About once a day I'll ask GitHub for the newest release and post one line in the log channel when there is a newer "
        "one. (Needs `/config logchannel`. GitHub only sees a request from this computer.)" if on else
        "No update notices.", ephemeral=True)


@config.command(name="setup", description="A guided panel for the main settings: roles, channels, timing and extras")
async def config_setup(interaction: discord.Interaction):
    view = SetupView(interaction.user.id, interaction.guild_id)
    await interaction.response.send_message(view.text(), view=view, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


@config.command(name="logchannel", description="Channel that gets a line for each raid created, edited or cancelled")
async def config_logchannel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    db.set_setting(interaction.guild_id, "log_channel", channel.id if channel else None)
    await interaction.response.send_message(
        f"Raid activity will be logged in {channel.mention}." if channel else "Raid activity logging is off.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@config.command(name="backup", description="Get a copy of this server's data (only you see it)")
async def config_backup(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"raidbot-{datetime.now():%Y-%m-%d-%H%M}.db"
        db.snapshot_for_guild(path, interaction.guild_id)  # never another server's raids, players or settings
        size = path.stat().st_size
        if size > 8 * 1024 * 1024:
            return await interaction.followup.send(
                f"This server's data is {size // 1024 // 1024} MB, too big to attach. Daily copies are in the `backups` "
                "folder next to the bot.", ephemeral=True)
        await interaction.followup.send(
            "Here is a copy of this server's data (raids, signups, characters, settings). It contains your members' "
            "Discord IDs, so keep it private. To restore it, stop the bot and replace `raidbot.db` with this file "
            "(it only holds this server's data, so if the bot runs in other servers too, use the `backups` folder instead).",
            file=discord.File(io.BytesIO(path.read_bytes()), filename=path.name), ephemeral=True)


@config.command(name="backupchannel", description="Post a copy of this server's data in a private admin channel every day (off by default)")
@app_commands.describe(channel="A channel only admins can see. Leave empty to turn it off")
async def config_backupchannel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    g = interaction.guild_id
    if channel is None:
        db.set_setting(g, "backup_channel", None)
        return await interaction.response.send_message(
            "No more daily backup posts. Copies already posted stay until you delete them, and the bot still keeps "
            "7 daily copies in its `backups` folder.", ephemeral=True)
    problem = backup_channel_problem(channel)
    if problem:
        return await interaction.response.send_message(problem + " Nothing was changed.", ephemeral=True,
                                                       allowed_mentions=discord.AllowedMentions.none())
    db.set_setting(g, "backup_channel", channel.id)
    db.set_setting(g, "backup_post_last", None)  # so the first copy goes out within a minute
    await interaction.response.send_message(
        f"Every day after {BACKUP_POST_HOUR:02d}:00 (server time) I'll post a copy of this server's data in "
        f"{channel.mention}, starting within a minute; the newest {BACKUP_POSTS_KEPT} are kept. Anyone who can read that "
        "channel can download your members' Discord IDs, so keep it to admins.", ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none())


@config.command(name="board", description="A pinned Upcoming raids message in a channel, kept up to date (off by default)")
@app_commands.describe(channel="Where the board goes, ideally a quiet channel. Leave empty to turn it off")
async def config_board(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    g = interaction.guild_id
    if channel is None:
        await remove_board(g)
        db.set_setting(g, "board_channel", None)
        return await interaction.response.send_message("The Upcoming raids board is off and its message is deleted.",
                                                       ephemeral=True)
    mine = channel.permissions_for(channel.guild.me)
    missing = [name for name, ok in (("View Channel", mine.view_channel), ("Send Messages", mine.send_messages),
                                     ("Embed Links", mine.embed_links)) if not ok]
    if missing:
        return await interaction.response.send_message(
            f"I need {', '.join(missing)} in {channel.mention} to keep the board there. Nothing was changed.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    await remove_board(g)  # moving it, or running this again to bring back a board deleted by hand
    db.set_setting(g, "board_channel", channel.id)
    now = int(time.time())
    embed = board_embed(g, channel, now)
    try:
        msg = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        db.set_setting(g, "board_channel", None)
        return await interaction.response.send_message(
            f"I couldn't post in {channel.mention}, so the board is still off. Check my permissions there.", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())
    db.set_setting(g, "board_message", msg.id)
    _board_drawn[g] = (msg.id, embed.description)
    pinned = await pin_board(msg)
    notes = []
    if not pinned:
        notes.append("I couldn't pin it: give me Manage Messages there, or pin it yourself.")
    _, hidden = board_raids(g, channel, now)
    if hidden:
        notes.append(f"{plural(hidden, 'upcoming raid')} {'is' if hidden == 1 else 'are'} left off because some people "
                     f"who can read {channel.mention} can't see {'its' if hidden == 1 else 'their'} channel.")
    await interaction.response.send_message(
        f"The Upcoming raids board is up in {channel.mention}{' and pinned' if pinned else ''}. It changes by itself when "
        "raids are posted, edited, cancelled or start, and when people sign up. Raids only appear if everyone who can "
        "read that channel can see the raid's channel." + "".join(f"\n{n}" for n in notes), ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none())


@config.command(name="timezone", description="Timezone used when typing raid dates")
@app_commands.describe(name="e.g. America/New_York or Europe/London")
async def config_timezone(interaction: discord.Interaction, name: str):
    try:
        zone = ZoneInfo(name.strip())
    except Exception:
        return await interaction.response.send_message(
            "I don't know that timezone. Use a name like `America/New_York` or `Europe/London`.", ephemeral=True)
    db.set_setting(interaction.guild_id, "timezone", zone.key)
    await interaction.response.send_message(f"Timezone set to **{zone.key}**.", ephemeral=True)


@config.command(name="reminder", description="How many minutes before a raid the bot pings signups")
async def config_reminder(interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 1440]):
    db.set_setting(interaction.guild_id, "reminder_minutes", minutes)
    await interaction.response.send_message(f"Reminders go out **{plural(minutes, 'minute')}** before the raid.", ephemeral=True)


@config.command(name="officer", description="Role allowed to manage raids (leave empty to clear)")
async def config_officer(interaction: discord.Interaction, role: Optional[discord.Role] = None):
    db.set_setting(interaction.guild_id, "officer_role", role.id if role else None)
    await interaction.response.send_message(
        f"Members with {role.mention} can now use `/raid` commands." if role else
        "Officer role cleared. Only people with Manage Events can use `/raid` commands.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@config.command(name="pingrole", description="Role pinged whenever a raid is posted (leave empty to clear)")
async def config_pingrole(interaction: discord.Interaction, role: Optional[discord.Role] = None):
    db.set_setting(interaction.guild_id, "ping_role", role.id if role else None)
    await interaction.response.send_message(
        f"New raids will ping {role.mention}." if role else "New raids won't ping anyone by default.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


# ---------------------------------------------------------------- /help
@bot.tree.command(name="help", description="What this bot can do")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title=f"Raid Bot v{VERSION}", colour=discord.Colour.blurple())
    embed.add_field(name="Everyone", inline=False, value=(
        "`/myraids` the upcoming raids you've answered · `/stats me` your attendance\n"
        "On a raid post press **Sign Up** and pick a character or class and spec. "
        "**Late / Tentative / Bench / Absent** change your status.\n"
        "`/character add` save a character (do this once, then sign up in one click)\n"
        "`/character list` · `/character main` · `/character realm` · `/character remove`\n"
        "`/remindme` get reminders as a private message · `/always join` stay signed up to a repeating raid\n"
        "`/note` add a short note to your signup, e.g. late 15 min (if your admin turned it on)\n"
        "`/forgetme` delete your characters and signup history from the bot\n"
        "`/character import` look a retail character up on Raider.IO (if your admin turned it on)\n"
        "The **Calendar** button on a raid post gives you a file for your calendar app."))
    embed.add_field(name="Officers", inline=False, value=(
        "`/raid create` post a raid (optionally ping a role)\n"
        "`/raid edit` change title, time, description or limits\n"
        "`/raid roster` copy-friendly list of who is coming (`invites: True` for /invite lines)\n"
        "`/raid copy` same raid, new date · `/raid repeat` weekly or every 2 weeks\n"
        "`/raid template save|list|delete` saved setups, then `/raid create template:`\n"
        "`/raid noshow` mark someone who didn't turn up · `/stats team` everyone's attendance · `/stats export` as a spreadsheet\n"
        "`/raid list` upcoming raids with counts · `/raid add` sign someone up or change their status\n"
        "`/raid promote` move a benched player in, ahead of the line\n"
        "`/raid close` · `/raid cancel` (undo: `/raid restore`) · `/raid remove` · `/raid unsigned`"))
    embed.add_field(name="Admins", inline=False, value=(
        "`/config show` · `/config timezone` · `/config reminder`\n"
        "`/config officer` who can manage every raid · `/config pingrole` default role to ping\n"
        "`/config channel` per-channel (or category) ping role and raid leaders\n"
        "`/config threads` discussion thread per raid · `/config archive` delete old posts\n"
        "`/config nudge` ping people who haven't answered · `/alerts lowsignup` tell officers a raid is short · `/alerts events` Discord events · `/config logchannel` activity log\n"
        "`/config raiderrole` only that role can sign up · `/config autoclose` close sign-ups before the start\n"
        "`/config voicechannel` automatic no-shows from a voice channel · `/config buffs` warn about missing Bloodlust / battle res\n"
        "`/config digest` weekly attendance summary. Officers can limit classes: `/raid create classes: Mage 4`\n"
        "`/config setup` guided panel for the main settings · `/config raiderio` · `/config updatenotice` · `/config confirm` · `/config realms` · `/config notes`\n"
        "`/config backup` get a copy of the database · `/status` bot health check"))
    embed.set_footer(text="Anyone on the bench is moved in automatically when a slot opens.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return  # the check already told the user
    name = interaction.command.qualified_name if interaction.command else "?"
    log.error("Command /%s failed", name, exc_info=error)
    note_error(f"/{name}: {error!r}")
    msg = "Something went wrong with that command. The details are in the bot's window; ask your admin to check it."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------- class icons
async def load_class_emojis() -> None:
    """Upload icons/<class>.jpg|png as the bot's own (application) emoji once, then reuse them.

    Application emoji belong to the bot, so they work in any server it's in and nobody has to
    upload anything by hand. Delete an emoji in the Developer Portal to make the bot re-upload it.
    """
    icon_dir = Path(__file__).parent / "icons"
    if not icon_dir.is_dir():
        return
    try:
        existing = {e.name: e for e in await bot.fetch_application_emojis()}
    except discord.HTTPException as e:
        log.warning("Couldn't read application emoji, using colour squares instead: %s", e)
        return
    for cls in wd.class_names():
        slug = cls.lower().replace(" ", "")
        emoji = existing.get(f"wow_{slug}")
        if emoji is None:
            path = next((p for p in (icon_dir / f"{slug}.png", icon_dir / f"{slug}.jpg") if p.exists()), None)
            if path is None:
                continue
            try:
                emoji = await bot.create_application_emoji(name=f"wow_{slug}", image=path.read_bytes())
                log.info("Uploaded emoji for %s", cls)
            except discord.HTTPException as e:
                log.warning("Couldn't upload icon for %s: %s", cls, e)
                continue
        wd.CLASS_EMOJI.setdefault(cls, str(emoji))  # setdefault: a hand-set CLASS_EMOJI entry wins


# ---------------------------------------------------------------- startup
@bot.event
async def setup_hook():
    await load_class_emojis()
    bot.add_view(RaidView(spec_button=True))  # keeps old raid posts working after a restart (with every button, so a
    # post that shows the Spec button is answered even if it was drawn before a restart)
    bot.tree.add_command(character)
    bot.tree.add_command(raid)
    bot.tree.add_command(config)
    bot.tree.add_command(stats)
    bot.tree.add_command(always)
    bot.tree.add_command(alerts)
    if GUILD_ID:  # guild sync is instant; global sync can take up to an hour
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()
    reminder_loop.start()
    backup_loop.start()


@bot.event
async def on_ready():
    log.info("Logged in as %s (WoW preset: %s)", bot.user, os.getenv("WOW_VERSION") or "retail")


def preflight() -> None:
    """Check settings and talk to Discord once, so problems get a plain-English explanation."""
    global GUILD_ID
    print(f"Raid Bot v{VERSION}")
    print(f"  Folder:   {BASE_DIR}")

    if not ENV_PATH.exists():
        lookalikes = [p.name for p in BASE_DIR.iterdir()
                      if p.is_file() and p.name != ".env" and (p.suffix == ".env" or p.name.startswith(".env"))]
        msg = f"There is no file named exactly '.env' in {BASE_DIR}."
        if lookalikes:
            msg += f"\nI found {', '.join(lookalikes)} instead. Rename it to exactly:  .env"
        elif WINDOWS:
            msg += "\nRun SETUP.bat (or re-extract the download) to create it."
        else:
            msg += "\nRun:  python setup.py   (or copy .env.example to .env and fill it in)"
        if WINDOWS:
            msg += "\n(Tip: in File Explorer turn on View -> Show -> File name extensions, so you can see names like .env.txt.)"
        raise SystemExit(msg)
    print("  Settings: .env found")

    if not TOKEN:
        editor = "Notepad" if WINDOWS else "a text editor"
        helper = "double-click SETUP.bat" if WINDOWS else "run:  python setup.py"
        raise SystemExit(f"DISCORD_TOKEN is empty. Open .env in {editor}, paste your bot token after DISCORD_TOKEN=, and save.\n"
                         f"Or {helper} and it will do this for you.")
    problem = checks.token_problem(TOKEN)
    if problem:
        raise SystemExit(f"DISCORD_TOKEN in .env looks wrong: {problem}")

    try:
        me = checks.check_token(TOKEN)
    except checks.DiscordError as e:
        if e.status == 401:
            raise SystemExit("Discord rejected this token. It was probably reset or copied wrong.\n"
                             "Fix: Developer Portal -> your application -> Bot -> Reset Token -> Copy, "
                             "paste it after DISCORD_TOKEN= in .env, save, and start again.")
        print(f"  Discord:  couldn't verify the token ({e}); trying to start anyway")
        return
    print(f"  Token:    OK (bot is '{me.get('username')}')")

    if checks.members_intent_enabled(TOKEN) is False:
        raise SystemExit("The 'Server Members Intent' is switched off.\n"
                         "Fix: Developer Portal -> your application -> Bot -> Privileged Gateway Intents -> "
                         "turn on SERVER MEMBERS INTENT -> Save Changes, then start again.")

    try:
        guilds = checks.bot_guilds(TOKEN)
    except checks.DiscordError:
        return
    invite = checks.invite_url(me["id"])
    if not guilds:
        raise SystemExit("The bot isn't in any server yet. Open this link, pick your server and click Authorize:\n"
                         f"{invite}\nThen start again.")
    ids = {int(g["id"]) for g in guilds}
    if GUILD_ID and GUILD_ID not in ids:
        names = ", ".join(f"{g['name']} ({g['id']})" for g in guilds)
        raise SystemExit(f"GUILD_ID {GUILD_ID} in .env isn't a server this bot is in.\n"
                         f"The bot is in: {names}\n"
                         f"Fix GUILD_ID (or leave it blank), or add the bot to that server: {invite}")
    if not GUILD_ID and len(guilds) == 1:
        GUILD_ID = int(guilds[0]["id"])
        print(f"  Server:   '{guilds[0]['name']}' (picked automatically)")
    elif GUILD_ID:
        print(f"  Server:   {next(g['name'] for g in guilds if int(g['id']) == GUILD_ID)}")
    else:
        print("  Server:   GUILD_ID is blank and the bot is in several servers, so commands may take up to an hour to appear.")


def setup_logging() -> None:
    """Console plus raidbot.log (1 MB x 5 files) next to the bot, so errors survive closing the window."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            BASE_DIR / "raidbot.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"))
    except OSError:
        pass  # read-only folder: console only
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


CRASH_EXIT_CODE = 3  # start.bat restarts the bot on this code, but not on a settings problem


if __name__ == "__main__":
    setup_logging()
    if db.recovered_from:
        log.warning("The database was damaged; restored from backups/%s (the damaged file was kept)", db.recovered_from)
    preflight()
    try:
        bot.run(TOKEN, log_handler=None)
    except discord.LoginFailure:
        raise SystemExit("Discord rejected the token at login. Reset it in the Developer Portal and paste the new one into .env.")
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Turn on 'Server Members Intent': Developer Portal -> Bot -> Privileged Gateway Intents -> Save Changes.")
    except Exception:
        log.exception("The bot crashed")
        print("The bot crashed. The details are saved in raidbot.log.")
        raise SystemExit(CRASH_EXIT_CODE)
