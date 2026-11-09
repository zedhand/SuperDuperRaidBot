"""Class / spec data. Edit this file when a new expansion or spec ships.

Each preset maps  class -> {spec: group}  where group is "tank", "healer", "melee" or "ranged".
Role caps use the broad role: tank, healer, or dps (melee + ranged together).
Pick the active preset with WOW_VERSION in .env (default: retail). Options: see PRESETS below.
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

def _derive(base, **changes):
    """A copy of a preset with some classes replaced/added (keeps the long lists from being typed out again)."""
    out = {c: dict(specs) for c, specs in base.items()}
    for cls, specs in changes.items():
        out[cls.replace("_", " ")] = specs
    return dict(sorted(out.items()))


# Season of Discovery: the Era classes, plus the tanking/melee options its runes unlock.
SEASON_OF_DISCOVERY = _derive(
    CLASSIC_ERA,
    Hunter={"Beast Mastery": "ranged", "Marksmanship": "ranged", "Survival": "ranged", "Survival (Melee)": "melee"},
    Rogue={"Assassination": "melee", "Combat": "melee", "Combat (Tank)": "tank", "Subtlety": "melee"},
    Shaman={"Elemental": "ranged", "Enhancement": "melee", "Enhancement (Tank)": "tank", "Restoration": "healer"},
    Warlock={"Affliction": "ranged", "Demonology": "ranged", "Demonology (Tank)": "tank", "Destruction": "ranged"},
)

# Burning Crusade Classic: same nine classes as Era (Paladin and Shaman for everyone).
TBC_CLASSIC = _derive(CLASSIC_ERA)

# Wrath of the Lich King Classic and Cataclysm Classic add the Death Knight.
DEATH_KNIGHT = {"Blood": "tank", "Frost": "melee", "Unholy": "melee"}
WRATH_CLASSIC = _derive(CLASSIC_ERA, Death_Knight=DEATH_KNIGHT)
CATACLYSM_CLASSIC = _derive(CLASSIC_ERA, Death_Knight=DEATH_KNIGHT)

# Mists of Pandaria Classic adds the Monk and the Guardian druid spec.
MISTS_CLASSIC = _derive(
    WRATH_CLASSIC,
    Druid={"Balance": "ranged", "Feral": "melee", "Guardian": "tank", "Restoration": "healer"},
    Monk={"Brewmaster": "tank", "Mistweaver": "healer", "Windwalker": "melee"},
)

# World of Warcraft: Forever (Classic+, launched Nov 4 2026): the nine Classic classes. Blizzard says it keeps
# Classic's simple class design and adds options per class; adjust this list if your realm shows new specs.
WOW_FOREVER = _derive(CLASSIC_ERA)

PRESETS = {
    "retail": RETAIL,
    "classic_era": CLASSIC_ERA,
    "season_of_discovery": SEASON_OF_DISCOVERY,
    "tbc_classic": TBC_CLASSIC,
    "wrath_classic": WRATH_CLASSIC,
    "cata_classic": CATACLYSM_CLASSIC,
    "mists_classic": MISTS_CLASSIC,
    "wow_forever": WOW_FOREVER,
}
# Utility a raid usually wants. Shown as a warning on the post (only if an admin turns it on with /config buffs)
# when nobody who can provide it has signed up. {key: (what to call it, classes that provide it)}.
# Deliberately short and certain; edit it when the game changes. Classic Era has no Bloodlust.
_BR_CLASSIC = {"battle_res": ("a battle res", ("Druid",))}
_BL_SHAMAN = {"bloodlust": ("Bloodlust", ("Shaman",))}
UTILITY_BY_PRESET = {
    "retail": {
        "bloodlust": ("Bloodlust", ("Shaman", "Mage", "Evoker", "Hunter")),
        "battle_res": ("a battle res", ("Druid", "Death Knight", "Warlock", "Paladin")),
    },
    "classic_era": dict(_BR_CLASSIC),
    "season_of_discovery": dict(_BR_CLASSIC),
    "tbc_classic": {**_BL_SHAMAN, **_BR_CLASSIC},
    "wrath_classic": {**_BL_SHAMAN, "battle_res": ("a battle res", ("Druid", "Death Knight"))},
    "cata_classic": {"bloodlust": ("Bloodlust", ("Shaman", "Mage")),  # Soulstone works on the dead from 4.0
                     "battle_res": ("a battle res", ("Druid", "Death Knight", "Warlock"))},
    "mists_classic": {"bloodlust": ("Bloodlust", ("Shaman", "Mage", "Hunter")),
                      "battle_res": ("a battle res", ("Druid", "Death Knight", "Warlock"))},
    "wow_forever": dict(_BR_CLASSIC),
}
UTILITY_MIN_ATTENDING = 8  # smaller groups aren't nagged about utility

_name = (os.getenv("WOW_VERSION") or "retail").strip().lower()
if _name not in PRESETS:
    raise SystemExit(f"Unknown WOW_VERSION '{_name}'. Options: {', '.join(PRESETS)}")
DATA = PRESETS[_name]
PRESET_NAME = _name  # a key of PRESETS, e.g. "retail" or "wrath_classic"
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
