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
_real_guess = S.guess_timezone
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
check("bad timezone rejected, menu choice used", env["TIMEZONE"] == S.TZ_CHOICES[0])
check("WoW version saved", env["WOW_VERSION"] == "retail")
check("template comments and extra settings kept", "REMINDER_MINUTES" in env and "Never share this file" in S.ENV_PATH.read_text(encoding="utf-8"))
check("setup flag written for SETUP.bat", S.OK_FLAG.exists())
check("all scripted answers were used", next(answers, None) is None)

print("timezone guess (offsets faked, so results don't depend on today's date)")
from datetime import timedelta
import types as _t

OFFSETS = {"America/Vancouver": -7, "America/Los_Angeles": -8, "America/Edmonton": -7, "America/Denver": -7,
           "America/Phoenix": -7, "America/Winnipeg": -6, "America/Chicago": -6, "America/Toronto": -5,
           "America/New_York": -5, "America/Halifax": -4, "America/St_Johns": -3.5, "Europe/London": 0,
           "Europe/Berlin": 1, "Europe/Paris": 1, "Australia/Sydney": 11, "Pacific/Auckland": 13}
S._offset_of = lambda name: timedelta(hours=OFFSETS[name])


def guess(windows_name, pc_hours):
    S._pc_offset = lambda: timedelta(hours=pc_hours)
    if windows_name is None:
        def boom(*a, **k):
            raise FileNotFoundError("no tzutil here")
        S.subprocess.run = boom
    else:
        S.subprocess.run = lambda *a, **k: _t.SimpleNamespace(stdout=windows_name + "\n")
    return _real_guess()


check("BC's new Windows zone -> America/Vancouver", guess("British Columbia Standard Time", -7) == "America/Vancouver")
check("a PC in BC whose zone says Pacific but whose clock says UTC-7 is NOT sent to Los Angeles",
      guess("Pacific Standard Time", -7) == "America/Vancouver")
check("a real Pacific PC (UTC-8) still gets Los Angeles", guess("Pacific Standard Time", -8) == "America/Los_Angeles")
check("Mountain zone name that matches the clock is kept (Denver, not the first -7 zone)",
      guess("Mountain Standard Time", -7) == "America/Denver")
check("unknown Windows zone name -> falls back to the clock offset",
      guess("Some Future Standard Time", -5) in ("America/Toronto", "America/New_York"))
check("non-Windows (no tzutil) -> clock offset still finds a zone", guess(None, 1) in ("Europe/Berlin", "Europe/Paris"))
check("a clock offset nothing matches -> no confident guess (user picks)", guess("Some Future Standard Time", 5.75) == "")


def unreadable():
    raise RuntimeError("clock unreadable")


S._pc_offset = unreadable
S.subprocess.run = lambda *a, **k: _t.SimpleNamespace(stdout="Eastern Standard Time\n")
check("if the clock can't be read, falls back to the name-based guess", _real_guess() == "America/New_York")

print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
sys.exit(1 if failed else 0)
