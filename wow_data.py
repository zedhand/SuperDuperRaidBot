"""Class / spec data. Edit this file when a new expansion or spec ships.

Each preset maps  class -> {spec: group}  where group is "tank", "healer", "melee" or "ranged".
Role caps use the broad role: tank, healer, or dps (melee + ranged together).
Pick the active preset with WOW_VERSION in .env (default: retail).
"""
import os

ROLES = ("tank", "healer", "dps")
GROUPS = ("tank", "healer", "melee", "ranged")
ROLE_LABEL = {"tank": "Tanks", "healer": "Healers", "dps": "DPS"}
ROLE_SINGULAR = {"tank": "Tank", "healer": "Healer", "dps": "DPS"}
GROUP_LABEL = {"tank": "Tank", "healer": "Healer", "melee": "Melee DPS", "ranged": "Ranged DPS"}
GROUP_ICON = {"tank": "🛡️", "healer": "💚", "melee": "⚔️", "ranged": "🏹"}

# Optional: map class name -> custom emoji string like "<:warrior:123456789>" (e.g. real class/spec
# icons you upload to your server). Anything not listed falls back to a class-colour square below.
CLASS_EMOJI: dict[str, str] = {}

CLASS_COLOUR_EMOJI = {
    "Death Knight": "🟥", "Demon Hunter": "🟪", "Druid": "🟧", "Evoker": "🟦", "Hunter": "🟩",
    "Mage": "🟦", "Monk": "🟩", "Paladin": "🟪", "Priest": "⬜", "Rogue": "🟨",
    "Shaman": "🟦", "Warlock": "🟪", "Warrior": "🟫",
}

RETAIL = {
    "Death Knight": {"Blood": "tank", "Frost": "melee", "Unholy": "melee"},
    "Demon Hunter": {"Havoc": "melee", "Vengeance": "tank", "Devourer": "ranged"},
    "Druid": {"Balance": "ranged", "Feral": "melee", "Guardian": "tank", "Restoration": "healer"},
    "Evoker": {"Devastation": "ranged", "Preservation": "healer", "Augmentation": "ranged"},
    "Hunter": {"Beast Mastery": "ranged", "Marksmanship": "ranged", "Survival": "melee"},
    "Mage": {"Arcane": "ranged", "Fire": "ranged", "Frost": "ranged"},
    "Monk": {"Brewmaster": "tank", "Mistweaver": "healer", "Windwalker": "melee"},
    "Paladin": {"Holy": "healer", "Protection": "tank", "Retribution": "melee"},
    "Priest": {"Discipline": "healer", "Holy": "healer", "Shadow": "ranged"},
    "Rogue": {"Assassination": "melee", "Outlaw": "melee", "Subtlety": "melee"},
    "Shaman": {"Elemental": "ranged", "Enhancement": "melee", "Restoration": "healer"},
    "Warlock": {"Affliction": "ranged", "Demonology": "ranged", "Destruction": "ranged"},
    "Warrior": {"Arms": "melee", "Fury": "melee", "Protection": "tank"},
}

CLASSIC_ERA = {
    "Druid": {"Balance": "ranged", "Feral (Tank)": "tank", "Feral (DPS)": "melee", "Restoration": "healer"},
    "Hunter": {"Beast Mastery": "ranged", "Marksmanship": "ranged", "Survival": "ranged"},
    "Mage": {"Arcane": "ranged", "Fire": "ranged", "Frost": "ranged"},
    "Paladin": {"Holy": "healer", "Protection": "tank", "Retribution": "melee"},
    "Priest": {"Discipline": "healer", "Holy": "healer", "Shadow": "ranged"},
    "Rogue": {"Assassination": "melee", "Combat": "melee", "Subtlety": "melee"},
    "Shaman": {"Elemental": "ranged", "Enhancement": "melee", "Restoration": "healer"},
    "Warlock": {"Affliction": "ranged", "Demonology": "ranged", "Destruction": "ranged"},
    "Warrior": {"Arms": "melee", "Fury": "melee", "Protection": "tank"},
}

PRESETS = {"retail": RETAIL, "classic_era": CLASSIC_ERA}

# Utility a raid usually wants. Shown as a warning on the post (only if an admin turns it on with /config buffs)
# when nobody who can provide it has signed up. {key: (what to call it, classes that provide it)}.
# Deliberately short and certain; edit it when the game changes. Classic Era has no Bloodlust.
UTILITY_BY_PRESET = {
    "retail": {
        "bloodlust": ("Bloodlust", ("Shaman", "Mage", "Evoker", "Hunter")),
        "battle_res": ("a battle res", ("Druid", "Death Knight", "Warlock", "Paladin")),
    },
    "classic_era": {
        "battle_res": ("a battle res", ("Druid",)),
    },
}
UTILITY_MIN_ATTENDING = 8  # smaller groups aren't nagged about utility

_name = (os.getenv("WOW_VERSION") or "retail").strip().lower()
if _name not in PRESETS:
    raise SystemExit(f"Unknown WOW_VERSION '{_name}'. Options: {', '.join(PRESETS)}")
DATA = PRESETS[_name]
PRESET_NAME = _name  # "retail" or "classic_era"
UTILITY = UTILITY_BY_PRESET.get(_name, {})


def class_names() -> list[str]:
    return list(DATA)


def find_class(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip().lower()
    return next((c for c in DATA if c.lower() == t), None)


def spec_names(cls: str | None) -> list[str]:
    cls = find_class(cls)
    return list(DATA[cls]) if cls else []


def find_spec(cls: str | None, text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip().lower()
    return next((s for s in spec_names(cls) if s.lower() == t), None)


def group_for(cls: str, spec: str) -> str:
    """tank / healer / melee / ranged. Unknown specs (e.g. removed from the list) count as ranged."""
    return DATA.get(cls, {}).get(spec, "ranged")


def role_for(cls: str, spec: str) -> str:
    """Broad role used for caps: tank / healer / dps."""
    g = group_for(cls, spec)
    return g if g in ("tank", "healer") else "dps"


def class_emoji(cls: str | None) -> str:
    return CLASS_EMOJI.get(cls or "") or CLASS_COLOUR_EMOJI.get(cls or "", "▪️")
