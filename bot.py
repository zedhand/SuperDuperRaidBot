"""Single-guild raid signup bot (a small Raid-Helper alternative)."""
import logging
import os
import platform
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Union
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

VERSION = "1.8.4"
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"  # always the .env next to bot.py, whatever folder you launch from
load_dotenv(ENV_PATH)

import checks  # noqa: E402
import wow_data as wd  # noqa: E402  # noqa: E402  (after load_dotenv so WOW_VERSION is read)
from db import DB  # noqa: E402
from embeds import build_embed  # noqa: E402

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
db = DB(os.getenv("DB_PATH", "raidbot.db"))

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


def guild_reminder_minutes(guild_id: int) -> int:
    return int(db.setting(guild_id, "reminder_minutes", REMINDER_MINUTES))


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


def apply_cap(event, user_id: int, role: str, status: str) -> tuple[str, str | None]:
    """Counted statuses ('in', 'late') overflow to the bench when their role is full.

    Returns (status, bench_from). bench_from remembers what they asked for so the bot can
    promote them if a slot opens up."""
    if status in ("in", "late"):
        cap = event[f"cap_{role}"]
        if cap and db.count_role(event["id"], role, user_id) >= cap:
            return "bench", status
    return status, None


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
        db.set_signup(event_id, s["user_id"], s["char_name"], s["wow_class"], s["spec"], s["role"], s["bench_from"])
        text = f"🎉 A {wd.ROLE_LABEL[s['role']].rstrip('s').lower()} slot opened up. You're off the bench for **{event['title']}**!"
        try:
            await (await bot.fetch_user(s["user_id"])).send(text)
        except discord.HTTPException:  # DMs closed: mention them in the raid channel instead
            channel = bot.get_channel(event["channel_id"])
            if channel:
                try:
                    await channel.send(f"<@{s['user_id']}> {text}")
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


async def update_event(event_id: int) -> None:
    """After any roster change: promote benched players if there is room, then redraw the post."""
    await promote_bench(event_id)
    await refresh_message(event_id)


async def refresh_message(event_id: int) -> None:
    event = db.event(event_id)
    if not event or not event["message_id"]:
        return
    channel = bot.get_channel(event["channel_id"])
    if channel is None:
        try:
            channel = await bot.fetch_channel(event["channel_id"])
        except discord.HTTPException:
            return
    locked = is_locked(event)
    embed = build_embed(event, db.signups(event_id), locked)
    try:
        await channel.get_partial_message(event["message_id"]).edit(
            embed=embed, view=ClosedView() if locked else RaidView())
    except discord.HTTPException as e:
        log.warning("Could not edit event %s message: %s", event_id, e)


def char_dict(row) -> dict:
    return {"name": row["char_name"], "cls": row["wow_class"], "spec": row["spec"], "role": row["role"]}


