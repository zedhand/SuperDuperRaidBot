"""Offline tests for the release builder (.github/scripts/build_release.py): what goes in the zip, and the leak scan.

Run:  python -I tests/test_release.py        (or python tests/run_all.py for every suite)
"""
import importlib.util
import io
import sys
import tempfile
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

proj = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_release", proj / ".github" / "scripts" / "build_release.py")
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if not cond else ""))
    failed += (not cond)


tmp = Path(tempfile.mkdtemp())
ver = br.version()
with redirect_stdout(io.StringIO()) as out:
    rc = br.main(["--out-dir", str(tmp), "--notes", str(tmp / "notes.md")])
zpath = tmp / f"SuperDuperRaidBot-v{ver}.zip"
check("builds a clean zip from the repo", rc == 0 and zpath.exists(), out.getvalue())
names = set(zipfile.ZipFile(zpath).namelist()) if zpath.exists() else set()
check("everything sits in one SuperDuperRaidBot/ folder", names and all(n.startswith("SuperDuperRaidBot/") for n in names))
check("ships a blank .env template", "SuperDuperRaidBot/.env" in names)
check("ships the updater and icons help", {"SuperDuperRaidBot/Update.bat", "SuperDuperRaidBot/update.ps1",
                                           "SuperDuperRaidBot/icons/README.txt"} <= names)
check("leaves out tests, git files and the roadmap",
      not any(s in n for n in names for s in ("tests/", ".github", ".git", "ROADMAP", "CONTRIBUTING")))
check("updater's install list is all in the zip", all(f"SuperDuperRaidBot/{f}" in names for f in
      ["bot.py", "checks.py", "db.py", "embeds.py", "wow_data.py", "setup.py", "start.bat", "requirements.txt",
       "README.md", "LICENSE", "CHANGELOG.md", "SECURITY.md", "Shortcuts.bat"]))
check("release notes come from this version's CHANGELOG section", (tmp / "notes.md").read_text(encoding="utf-8").strip() != "")

with redirect_stdout(io.StringIO()):
    check("a tag that doesn't match VERSION is refused", br.main(["--tag", "v0.0.1", "--out-dir", str(tmp)]) == 1)
    check("the matching tag is accepted", br.main(["--tag", f"v{ver}", "--out-dir", str(tmp)]) == 0)


def planted(extra: dict) -> list:
    p = tmp / "bad.zip"
    with zipfile.ZipFile(zpath) as src, zipfile.ZipFile(p, "w") as z:
        for n in src.namelist():
            if n not in extra:
                z.writestr(n, src.read(n))
        for n, data in extra.items():
            z.writestr(n, data)
    return br.scan(p)


fake_token = "M" + "T" * 25 + ".Gabcde." + "x" * 38
bad = {
    "a filled-in token in .env": {"SuperDuperRaidBot/.env": "DISCORD_TOKEN=abc\nGUILD_ID=\n"},
    "a filled-in server ID in .env": {"SuperDuperRaidBot/.env": "DISCORD_TOKEN=\nGUILD_ID=123\n"},
    "a bot token in code": {"SuperDuperRaidBot/bot.py": f'TOKEN = "{fake_token}"\n'},
    "a Discord ID": {"SuperDuperRaidBot/README.md": "server 112233445566778899 here\n"},
    "a webhook URL": {"SuperDuperRaidBot/README.md": "https://discord.com/api/webhooks/1/abc-DEF\n"},
    "a personal Windows path": {"SuperDuperRaidBot/README.md": r"see C:\Users\Dez\Desktop" + "\n"},
    "a personal home path": {"SuperDuperRaidBot/README.md": "see /home/dez/bot\n"},
    "a database file": {"SuperDuperRaidBot/raidbot.db": "x"},
    "a log file": {"SuperDuperRaidBot/raidbot.log": "x"},
    "icon artwork": {"SuperDuperRaidBot/icons/mage.jpg": "x"},
    "a backup": {"SuperDuperRaidBot/backups/raidbot-1.db": "x"},
    "an unlisted file": {"SuperDuperRaidBot/notes.txt": "hello"},
}
for label, extra in bad.items():
    check(f"scan catches {label}", planted(extra) != [])
check("generic example paths are allowed",
      planted({"SuperDuperRaidBot/README.md": "/home/youruser/SuperDuperRaidBot and C:\\RaidBot\n"}) == [])

print(f"\n{'ALL PASSED' if not failed else f'{failed} FAILED'}")
sys.exit(1 if failed else 0)
