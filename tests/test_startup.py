"""Startup check (preflight) and token-shape checks, with Discord's answers faked.

Run:  python -I tests/test_startup.py
"""
import os
import sys
import tempfile
from pathlib import Path

proj = Path(__file__).resolve().parent.parent
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "x.db")
os.chdir(proj)
sys.path.insert(0, str(proj))
import bot as B
import checks as C

GOOD = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GabcDE." + "x" * 38
failed = 0

# The checks look for a real .env next to bot.py. Use a private temp folder so these tests never
# depend on (or touch) anyone's actual settings file.
work = Path(tempfile.mkdtemp())
(work / ".env").write_text("DISCORD_TOKEN=placeholder\n", encoding="utf-8")
B.BASE_DIR, B.ENV_PATH = work, work / ".env"


def check(name, cond, extra=""):
    global failed
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if not cond else ""))
    failed += (not cond)


print("token shape")
for token, needle in [("", "empty"), (" abc ", "space"), ('"x"', "quote"), ("Bot abc.def.ghi", "space"),
                      ("a" * 64, "Public Key"), ("b" * 32, "Client Secret"), ("123456789012345678", "ID number"),
                      ("abc.def", "three parts")]:
    msg = C.token_problem(token) or ""
    check(f"{token[:14]!r:18} -> mentions {needle!r}", needle.lower() in msg.lower(), msg)
check("a well-formed token passes", C.token_problem(GOOD) is None)

print("missing or mis-named settings file")
empty = Path(tempfile.mkdtemp())
B.BASE_DIR, B.ENV_PATH = empty, empty / ".env"
try:
    B.preflight(); out = "OK"
except SystemExit as e:
    out = str(e)
check("no .env at all -> says how to create it (SETUP.bat on Windows, python setup.py elsewhere)",
      ".env" in out and "setup" in out.lower(), out)
(empty / "setup.env").write_text("x")
(empty / ".env.txt").write_text("x")
try:
    B.preflight(); out = "OK"
except SystemExit as e:
    out = str(e)
check("lookalike files are named in the message", ".env.txt" in out and "Rename" in out, out)
B.BASE_DIR, B.ENV_PATH = work, work / ".env"

print("wording on Linux/macOS (no Notepad, no .bat files)")
real_windows = B.WINDOWS
B.WINDOWS = False
fresh = Path(tempfile.mkdtemp())          # an empty folder: no .env and no look-alike files
B.BASE_DIR, B.ENV_PATH = fresh, fresh / ".env"
try:
    B.preflight(); out = "OK"
except SystemExit as e:
    out = str(e)
check("missing .env on Linux points to python setup.py, not SETUP.bat",
      "python setup.py" in out and "SETUP.bat" not in out and "File Explorer" not in out, out)
B.BASE_DIR, B.ENV_PATH = work, work / ".env"
B.TOKEN = ""
try:
    B.preflight(); out = "OK"
except SystemExit as e:
    out = str(e)
check("empty token on Linux says 'text editor' and python setup.py", "text editor" in out and "python setup.py" in out
      and "Notepad" not in out and "SETUP.bat" not in out, out)
B.WINDOWS = True
try:
    B.preflight(); out = "OK"
except SystemExit as e:
    out = str(e)
check("empty token on Windows still says Notepad and SETUP.bat", "Notepad" in out and "SETUP.bat" in out, out)
B.WINDOWS = real_windows

print("preflight")
B.TOKEN = GOOD


def run(name, me=None, intent=True, guilds=None, gid=0, expect=None):
    B.GUILD_ID = gid
    C.check_token = lambda t: me or {"id": "42", "username": "RaidBot"}
    C.members_intent_enabled = lambda t: intent
    C.bot_is_public = lambda t: False
    C.bot_guilds = lambda t: guilds if guilds is not None else [{"id": "1", "name": "My Server"}]
    try:
        B.preflight()
        out = "OK"
    except SystemExit as e:
        out = str(e)
    check(name, (expect is None and out == "OK") or (expect is not None and expect in out), out[:100])


run("happy path picks the only server", expect=None)
check("  and sets GUILD_ID", B.GUILD_ID == 1)
run("explicit matching GUILD_ID", gid=1)
run("intent off", intent=False, expect="Server Members Intent")
run("bot in no server", guilds=[], expect="isn't in any server")
run("wrong GUILD_ID", gid=999, expect="isn't a server this bot is in")
run("two servers, blank GUILD_ID, still starts", guilds=[{"id": "1", "name": "A"}, {"id": "2", "name": "B"}])


def rejected(token): raise C.DiscordError(401, "x")


def offline(token): raise C.DiscordError(0, "offline")


C.check_token = rejected
B.GUILD_ID = 0
try:
    B.preflight(); out = "OK"
except SystemExit as e:
    out = str(e)
check("Discord rejects the token -> clear message", "rejected" in out, out)
C.check_token = offline
try:
    B.preflight(); out = "OK"
except SystemExit as e:
    out = str(e)
check("Discord unreachable -> keeps going", out == "OK", out)

print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
sys.exit(1 if failed else 0)