# ---------------------------------------------------------------- raid message UI
class CharacterSelect(discord.ui.Select):
    def __init__(self, event_id: int, chars):
        self.event_id = event_id
        # No option is preselected: choosing an already-selected option sends nothing to the bot, which
        # would leave a player with one saved character unable to sign up. The main is starred instead.
        opts = [discord.SelectOption(label=(("⭐ " if c["is_main"] else "") + c["name"])[:100],
                                     description=f"{c['spec']} {c['wow_class']}"[:100],
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
    status, bench_from = apply_cap(event, interaction.user.id, role, "in")
    db.set_signup(event_id, interaction.user.id, name, cls, spec, role, status, bench_from)
    note = "" if status == "in" else f" ({wd.ROLE_LABEL[role].rstrip('s')} slots are full, so you were put on the bench)"
    await interaction.response.edit_message(
        content=f"Signed up as **{name}** — {spec} {cls} ({wd.ROLE_LABEL[role]}){note}.", view=None)
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


class CharacterPickView(discord.ui.View):
    """Saved characters (if any) plus a class picker for signing up without one."""

    def __init__(self, event_id: int, chars):
        super().__init__(timeout=120)
        if chars:
            self.add_item(CharacterSelect(event_id, chars))
        self.add_item(ClassSelect(event_id))


class ClosedView(discord.ui.View):
    """Replaces the buttons with a greyed-out bar once an event is closed."""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Select(placeholder="This event is closed.", disabled=True,
                                        options=[discord.SelectOption(label="Closed", value="closed")]))


class RaidView(discord.ui.View):
    """Persistent buttons under every raid post. Event is found via the message id."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _event(self, interaction: discord.Interaction):
        event = db.event_by_message(interaction.message.id)
        if not event:
            await interaction.response.send_message("This event no longer exists.", ephemeral=True)
            return None
        if is_locked(event):
            await interaction.response.send_message("Signups are closed for this event.", ephemeral=True)
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
            status, bench_from = apply_cap(event, uid, row["role"], status)
            db.set_signup(event["id"], uid, row["char_name"], row["wow_class"], row["spec"],
                          row["role"], status, bench_from)
        await interaction.response.send_message(f"You are now marked as **{status}**.", ephemeral=True)
        await update_event(event["id"])

    @discord.ui.button(label="Sign Up", style=discord.ButtonStyle.success, custom_id="raid:signup")
    async def signup(self, interaction: discord.Interaction, _: discord.ui.Button):
        event = await self._event(interaction)
        if not event:
            return
        chars = db.characters(interaction.user.id)
        prompt = ("Pick a saved character, or choose a class to sign up without one:" if chars else
                  "Choose your class to sign up. (Tip: save characters with `/character add` to skip this next time.)")
        await interaction.response.send_message(prompt, view=CharacterPickView(event["id"], chars),
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


# ---------------------------------------------------------------- /character
character = app_commands.Group(name="character", description="Manage your WoW characters")


async def class_autocomplete(_: discord.Interaction, current: str):
    return [app_commands.Choice(name=c, value=c) for c in wd.class_names()
            if current.lower() in c.lower()][:25]


async def spec_autocomplete(interaction: discord.Interaction, current: str):
    cls = getattr(interaction.namespace, "wowclass", None)
    return [app_commands.Choice(name=s, value=s) for s in wd.spec_names(cls)
            if current.lower() in s.lower()][:25]


async def own_char_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=f"{c['name']} ({c['spec']} {c['wow_class']})", value=c["name"])
            for c in db.characters(interaction.user.id) if current.lower() in c["name"].lower()][:25]


@character.command(name="add", description="Add a character (or change the spec of an existing one)")
@app_commands.describe(name="Character name", wowclass="Class", spec="Spec", main="Make this your main")
@app_commands.autocomplete(wowclass=class_autocomplete, spec=spec_autocomplete)
async def character_add(interaction: discord.Interaction, name: str, wowclass: str, spec: str,
                        main: bool = False):
    cls = wd.find_class(wowclass)
    if not cls:
        return await interaction.response.send_message(
            f"Unknown class. Options: {', '.join(wd.class_names())}", ephemeral=True)
    sp = wd.find_spec(cls, spec)
    if not sp:
        return await interaction.response.send_message(
            f"Unknown spec for {cls}. Options: {', '.join(wd.spec_names(cls))}", ephemeral=True)
    name = name.strip()[:32]
    db.upsert_character(interaction.user.id, name, cls, sp, wd.role_for(cls, sp), main)
    await interaction.response.send_message(f"Saved **{name}** — {sp} {cls}.", ephemeral=True)


@character.command(name="list", description="Show your characters")
async def character_list(interaction: discord.Interaction):
    chars = db.characters(interaction.user.id)
    if not chars:
        return await interaction.response.send_message("No characters yet. Use `/character add`.",
                                                       ephemeral=True)
    lines = [f"{'⭐ ' if c['is_main'] else ''}**{c['name']}** — {c['spec']} {c['wow_class']} ({c['role']})"
             for c in chars]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


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


def parse_start(guild_id: int, date: str, at: str) -> int | None:
    """'2026-11-14' + '20:00' in the server's timezone -> unix time, or None if unreadable."""
    try:
        start = datetime.strptime(f"{date.strip()} {at.strip()}", "%Y-%m-%d %H:%M").replace(tzinfo=guild_tz(guild_id))
    except ValueError:
        return None
    return int(start.timestamp())


BAD_TIME = "Couldn't read that date/time. Use `YYYY-MM-DD` and `HH:MM` (24-hour), e.g. `2026-11-14` `20:00`."


def threads_default(guild_id: int) -> bool:
    return db.setting(guild_id, "threads", "0") == "1"


async def post_event(channel, guild, creator_id: int, title: str, description: str, ts: int,
                     tanks: int, healers: int, dps: int, ping=None, repeat_days: int = 0,
                     thread: bool = False):
    """Create the event row and its post. Returns (event_id, message, notes). Raises HTTPException
    (and leaves nothing behind) if the post itself can't be sent."""
    event_id = db.create_event(guild.id, channel.id, title, description, ts, creator_id,
                               tanks, healers, dps, repeat_days)
    event = db.event(event_id)
    if ping and ping.is_default():
        mentions = discord.AllowedMentions(everyone=True)
    elif ping:
        mentions = discord.AllowedMentions(roles=[ping])
    else:
        mentions = discord.AllowedMentions.none()
    try:
        msg = await channel.send(content=ping.mention if ping else None, embed=build_embed(event, [], False),
                                 view=RaidView(), allowed_mentions=mentions)
    except discord.HTTPException:
        db.delete_event(event_id)
        raise
    db.set_message_id(event_id, msg.id)
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
    """Delete the raid's post and its thread, ignoring anything already gone."""
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
@app_commands.describe(date="YYYY-MM-DD (server timezone)", at="HH:MM, 24-hour",
                       title="Raid name (optional if you pick a template)",
                       template="Start from a saved template (see /raid template save)",
                       description="Extra info (optional)", tanks="Max tanks (0 = no limit)",
                       healers="Max healers (0 = no limit)", dps="Max DPS (0 = no limit)",
                       ping="Role to ping (default: this channel's, category's or the server's ping role)",
                       repeat="Post the next one automatically", thread="Start a discussion thread under the post")
@app_commands.choices(repeat=REPEAT_CHOICES)
@app_commands.autocomplete(template=template_autocomplete)
async def raid_create(interaction: discord.Interaction, date: str, at: str, title: Optional[str] = None,
                      template: Optional[str] = None, description: Optional[str] = None,
                      tanks: Optional[app_commands.Range[int, 0, 40]] = None,
                      healers: Optional[app_commands.Range[int, 0, 40]] = None,
                      dps: Optional[app_commands.Range[int, 0, 40]] = None,
                      ping: Optional[discord.Role] = None,
                      repeat: Optional[app_commands.Choice[int]] = None, thread: Optional[bool] = None):
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
    ts = parse_start(interaction.guild_id, date, at)
    if ts is None:
        return await interaction.response.send_message(BAD_TIME, ephemeral=True)
    if ts <= int(time.time()):
        return await interaction.response.send_message("That time is in the past.", ephemeral=True)

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
            thread=thread if thread is not None else threads_default(interaction.guild_id))
    except discord.HTTPException:
        return await interaction.followup.send(
            "I couldn't post in this channel. Give the bot View Channel, Send Messages and Embed Links here.",
            ephemeral=True)
    await audit(interaction.guild_id, f"📅 {interaction.user.mention} created **{title}** (#{event_id}) in {interaction.channel.mention}")
    await interaction.followup.send("\n".join([f"Created event #{event_id}: {msg.jump_url}"] +
                                              [f"⚠️ {n}" for n in notes]), ephemeral=True)


