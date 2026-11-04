"""Drives setup.py end to end with a scripted user and a fake Discord (no network, no real token).

Run:  python -I tests/test_setup_wizard.py
"""
import builtins
import pathlib
import shutil
import sys
import tempfile
import time
import webbrowser

proj = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(proj))
import checks as C
import setup as S

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if not cond else ""))
    failed += (not cond)


GOOD = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GabcDE." + "x" * 38
d = pathlib.Path(tempfile.mkdtemp())
# a fresh clone has no .env: the wizard must create it from .env.example
S.BASE, S.ENV_PATH, S.OK_FLAG = d, d / ".env", d / ".setup_ok"
S.TEMPLATE_PATH = proj / ".env.example"
webbrowser.open = lambda url: None
time.sleep = lambda s: None
calls = {"intent": 0, "guilds": 0}


def intent(_):
    calls["intent"] += 1
    return calls["intent"] > 2          # switch is off twice, then the user turns it on


def guilds(_):
    calls["guilds"] += 1
    return [] if calls["guilds"] < 3 else [{"id": "555", "name": "Test Server"}]


C.check_token = lambda t: {"id": "42", "username": "RaidBot"}
C.members_intent_enabled = intent
C.bot_guilds = guilds
S.guess_timezone = lambda: ""
answers = iter(["abc", "Bot " + GOOD, "bogus/zone", "1", "1"])   # junk token, real one with 'Bot ' prefix, bad tz, tz #1, retail
asked = []


def fake_input(prompt):
    a = next(answers)
    asked.append(a)
    return a


builtins.input = fake_input
S.main()
env = S.read_env()
check("junk token rejected, real one accepted (and 'Bot ' prefix stripped)", env["DISCORD_TOKEN"] == GOOD)
check("waited for the intent switch and the invite", calls["intent"] > 2 and calls["guilds"] >= 3)
check("server picked automatically", env["GUILD_ID"] == "555")
check("bad timezone rejected, menu choice used", env["TIMEZONE"] == "America/New_York")
check("WoW version saved", env["WOW_VERSION"] == "retail")
check("template comments and extra settings kept", "REMINDER_MINUTES" in env and "Never share this file" in S.ENV_PATH.read_text(encoding="utf-8"))
check("setup flag written for SETUP.bat", S.OK_FLAG.exists())
check("all scripted answers were used", next(answers, None) is None)

print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
sys.exit(1 if failed else 0)
