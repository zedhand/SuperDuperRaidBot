"""Builds the raid embed from DB rows (Raid-Helper-style layout). No Discord connection needed."""
import discord

import wow_data as wd
from db import caps_dict, waiting_key

ZWS = "​"  # zero-width space: lets a field have an "empty" name

# Discord rejects an embed over 256 characters of title, 4096 of description, or 6000 in total.
TITLE_MAX = 256
DESCRIPTION_MAX = 2000      # our own cap: leaves room for a full roster inside the 6000 total
EMBED_BUDGET = 5800         # what build_embed aims for, under Discord's 6000


def text_problem(title: str | None, description: str | None) -> str | None:
    """A plain-English reason the title/description are too long for a raid post, or None."""
    if title is not None and len(title) > TITLE_MAX:
        return f"That title is {len(title)} characters; the most a raid post can show is {TITLE_MAX}."
    if description is not None and len(description) > DESCRIPTION_MAX:
        return f"That description is {len(description)} characters; the most is {DESCRIPTION_MAX}."
    return None


def _number_map(signups) -> dict[int, int]:
    """Signup order number per user (stable across status changes)."""
    return {s["user_id"]: i for i, s in enumerate(signups, start=1)}


def _note(s, notes: dict | None) -> str:
    """' · *late 15 min*' when /config notes shows notes on the post and this player left one."""
    note = (notes or {}).get(s["user_id"])
    return f" · *{note}*" if note else ""


def _entry(s, num: dict[int, int], realms: dict | None = None, notes: dict | None = None) -> str:
    n = f"`{num[s['user_id']]}`"
    if not s["char_name"]:
        return f"{n} <@{s['user_id']}>" + _note(s, notes)
    realm = (realms or {}).get(s["user_id"])
    return f"{n} {wd.class_emoji(s['wow_class'])} **{s['char_name']}**" + (f" ({realm})" if realm else "") + _note(s, notes)


def _column(entries: list[str]) -> str:
    return "\n".join(entries) if entries else "—"


NL = "\n"


def _clip(text: str, limit: int = 1024) -> str:
    """Shorten to `limit` characters, cutting between entries (never through a @mention)."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    at = max(cut.rfind(NL), cut.rfind(", "))
    return (cut[:at] if at > 0 else cut) + "…"


def _fit(entries: list[str], limit: int, sep: str = NL) -> str:
    """Join whole entries up to `limit` characters, then say how many more there are. Entries are never cut
    (a note with a comma or *italics* stays whole), and nobody disappears without a count."""
    if not entries:
        return "—"
    full = sep.join(entries)
    if len(full) <= limit:
        return full
    out: list[str] = []
    for n, entry in enumerate(entries):
        left_after = len(entries) - n - 1
        if len(sep.join(out + [entry])) + len(f"{sep}…and {left_after} more") > limit:
            return (sep.join(out) + sep if out else "") + f"…and {len(entries) - n} more"
        out.append(entry)
    return sep.join(out)


def _oneline(icon: str, label: str, rows, num, limit: int = 1024, suffix=None, realms=None, notes=None) -> str:
    suffix, realms = suffix or {}, realms or {}
    entries = [
        (f"`{num[s['user_id']]}` **{s['char_name']}**" + (f" ({realms[s['user_id']]})" if s["user_id"] in realms else "")
         if s["char_name"]
         else f"`{num[s['user_id']]}` <@{s['user_id']}>")
        + suffix.get(s["user_id"], "") + _note(s, notes)
        for s in rows]
    head = f"{icon} **{label} ({len(rows)})** : "
    return head + _fit(entries, limit - len(head), ", ")


def composition_warnings(event, attending) -> list[str]:
    """Short notes about a roster that can't work yet. Capped roles report how many are missing;
    uncapped tank/healer only complain when there are none at all."""
    notes = []
    for role, label in (("tank", "tank"), ("healer", "healer")):
        have = sum(1 for s in attending if s["role"] == role)
        cap = event[f"cap_{role}"]
        if cap and have < cap:
            notes.append(f"Need {cap - have} more {label}{'s' if cap - have != 1 else ''} ({have}/{cap})")
        elif not cap and have == 0:
            notes.append(f"No {label} signed up yet")
    return notes


def utility_warnings(attending) -> list[str]:
    """Notes like 'No Bloodlust yet (Shaman, Mage, ...)' for a group big enough to care. Only used when an admin
    has turned on /config buffs; which classes provide what lives in wow_data.UTILITY."""
    if len(attending) < wd.UTILITY_MIN_ATTENDING:
        return []
    have = {s["wow_class"] for s in attending}
    return [f"No {label} yet ({', '.join(classes)})"
            for label, classes in wd.UTILITY.values() if not have & set(classes)]


def build_embed(event, signups, locked: bool, buffs: bool = False, realms: dict | None = None,
                notes: dict | None = None) -> discord.Embed:
    """The raid post. Roster fields are shortened as far as needed to keep the whole embed under
    Discord's 6000-character limit, so a long description plus a full roster can't make it fail.
    realms ({user_id: realm}, only when /config realms is on) adds each character's realm after the name, and
    notes ({user_id: note}, only when /config notes shows them on the post) adds the player's note in italics."""
    limit = 1024
    embed = _build(event, signups, locked, limit, buffs, realms, notes)
    while len(embed) > EMBED_BUDGET and limit > 120:
        limit = int(limit * 0.8)
        embed = _build(event, signups, locked, limit, buffs, realms, notes)
    return embed


