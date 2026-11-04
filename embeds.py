"""Builds the raid embed from DB rows (Raid-Helper-style layout). No Discord connection needed."""
import discord

import wow_data as wd

ZWS = "​"  # zero-width space: lets a field have an "empty" name


def _number_map(signups) -> dict[int, int]:
    """Signup order number per user (stable across status changes)."""
    return {s["user_id"]: i for i, s in enumerate(signups, start=1)}


def _entry(s, num: dict[int, int]) -> str:
    n = f"`{num[s['user_id']]}`"
    if not s["char_name"]:
        return f"{n} <@{s['user_id']}>"
    return f"{n} {wd.class_emoji(s['wow_class'])} **{s['char_name']}**"


def _column(entries: list[str]) -> str:
    return "\n".join(entries) if entries else "—"


def _clip(text: str, limit: int = 1024) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _oneline(icon: str, label: str, rows, num) -> str:
    names = ", ".join(
        f"`{num[s['user_id']]}` **{s['char_name']}**" if s["char_name"] else f"`{num[s['user_id']]}` <@{s['user_id']}>"
        for s in rows)
    return _clip(f"{icon} **{label} ({len(rows)})** : {names}")


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


def build_embed(event, signups, locked: bool) -> discord.Embed:
    ts = event["start_ts"]
    num = _number_map(signups)
    embed = discord.Embed(title=event["title"], description=event["description"] or None,
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
        value=_clip(_column([_entry(s, num) for s in tanks]) +
                    f"\n\n**{wd.GROUP_ICON['healer']} Healer ({count('healer', healers)})**\n" +
                    _column([_entry(s, num) for s in healers])),
        inline=True)
    embed.add_field(name=f"{wd.GROUP_ICON['melee']} Melee ({len(melee)})",
                    value=_clip(_column([_entry(s, num) for s in melee])), inline=True)
    embed.add_field(name=f"{wd.GROUP_ICON['ranged']} Ranged ({len(ranged)})",
                    value=_clip(_column([_entry(s, num) for s in ranged])), inline=True)

    # Composition warnings (only while signups are open)
    if not locked:
        warnings = composition_warnings(event, attending)
        if warnings:
            embed.add_field(name=ZWS, value="\n".join(f"⚠️ {w}" for w in warnings), inline=False)

    # One-line lists for the other statuses
    for status, icon, label in (("bench", "🪑", "Bench"), ("late", "⏰", "Late"),
                                ("tentative", "❔", "Tentative"), ("absent", "❌", "Absence")):
        rows = [s for s in signups if s["status"] == status]
        if rows:
            embed.add_field(name=ZWS, value=_oneline(icon, label, rows, num), inline=False)

    footer = f"Event #{event['id']}"
    if event["cap_dps"]:
        footer += f" • DPS {dps_total}/{event['cap_dps']}"
    if event["repeat_days"]:
        footer += " • 🔁 " + {7: "weekly", 14: "every 2 weeks"}.get(event["repeat_days"], f"every {event['repeat_days']} days")
    embed.set_footer(text=footer)
    return embed
