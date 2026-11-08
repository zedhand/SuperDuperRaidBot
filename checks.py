"""Discord sanity checks shared by bot.py (startup) and setup.py (first-run wizard).

Uses only the standard library so it works before anything is installed.
"""
import json
import re
import urllib.error
import urllib.request

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/zedhand/SuperDuperRaidBot, 1.9.0)"  # Discord blocks the default Python UA

# View Channels + Send Messages + Embed Links + Read Message History + Use External Emojis
# + Create Public Threads + Send Messages in Threads (for the optional per-raid discussion threads)
PERMISSIONS = 1024 + 2048 + 16384 + 65536 + 262144 + (1 << 35) + (1 << 38)

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