@raid.command(name="copy", description="Copy a raid to a new date (same limits and description, no signups)")
@app_commands.rename(at="time")
@app_commands.describe(date="New date YYYY-MM-DD", at="New time HH:MM, 24-hour", title="New title (default: same)")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_copy(interaction: discord.Interaction, event_id: int, date: str, at: str,
                    title: Optional[str] = None):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    ts = parse_start(interaction.guild_id, date, at)
    if ts is None:
        return await interaction.response.send_message(BAD_TIME, ephemeral=True)
    if ts <= int(time.time()):
        return await interaction.response.send_message("That time is in the past.", ephemeral=True)
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
            thread=threads_default(interaction.guild_id))
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
    await interaction.response.send_message(
        f"Event #{event_id} will repeat {every.name.lower()}: the next one is posted when this one starts."
        if every.value else f"Event #{event_id} will no longer repeat.", ephemeral=True)
    await refresh_message(event_id)


async def spawn_next(event) -> None:
    """Post the next occurrence of a recurring raid, keeping the same wall-clock time across DST."""
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
        except discord.HTTPException:
            log.warning("Recurring raid #%s: its channel is gone, not posting the next one", event["id"])
            return
    role_id = default_ping_role_id(event["guild_id"], channel)
    try:
        await post_event(channel, channel.guild, event["creator_id"], event["title"], event["description"],
                         int(nxt.timestamp()), event["cap_tank"], event["cap_healer"], event["cap_dps"],
                         ping=channel.guild.get_role(role_id) if role_id else None,
                         repeat_days=event["repeat_days"], thread=threads_default(event["guild_id"]))
        log.info("Posted the next occurrence of recurring raid #%s", event["id"])
    except discord.HTTPException as e:
        log.warning("Couldn't post the next occurrence of raid #%s: %s", event["id"], e)


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
    db.save_template(interaction.guild_id, name, event["title"], event["description"], event["cap_tank"],
                     event["cap_healer"], event["cap_dps"], event["repeat_days"])
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
        lines.append(f"**{t['name']}**: {t['title']} (tanks/healers/dps {caps}){rep}")
    await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True)


