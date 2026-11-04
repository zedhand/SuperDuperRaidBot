"""First-run wizard, launched by SETUP.bat. Walks through Discord setup and writes .env.

SETUP.bat deletes this file (and itself) once setup succeeds.
"""
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import checks

BASE = Path(__file__).resolve().parent
ENV_PATH = BASE / ".env"
TEMPLATE_PATH = BASE / ".env.example"  # a fresh clone has no .env: it is created from this
OK_FLAG = BASE / ".setup_ok"

# Windows time zone names -> IANA names (for guessing the default)
WIN_TZ = {
    "Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver", "US Mountain Standard Time": "America/Phoenix",
    "Pacific Standard Time": "America/Los_Angeles", "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu", "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin", "Romance Standard Time": "Europe/Paris",
    "Central Europe Standard Time": "Europe/Budapest", "E. Europe Standard Time": "Europe/Bucharest",
    "AUS Eastern Standard Time": "Australia/Sydney", "E. Australia Standard Time": "Australia/Brisbane",
    "Canada Central Standard Time": "America/Regina", "Atlantic Standard Time": "America/Halifax",
    "New Zealand Standard Time": "Pacific/Auckland", "E. South America Standard Time": "America/Sao_Paulo",
    "British Columbia Standard Time": "America/Vancouver",  # BC moved to permanent UTC-7 in 2026
}
# Used when the Windows zone name is unknown or no longer matches the PC's clock: the first of
# these whose UTC offset equals the PC's offset right now is proposed.
COMMON_ZONES = ["America/Vancouver", "America/Los_Angeles", "America/Edmonton", "America/Denver",
                "America/Phoenix", "America/Winnipeg", "America/Chicago", "America/Toronto",
                "America/New_York", "America/Halifax", "America/St_Johns", "Europe/London",
                "Europe/Berlin", "Europe/Paris", "Australia/Sydney", "Pacific/Auckland"]
TZ_CHOICES = ["America/Vancouver", "America/Edmonton", "America/Los_Angeles", "America/Denver",
              "America/Chicago", "America/Toronto", "America/New_York", "Europe/London",
              "Europe/Berlin", "Australia/Sydney"]


def say(text=""):
    print(text, flush=True)


def ask(prompt: str) -> str:
    return input(prompt).strip()


def step(n: int, total: int, title: str):
    say()
    say(f"=== Step {n} of {total}: {title} ===")


def read_env() -> dict:
    values = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def write_env(values: dict):
    """Update the given keys in .env, keeping the comments and any other settings."""
    source = ENV_PATH if ENV_PATH.exists() else TEMPLATE_PATH
    lines = source.read_text(encoding="utf-8").splitlines() if source.exists() else []
    done = set()
    for i, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in values:
                lines[i] = f"{key}={values[key]}"
                done.add(key)
    for key, val in values.items():
        if key not in done:
            lines.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pc_offset():
    """The PC clock's UTC offset right now."""
    return datetime.now().astimezone().utcoffset()


def _offset_of(name: str):
    """An IANA zone's UTC offset right now."""
    from zoneinfo import ZoneInfo
    return datetime.now(timezone.utc).astimezone(ZoneInfo(name)).utcoffset()


def guess_timezone() -> str:
    """Best IANA zone for this PC, or "" if unsure.

    A guess from the Windows zone name is only trusted when it gives the same UTC offset as the PC's
    clock right now, because zones change (British Columbia moved to permanent UTC-7 in 2026, so
    "Pacific" is no longer right for BC). Otherwise the first common zone matching the clock is used."""
    guess = ""
    try:
        out = subprocess.run(["tzutil", "/g"], capture_output=True, text=True, timeout=5).stdout.strip()
        guess = WIN_TZ.get(out, "")
    except Exception:
        pass
    try:
        pc = _pc_offset()
        if guess and _offset_of(guess) == pc:
            return guess
        for name in COMMON_ZONES:
            if _offset_of(name) == pc:
                return name
        return ""
    except Exception:
        return guess


def get_token(existing: str) -> tuple[str, dict]:
    say("1. A browser page is opening. Click 'New Application' and give it any name (e.g. Raid Bot).")
    say("2. In the left sidebar click 'Bot'.")
    say("3. Click 'Reset Token', then 'Copy'.")
    say("4. Come back here and paste it (right-click pastes in this window), then press Enter.")
    webbrowser.open("https://discord.com/developers/applications")
    first = True
    while True:
        prompt = "Paste the bot token here: "
        if first and existing and not checks.token_problem(existing):
            prompt = "Paste the bot token here (or just press Enter to keep the one already saved): "
        token = ask(prompt).strip().strip("\"'")
        if token.lower().startswith("bot "):
            token = token[4:].strip()
        if not token and first and existing:
            token = existing
        first = False
        problem = checks.token_problem(token)
        if problem:
            say(f"  That didn't work: {problem}")
            continue
        say("  Checking with Discord...")
        try:
            me = checks.check_token(token)
        except checks.DiscordError as e:
            if e.status == 401:
                say("  Discord says that token isn't valid. Click 'Reset Token' again and copy the NEW one.")
            else:
                say(f"  {e}. Check your internet connection and paste it again.")
            continue
        say(f"  Token works. Your bot is called '{me['username']}'.")
        return token, me