def _tanks_and_healers(tanks: list[str], healer_head: str, healers: list[str], limit: int) -> str:
    """Tanks and healers share one field. Healers are always given room (up to half), so a long tank list can
    never push the healer heading and every healer out of the post."""
    room = limit - len(healer_head)
    tank_text = _fit(tanks, room - min(len(_column(healers)), room // 2))
    return tank_text + healer_head + _fit(healers, room - len(tank_text))


def _build(event, signups, locked: bool, limit: int, buffs: bool = False, realms: dict | None = None,
           notes: dict | None = None) -> discord.Embed:
    ts = event["start_ts"]
    num = _number_map(signups)
    embed = discord.Embed(title=_clip(event["title"], TITLE_MAX),
                          description=_clip(event["description"], DESCRIPTION_MAX) if event["description"] else None,
                          colour=discord.Colour.red() if locked else discord.Colour.blurple())

    attending = [s for s in signups if s["status"] in ("in", "late")]

    # Header row: leader / count + time / countdown
    embed.add_field(name=ZWS, value=f"🏳️ <@{event['creator_id']}>\n📅 <t:{ts}:D>", inline=True)
    embed.add_field(name=ZWS, value=f"👥 **{len(attending)}**\n🕒 <t:{ts}:t>", inline=True)
    embed.add_field(name=ZWS, value=f"⏳ <t:{ts}:R>", inline=True)

    # Role columns
    by_group: dict[str, list] = {g: [] for g in wd.GROUPS}
    for s in signups:
        if s["status"] == "in" and s["role"]:
            by_group[wd.group_for(s["wow_class"], s["spec"])].append(s)

    def count(role: str, members: list) -> str:
        cap = event[f"cap_{role}"]
        return f"{len(members)}/{cap}" if cap else str(len(members))

    tanks, healers = by_group["tank"], by_group["healer"]
    melee, ranged = by_group["melee"], by_group["ranged"]
    dps_total = sum(1 for s in attending if s["role"] == "dps")

    embed.add_field(
        name=f"{wd.GROUP_ICON['tank']} Tank ({count('tank', tanks)})",
        value=_tanks_and_healers([_entry(s, num, realms, notes) for s in tanks],
                                 f"\n\n**{wd.GROUP_ICON['healer']} Healer ({count('healer', healers)})**\n",
                                 [_entry(s, num, realms, notes) for s in healers], limit),
        inline=True)
    embed.add_field(name=f"{wd.GROUP_ICON['melee']} Melee ({len(melee)})",
                    value=_fit([_entry(s, num, realms, notes) for s in melee], limit), inline=True)
    embed.add_field(name=f"{wd.GROUP_ICON['ranged']} Ranged ({len(ranged)})",
                    value=_fit([_entry(s, num, realms, notes) for s in ranged], limit), inline=True)

    # Composition warnings (only while signups are open)
    if not locked:
        warnings = composition_warnings(event, attending) + (utility_warnings(attending) if buffs else [])
        if warnings:
            embed.add_field(name=ZWS, value="\n".join(f"⚠️ {w}" for w in warnings), inline=False)

    # One-line lists for the other statuses
    for status, icon, label in (("bench", "🪑", "Bench"), ("late", "⏰", "Late"),
                                ("tentative", "❔", "Tentative"), ("absent", "❌", "Absence")):
        rows = [s for s in signups if s["status"] == status]
        queue = {}
        if status == "bench":  # people the bot benched because a role or class was full: their place in line
            place: dict[str, int] = {}
            for s in rows:
                if s["bench_from"] and s["role"]:
                    key = waiting_key(event, signups, s)
                    place[key] = place.get(key, 0) + 1
                    queue[s["user_id"]] = f" (#{place[key]} {key})"
        if rows:
            embed.add_field(name=ZWS, value=_oneline(icon, label, rows, num, limit, queue, realms, notes), inline=False)

    footer = f"Event #{event['id']}"
    if event["cap_dps"]:
        footer += f" • DPS {dps_total}/{event['cap_dps']}"
    caps = caps_dict(event["class_caps"])
    if caps:
        have = {}
        for s in attending:
            have[s["wow_class"]] = have.get(s["wow_class"], 0) + 1
        footer += " • " + ", ".join(f"{c} {have.get(c, 0)}/{n}" for c, n in sorted(caps.items()))
    if event["repeat_days"]:
        footer += " • 🔁 " + {7: "weekly", 14: "every 2 weeks"}.get(event["repeat_days"], f"every {event['repeat_days']} days")
    embed.set_footer(text=footer)
    return embed
