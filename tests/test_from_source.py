"""Fresh-install smoke test, the way a brand-new user would do it from source:
copy the files into an empty folder, make a NEW virtualenv, pip-install requirements.txt, then run bot.py and setup.py
with no token and no keyboard. Needs internet (pip). Skipped where Python can't create a virtualenv with pip
(for example Debian without the python3-venv package).

Run:  python -I tests/test_from_source.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

root = Path(__file__).resolve().parent.parent
failed = 0


def check(name, cond, extra=""):
    global failed
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if not cond else ""))
    failed += (not cond)


try:
    import ensurepip  # noqa: F401
except ImportError:
    print("this Python can't create virtualenvs with pip (e.g. Debian without python3-venv): skipping")
    sys.exit(0)

work = Path(tempfile.mkdtemp())
app = work / "app"
app.mkdir()
for pattern in ("*.py", "requirements.txt", ".env.example"):
    for f in root.glob(pattern):
        shutil.copy(f, app / f.name)

print("creating a fresh virtualenv and installing requirements.txt (this takes a little while)")
env_dir = app / ".venv"
venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
py = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
check("virtualenv created", py.exists())
install = subprocess.run([str(py), "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=app,
                         capture_output=True, text=True, timeout=900)
check("pip install -r requirements.txt succeeds", install.returncode == 0, install.stderr[-400:])

clean_env = {k: v for k, v in os.environ.items() if k not in ("DISCORD_TOKEN", "GUILD_ID")}
clean_env["PYTHONIOENCODING"] = "utf-8"


def run(*args, timeout=120):
    p = subprocess.run([str(py), *args], cwd=app, capture_output=True, timeout=timeout, env=clean_env,
                       stdin=subprocess.DEVNULL)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


check("every dependency imports in the fresh environment",
      run("-c", "import discord, dotenv, tzdata; print('ok')")[1].strip() == "ok")

print("first run with a blank .env, as a new user would see it")
shutil.copy(app / ".env.example", app / ".env")
code, out = run("bot.py")
check("bot.py starts, prints its version, and explains the missing token (no traceback)",
      code != 0 and "Raid Bot v" in out and "DISCORD_TOKEN is empty" in out and "Traceback" not in out, out)
check("the message points at setup (SETUP.bat on Windows, python setup.py elsewhere)",
      ("SETUP.bat" if os.name == "nt" else "python setup.py") in out, out)

print("first run with no .env at all")
(app / ".env").unlink()
code, out = run("bot.py")
check("explains the missing .env, no traceback", code != 0 and ".env" in out and "Traceback" not in out, out)

print("setup wizard started with no keyboard")
code, out = run("setup.py")
check("shows the first step, then cancels cleanly (no traceback)",
      "Step 1 of 5" in out and "Setup cancelled" in out and "Traceback" not in out, out)
check("prints the Discord address to open", "discord.com/developers/applications" in out, out)

shutil.rmtree(work, ignore_errors=True)
print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
sys.exit(1 if failed else 0)