def wait_members_intent(token: str):
    if checks.members_intent_enabled(token) is not False:
        say("Server Members Intent is already on. Nothing to do.")
        return
    say("On the same Bot page, scroll down to 'Privileged Gateway Intents'.")
    say("Turn ON 'Server Members Intent', then click 'Save Changes'.")
    say("I'll wait and notice when you've done it...")
    waited = 0
    while checks.members_intent_enabled(token) is False:
        time.sleep(3)
        waited += 3
        if waited % 30 == 0:
            say("  ...still waiting. Is the switch on and 'Save Changes' clicked?")
        if waited >= 300:
            say("  Skipping this check. If the bot later complains about intents, come back to this switch.")
            return
    say("  Got it, the intent is on.")


def pick_server(token: str, me: dict) -> int:
    url = checks.invite_url(me["id"])
    guilds = checks.bot_guilds(token)
    if not guilds:
        say("A page is opening to add the bot to your server.")
        say("Pick your server in the dropdown and click 'Continue', then 'Authorize'.")
        say(f"(If no page opens, copy this link into your browser: {url})")
        webbrowser.open(url)
        say("I'll wait until the bot shows up in your server...")
        waited = 0
        while not guilds:
            time.sleep(3)
            waited += 3
            guilds = checks.bot_guilds(token)
            if waited % 30 == 0 and not guilds:
                say("  ...still waiting for you to click Authorize.")
            if waited >= 600:
                raise SystemExit("Timed out waiting for the bot to be added. Run SETUP.bat again.")
    if len(guilds) == 1:
        say(f"  Bot is in '{guilds[0]['name']}'. Using that server.")
        return int(guilds[0]["id"])
    say("The bot is in more than one server. Which one should it use?")
    for i, g in enumerate(guilds, 1):
        say(f"  {i}. {g['name']}")
    while True:
        raw = ask("Number: ")
        if raw.isdigit() and 1 <= int(raw) <= len(guilds):
            return int(guilds[int(raw) - 1]["id"])


def pick_timezone(current: str) -> str:
    from zoneinfo import ZoneInfo
    guess = guess_timezone()
    say("Raid times you type are read in this timezone.")
    if guess:
        say(f"  Your PC looks like it's set to {guess}.")
        if ask(f"  Use {guess}? [Y/n]: ").lower() in ("", "y", "yes"):
            return guess
    for i, tz in enumerate(TZ_CHOICES, 1):
        say(f"  {i}. {tz}")
    say("  Or type any name such as Europe/Paris or Asia/Tokyo.")
    while True:
        raw = ask("Number or name: ")
        name = TZ_CHOICES[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(TZ_CHOICES) else raw
        try:
            ZoneInfo(name)
            return name
        except Exception:
            say("  I don't know that timezone. Try a number from the list, or a name like Europe/Paris.")


def pick_version() -> str:
    say("Which WoW class list should the bot use?")
    say("  1. Retail (current expansion)")
    say("  2. Classic Era")
    while True:
        raw = ask("Number: ")
        if raw == "1":
            return "retail"
        if raw == "2":
            return "classic_era"


def main():
    say("=" * 56)
    say("  RAID BOT SETUP  -  about 3 minutes, no typing of codes")
    say("=" * 56)
    say("I'll open the Discord pages for you and fill in the settings file myself.")
    env = read_env()

    step(1, 5, "Create the bot and copy its token")
    token, me = get_token(env.get("DISCORD_TOKEN", ""))

    step(2, 5, "Allow the bot to see member lists")
    wait_members_intent(token)

    step(3, 5, "Add the bot to your server")
    guild_id = pick_server(token, me)

    step(4, 5, "Timezone")
    tz = pick_timezone(env.get("TIMEZONE", ""))

    step(5, 5, "WoW version")
    version = pick_version()

    write_env({"DISCORD_TOKEN": token, "GUILD_ID": str(guild_id), "TIMEZONE": tz, "WOW_VERSION": version})
    OK_FLAG.write_text("ok", encoding="utf-8")
    say()
    say("All set! Settings saved. This setup file will now delete itself and the bot will start.")
    say("From now on, start the bot with start.bat. Leave its window open.")
    time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit("\nSetup cancelled. Run SETUP.bat again any time.")