@template.command(name="delete", description="Delete a saved template")
@app_commands.autocomplete(name=template_autocomplete)
async def template_delete(interaction: discord.Interaction, name: str):
    ok = db.delete_template(interaction.guild_id, name.strip())
    await interaction.response.send_message(
        f"Deleted template **{name}**." if ok else "No template with that name.", ephemeral=True)


@raid.command(name="edit", description="Change an event's title, time, description or role limits")
@app_commands.rename(at="time")
@app_commands.describe(title="New title", description="New description", date="New date YYYY-MM-DD",
                       at="New time HH:MM (24-hour)", tanks="New tank limit (0 = no limit)",
                       healers="New healer limit (0 = no limit)", dps="New DPS limit (0 = no limit)",
                       notify="If the time changed, tell everyone who signed up (default yes)")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_edit(interaction: discord.Interaction, event_id: int, title: Optional[str] = None,
                    description: Optional[str] = None, date: Optional[str] = None,
                    at: Optional[str] = None, tanks: Optional[app_commands.Range[int, 0, 40]] = None,
                    healers: Optional[app_commands.Range[int, 0, 40]] = None,
                    dps: Optional[app_commands.Range[int, 0, 40]] = None, notify: bool = True):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    fields: dict = {}
    if title:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    for key, val in (("cap_tank", tanks), ("cap_healer", healers), ("cap_dps", dps)):
        if val is not None:
            fields[key] = val
    time_changed = False
    if date or at:
        tz = guild_tz(interaction.guild_id)
        cur = datetime.fromtimestamp(event["start_ts"], tz)
        try:
            new = datetime.strptime(f"{date or cur.strftime('%Y-%m-%d')} {at or cur.strftime('%H:%M')}",
                                    "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except ValueError:
            return await interaction.response.send_message(
                "Couldn't read that date/time. Use `YYYY-MM-DD` and `HH:MM` (24-hour).", ephemeral=True)
        ts = int(new.timestamp())
        if ts <= int(time.time()):
            return await interaction.response.send_message("That time is in the past.", ephemeral=True)
        if ts != event["start_ts"]:
            fields.update(start_ts=ts, reminded=0, lock_shown=0)  # re-arm the reminder and the lock
            time_changed = True
    if not fields:
        return await interaction.response.send_message("Nothing to change. Fill in at least one option.",
                                                       ephemeral=True)
    db.update_event(event_id, **fields)
    await audit(interaction.guild_id, f"✏️ {interaction.user.mention} edited **{event['title']}** (#{event_id}): "
                                      + ", ".join(sorted(k for k in fields if k not in ("reminded", "lock_shown"))))
    await interaction.response.send_message(f"Updated event #{event_id}.", ephemeral=True)
    await update_event(event_id)  # a raised limit may free bench slots
    if time_changed and notify:
        users = [x["user_id"] for x in db.signups(event_id) if x["status"] in ("in", "late", "tentative", "bench")]
        channel = bot.get_channel(event["channel_id"])
        if channel and users:
            try:
                await channel.send(f"📅 **{fields.get('title', event['title'])}** moved to <t:{fields['start_ts']}:F> "
                                   f"(<t:{fields['start_ts']}:R>). " + " ".join(f"<@{u}>" for u in users),
                                   allowed_mentions=discord.AllowedMentions(users=True))
            except discord.HTTPException:
                pass


@raid.command(name="roster", description="Private, copy-friendly list of who is coming")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_roster(interaction: discord.Interaction, event_id: int):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    signups = db.signups(event_id)
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
    await interaction.response.send_message("```\n" + "\n".join(lines) + "\n```", ephemeral=True)


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


@raid.command(name="cancel", description="Delete an event and its post")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_cancel(interaction: discord.Interaction, event_id: int):
    event = await _get_event(interaction, event_id)
    if not event:
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    await delete_event_post(event)
    db.delete_event(event_id)
    await audit(interaction.guild_id, f"🗑️ {interaction.user.mention} cancelled **{event['title']}** (#{event_id})")
    await interaction.response.send_message(f"Event #{event_id} deleted.", ephemeral=True)


@raid.command(name="remove", description="Remove a member's signup")
@app_commands.autocomplete(event_id=event_autocomplete)
async def raid_remove(interaction: discord.Interaction, event_id: int, member: discord.Member):
    if not await _get_event(interaction, event_id):
        return await interaction.response.send_message("Event not found.", ephemeral=True)
    db.remove_signup(event_id, member.id)
    await audit(interaction.guild_id, f"🚫 {interaction.user.mention} removed {member.mention} from #{event_id}")
    await interaction.response.send_message(f"Removed {member.mention} from event #{event_id}.",
                                            ephemeral=True)
    await update_event(event_id)


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
        await channel.send(head + mentions, allowed_mentions=discord.AllowedMentions(users=True))
    except discord.HTTPException as e:
        log.warning("Nudge failed for event %s: %s", event["id"], e)


@tasks.loop(minutes=1)
async def reminder_loop():
    now = int(time.time())
    for event in db.due_lock(now):  # start time passed: show "This event is closed."
        db.mark_lock_shown(event["id"])
        await refresh_message(event["id"])
    for event in db.due_repeat(now):  # recurring raid started: post the next one (once)
        db.update_event(event["id"], repeated=1)
        await spawn_next(event)
    for event in db.archivable(now - 86400):  # optional cleanup of old posts (/config archive)
        days = int(db.setting(event["guild_id"], "archive_days", 0))
        if days and event["start_ts"] <= now - days * 86400:
            db.update_event(event["id"], archived=1)
            await delete_event_post(event)
    for event in db.pending_reminders(now):
        if event["start_ts"] - guild_reminder_minutes(event["guild_id"]) * 60 > now:
            continue  # not due yet
        users = [x["user_id"] for x in db.signups(event["id"]) if x["status"] in ("in", "late", "tentative")]
        if not users:
            continue  # nobody to remind yet: keep the reminder for when the first person signs up
        db.mark_reminded(event["id"])
        channel = bot.get_channel(event["channel_id"])
        if channel:
            pings = " ".join(f"<@{u}>" for u in users)
            try:
                await channel.send(f"⏰ **{event['title']}** starts <t:{event['start_ts']}:R>! {pings}"[:2000])
            except discord.HTTPException as e:
                log.warning("Reminder failed for event %s: %s", event["id"], e)
    for event in db.pending_nudges(now):  # optional "who hasn't answered" ping (/config nudge)
        hours = int(db.setting(event["guild_id"], "nudge_hours", 0))
        if hours and event["start_ts"] - hours * 3600 <= now:
            db.update_event(event["id"], nudged=1)
            await nudge_unanswered(event)


@tasks.loop(hours=6)
async def backup_loop():
    """One copy of raidbot.db per day in the 'backups' folder; the newest 7 are kept."""
    try:
        made = db.backup_daily()
        if made:
            log.info("Backed up the database to %s", made.name)
    except Exception as e:  # a failed backup must never stop the bot
        log.warning("Database backup failed: %s", e)


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


@bot.tree.command(name="myraids", description="The upcoming raids you've answered, with links")
@app_commands.guild_only()
async def myraids(interaction: discord.Interaction):
    rows = db.user_upcoming(interaction.guild_id, interaction.user.id, int(time.time()))
    if not rows:
        return await interaction.response.send_message("You haven't answered any upcoming raids.", ephemeral=True)
    label = {"in": "✅ in", "late": "⏰ late", "tentative": "❔ tentative", "bench": "🪑 bench", "absent": "❌ absent"}
    lines = [f"{label.get(r['my_status'], r['my_status'])} · <t:{r['start_ts']}:f> · "
             f"[{r['title']}](https://discord.com/channels/{r['guild_id']}/{r['channel_id']}/{r['message_id']})"
             for r in rows[:15]]
    await interaction.response.send_message("\n".join(lines), ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


@bot.tree.command(name="status", description="Bot health: version, uptime, permissions and backups")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def status(interaction: discord.Interaction):
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
    await interaction.response.send_message("\n".join(lines), ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


# ---------------------------------------------------------------- /config (admins)
config = app_commands.Group(name="config", description="Bot settings for this server",
                            default_permissions=discord.Permissions(manage_guild=True), guild_only=True)


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
    nudge = int(db.setting(g, "nudge_hours", 0))
    lines.append(f"**Unanswered reminder:** {f'{plural(nudge, 'hour')} before' if nudge else 'off'}")
    log_ch = db.setting(g, "log_channel")
    lines.append(f"**Log channel:** {f'<#{log_ch}>' if log_ch else 'none'}")
    archive = int(db.setting(g, "archive_days", 0))
    lines.append(f"**Delete old raid posts:** {f'{plural(archive, 'day')} after they start' if archive else 'never'}")
    scoped: dict[int, dict] = {}
    for key, val in db.settings_with_prefix(g).items():
        for prefix, label in (("ping_role@", "ping"), ("officer_role@", "officers")):
            if key.startswith(prefix):
                scoped.setdefault(int(key[len(prefix):]), {})[label] = val
    if scoped:
        lines.append("**Channel / category settings:**")
        for cid, d in scoped.items():
            parts = [f"ping <@&{d['ping']}>" if "ping" in d else "", f"officers <@&{d['officers']}>" if "officers" in d else ""]
            lines.append(f"• <#{cid}>: " + ", ".join(p for p in parts if p))
    await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


@config.command(name="channel", description="Defaults for one channel or category: ping role and raid leaders")
@app_commands.describe(target="Channel or category (default: the channel you run this in)",
                       ping="Role pinged for raids posted there",
                       officer="Role that can manage raids there, and only there",
                       clear="Remove this channel's settings")
async def config_channel(interaction: discord.Interaction,
                         target: Optional[Union[discord.TextChannel, discord.CategoryChannel]] = None,
                         ping: Optional[discord.Role] = None, officer: Optional[discord.Role] = None,
                         clear: bool = False):
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
        return await interaction.response.send_message(f"Cleared the settings for {tgt.mention}.",
                                                       ephemeral=True, allowed_mentions=quiet)
    if ping is None and officer is None:
        cur_ping = db.setting(g, f"ping_role@{tgt.id}")
        cur_off = db.setting(g, f"officer_role@{tgt.id}")
        return await interaction.response.send_message(
            f"{tgt.mention}\nPing role: {f'<@&{cur_ping}>' if cur_ping else 'none'}\n"
            f"Officer role: {f'<@&{cur_off}>' if cur_off else 'none'}\n"
            "Set them with the `ping` and `officer` options, or `clear: True` to remove both.",
            ephemeral=True, allowed_mentions=quiet)
    done = []
    if ping is not None:
        db.set_setting(g, f"ping_role@{tgt.id}", ping.id)
        done.append(f"raids posted in {tgt.mention} will ping {ping.mention}")
    if officer is not None:
        db.set_setting(g, f"officer_role@{tgt.id}", officer.id)
        done.append(f"{officer.mention} can manage raids in {tgt.mention} only")
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


@config.command(name="logchannel", description="Channel that gets a line for each raid created, edited or cancelled")
async def config_logchannel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    db.set_setting(interaction.guild_id, "log_channel", channel.id if channel else None)
    await interaction.response.send_message(
        f"Raid activity will be logged in {channel.mention}." if channel else "Raid activity logging is off.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


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
        "`/character list` · `/character main` · `/character remove`"))
    embed.add_field(name="Officers", inline=False, value=(
        "`/raid create` post a raid (optionally ping a role)\n"
        "`/raid edit` change title, time, description or limits\n"
        "`/raid roster` copy-friendly list of who is coming\n"
        "`/raid copy` same raid, new date · `/raid repeat` weekly or every 2 weeks\n"
        "`/raid template save|list|delete` saved setups, then `/raid create template:`\n"
        "`/raid noshow` mark someone who didn't turn up · `/stats team` everyone's attendance\n"
        "`/raid close` · `/raid cancel` · `/raid remove` · `/raid unsigned`"))
    embed.add_field(name="Admins", inline=False, value=(
        "`/config show` · `/config timezone` · `/config reminder`\n"
        "`/config officer` who can manage every raid · `/config pingrole` default role to ping\n"
        "`/config channel` per-channel (or category) ping role and raid leaders\n"
        "`/config threads` discussion thread per raid · `/config archive` delete old posts\n"
        "`/config nudge` ping people who haven't answered · `/config logchannel` activity log\n"
        "`/status` bot health check"))
    embed.set_footer(text="Anyone on the bench is moved in automatically when a slot opens.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        return  # the check already told the user
    name = interaction.command.qualified_name if interaction.command else "?"
    log.error("Command /%s failed", name, exc_info=error)
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
    bot.add_view(RaidView())  # keeps old raid posts working after a restart
    bot.tree.add_command(character)
    bot.tree.add_command(raid)
    bot.tree.add_command(config)
    bot.tree.add_command(stats)
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
        else:
            msg += "\nRun SETUP.bat (or re-extract the download) to create it."
        msg += "\n(Windows tip: in File Explorer turn on View -> Show -> File name extensions, so you can see names like .env.txt.)"
        raise SystemExit(msg)
    print("  Settings: .env found")

    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is empty. Open .env in Notepad, paste your bot token after DISCORD_TOKEN=, and save.\n"
                         "Or double-click SETUP.bat and it will do this for you.")
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    preflight()
    try:
        bot.run(TOKEN, log_handler=None)
    except discord.LoginFailure:
        raise SystemExit("Discord rejected the token at login. Reset it in the Developer Portal and paste the new one into .env.")
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Turn on 'Server Members Intent': Developer Portal -> Bot -> Privileged Gateway Intents -> Save Changes.")
