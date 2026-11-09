"""Discord sanity checks shared by bot.py (startup) and setup.py (first-run wizard).

Uses only the standard library so it works before anything is installed.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://discord.com/api/v10"
# The WoW versions the setup wizard offers, in menu order: {WOW_VERSION value: label}. Must match wow_data.PRESETS.
PRESET_LABELS = {
    "retail": "Retail (current expansion)",
    "classic_era": "Classic Era / Hardcore",
    "season_of_discovery": "Season of Discovery",
    "tbc_classic": "Burning Crusade Classic",
    "wrath_classic": "Wrath of the Lich King Classic",
    "cata_classic": "Cataclysm Classic",
    "mists_classic": "Mists of Pandaria Classic",
    "wow_forever": "World of Warcraft: Forever",
}

USER_AGENT = "DiscordBot (https://github.com/zedhand/SuperDuperRaidBot, 1.13.1)"  # Discord blocks the default Python UA

# View Channels + Send Messages + Embed Links + Read Message History + Use External Emojis
# + Create Public Threads + Send Messages in Threads (for the optional per-raid discussion threads)
# + Manage Messages (pin the /config board), Attach Files (/config backupchannel), Manage Events (/alerts events)
PERMISSIONS = (1024 + 2048 + 16384 + 65536 + 262144 + (1 << 35) + (1 << 38)
               + (1 << 13) + (1 << 15) + (1 << 33))

_MEMBERS_INTENT_FLAGS = (1 << 14) | (1 << 15)  # GATEWAY_GUILD_MEMBERS / _LIMITED


class DiscordError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def api_get(path: str, token: str, timeout: float = 10):
    req = urllib.request.Request(
        API + path, headers={"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise DiscordError(e.code, f"Discord answered HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise DiscordError(0, f"Couldn't reach Discord ({e})") from e


def token_problem(token: str) -> str | None:
    """Return a plain-English reason the token is obviously wrong, or None if it looks fine."""
    if not token:
        return "The token is empty."
    if token != token.strip() or any(c.isspace() for c in token):
        return "The token contains a space or line break. Paste it again with nothing around it."
    if token[0] in "\"'" or token[-1] in "\"'":
        return "The token has quote marks around it. Remove the quotes."
    if token.lower().startswith("bot "):
        return "The token starts with 'Bot '. Paste only the token itself."
    if re.fullmatch(r"[0-9a-f]{64}", token):
        return "That looks like the Public Key, not the bot token. Use Bot -> Reset Token -> Copy."
    if re.fullmatch(r"[0-9a-zA-Z_-]{32}", token):
        return "That looks like the Client Secret, not the bot token. Use Bot -> Reset Token -> Copy."
    if re.fullmatch(r"\d{17,20}", token):
        return "That is an ID number, not the bot token. Use Bot -> Reset Token -> Copy."
    if token.count(".") != 2 or len(token) < 50:
        return ("That doesn't look like a bot token (they have three parts separated by dots and are "
                "about 70 characters long). Use Bot -> Reset Token -> Copy.")
    return None


def check_token(token: str) -> dict:
    """Return the bot's user object. Raises DiscordError(401) when Discord rejects the token."""
    return api_get("/users/@me", token)


def members_intent_enabled(token: str) -> bool | None:
    """True/False, or None if it couldn't be determined."""
    try:
        app = api_get("/applications/@me", token)
    except DiscordError:
        return None
    return bool(app.get("flags", 0) & _MEMBERS_INTENT_FLAGS)


def bot_guilds(token: str) -> list[dict]:
    return api_get("/users/@me/guilds", token)


def invite_url(client_id) -> str:
    return (f"https://discord.com/oauth2/authorize?client_id={client_id}"
            f"&permissions={PERMISSIONS}&scope=bot%20applications.commands")


# ---------------------------------------------------------------- optional Windows shortcuts
SHORTCUT_NAME = "Raid Bot.lnk"


def special_folder(kind: str) -> Path | None:
    """The user's Desktop or Startup folder ('Desktop' / 'Startup'). Asks Windows, so a OneDrive-redirected desktop
    works. RAIDBOT_DESKTOP_DIR / RAIDBOT_STARTUP_DIR override it (used by the tests, never set normally)."""
    override = os.environ.get(f"RAIDBOT_{kind.upper()}_DIR")
    if override:
        return Path(override)
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              f"[Environment]::GetFolderPath('{kind}')"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out) if out else None


def _powershell(script: str, **values: str) -> subprocess.CompletedProcess:
    """Run a PowerShell snippet. Values travel as environment variables, so nothing is ever pasted into the script."""
    env = {**os.environ, **{f"RB_{k.upper()}": v for k, v in values.items()}}
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                          capture_output=True, text=True, timeout=30)


def make_shortcut(link: Path, target: Path, workdir: Path, description: str = "Raid Bot") -> None:
    """Create a Windows shortcut (.lnk). Raises OSError with a plain reason if it couldn't."""
    link.parent.mkdir(parents=True, exist_ok=True)
    result = _powershell("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:RB_LINK); "
                         "$s.TargetPath = $env:RB_TARGET; $s.WorkingDirectory = $env:RB_DIR; "
                         "$s.Description = $env:RB_DESC; $s.Save()",
                         link=str(link), target=str(target), dir=str(workdir), desc=description)
    if result.returncode or not link.exists():
        raise OSError(result.stderr.strip() or "Windows would not create the shortcut")


def read_shortcut(link: Path) -> tuple[str, str]:
    """(target, working folder) of a shortcut."""
    result = _powershell("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:RB_LINK); "
                         "Write-Output $s.TargetPath; Write-Output $s.WorkingDirectory", link=str(link))
    lines = result.stdout.splitlines()
    return (lines[0].strip() if lines else ""), (lines[1].strip() if len(lines) > 1 else "")


def toggle_shortcut(kind: str, base: Path) -> str:
    """Create the shortcut if it isn't there, remove it if it is. kind is 'Desktop' or 'Startup'. Returns a message."""
    folder = special_folder(kind)
    if folder is None:
        return "Shortcuts are only available on Windows."
    link = folder / SHORTCUT_NAME
    what = "the desktop shortcut" if kind == "Desktop" else "starting with Windows"
    if link.exists():
        link.unlink()
        return f"Removed {what}."
    try:
        make_shortcut(link, base / "start.bat", base)
    except OSError as e:
        return f"Couldn't set up {what}: {e}"
    return f"Added {what}."


def shortcuts_menu(base: Path) -> None:
    """Shortcuts.bat: switch the desktop shortcut and 'start with Windows' on or off."""
    if special_folder("Desktop") is None:
        print("This is for Windows. On other systems see the README ('Keep it running 24/7').")
        return
    if not (base / "start.bat").exists():
        print("I can't find start.bat next to this program.")
        return
    while True:
        on = {k: (special_folder(k) / SHORTCUT_NAME).exists() for k in ("Desktop", "Startup")}
        print()
        print("RAID BOT SHORTCUTS")
        print(f"  1. Shortcut on the desktop ................. {'ON' if on['Desktop'] else 'off'}")
        print(f"  2. Start the bot when Windows starts ....... {'ON' if on['Startup'] else 'off'}")
        print("  Type 1 or 2 to switch one, or just press Enter to finish.")
        choice = input("> ").strip()
        if choice not in ("1", "2"):
            return
        print(toggle_shortcut("Desktop" if choice == "1" else "Startup", base))


if __name__ == "__main__" and sys.argv[1:] == ["shortcuts"]:
    shortcuts_menu(Path(__file__).resolve().parent)
