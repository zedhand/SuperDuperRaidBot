"""Update.bat / update.ps1 against a fake GitHub served from localhost (Windows PowerShell only).

Run:  python -I tests/test_update.py
"""
import hashlib
import http.server
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

proj = Path(__file__).resolve().parent.parent
if not shutil.which("powershell"):
    print("powershell not available here: skipping the updater tests")
    sys.exit(0)

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if not cond else ""))
    failed += (not cond)


def make_zip(version: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Bot/bot.py", f'VERSION = "{version}"\n')
        z.writestr("Bot/checks.py", f"# checks {version}\n")
        z.writestr("Bot/requirements.txt", "discord.py\n")
        z.writestr("Bot/README.md", f"readme {version}\n")
        z.writestr("Bot/icons/README.txt", "icons help\n")
        z.writestr("Bot/.env", "DISCORD_TOKEN=SHOULD-NEVER-BE-COPIED\n")      # a release must never overwrite settings
        z.writestr("Bot/raidbot.db", "SHOULD-NEVER-BE-COPIED")
    return buf.getvalue()


NEW_ZIP = make_zip("9.9")
GOOD_SHA = hashlib.sha256(NEW_ZIP).hexdigest()
state = {"port": 0}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        base = f"http://127.0.0.1:{state['port']}"
        if self.path in ("/api", "/bad", "/same", "/nodigest", "/weird"):
            tag = {"/same": "v1.0", "/weird": "vnot-a-version"}.get(self.path, "v9.9")
            digest = "sha256:" + ("0" * 64 if self.path == "/bad" else GOOD_SHA)
            asset = {"name": "Bot-9.9.zip", "browser_download_url": base + "/asset.zip", "digest": digest}
            if self.path == "/nodigest":
                del asset["digest"]  # GitHub's API sometimes omits it
            body = json.dumps({"tag_name": tag, "assets": [asset]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
        elif self.path == "/asset.zip":
            body = NEW_ZIP
            self.send_response(200); self.send_header("Content-Type", "application/zip")
        else:
            body = b"nope"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
state["port"] = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()


def fresh_install() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "bot.py").write_text('VERSION = "1.0"\n')
    (d / "checks.py").write_text("# checks 1.0\n")
    (d / "requirements.txt").write_text("discord.py\n")
    (d / ".env").write_text("DISCORD_TOKEN=my-real-token\n")
    (d / "raidbot.db").write_text("my-real-data")
    (d / "icons").mkdir()
    (d / "icons" / "warrior.png").write_text("my own icon")
    shutil.copy(proj / "update.ps1", d / "update.ps1")
    return d


def run_update(d: Path, route: str, env=None):
    import os
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(d / "update.ps1"),
                        "-ApiUrl", f"http://127.0.0.1:{state['port']}{route}", "-Yes", "-SkipDeps"],
                       capture_output=True, text=True, timeout=120, env={**os.environ, **(env or {})})
    return p.returncode, p.stdout + p.stderr


print("successful update")
d = fresh_install()
code, out = run_update(d, "/api")
check("exit code 0 and a clear message", code == 0 and "Updated 1.0 -> 9.9" in out, out)
check("program files replaced", 'VERSION = "9.9"' in (d / "bot.py").read_text() and "checks 9.9" in (d / "checks.py").read_text())
check("settings file untouched (even though the zip contained one)", (d / ".env").read_text() == "DISCORD_TOKEN=my-real-token\n")
check("database untouched (even though the zip contained one)", (d / "raidbot.db").read_text() == "my-real-data")
check("user's own icons untouched, icons help file added", (d / "icons" / "warrior.png").read_text() == "my own icon" and (d / "icons" / "README.txt").exists())
backups = list((d / "backups").glob("code-1.0-*"))
check("previous code backed up", len(backups) == 1 and 'VERSION = "1.0"' in (backups[0] / "bot.py").read_text())
check("checksum reported OK", "Checksum OK" in out)

print("odd PowerShell module path (best effort: the real failure only reproduced on the GitHub Windows runner, where Windows PowerShell is started from PowerShell 7)")
d = fresh_install()
code, out = run_update(d, "/api", env={"PSModulePath": tempfile.mkdtemp()})   # does not reproduce it locally; CI is the real check
check("still updates correctly with an unusual module path (updater uses .NET, not Get-FileHash / Expand-Archive)",
      code == 0 and "Updated 1.0 -> 9.9" in out and 'VERSION = "9.9"' in (d / "bot.py").read_text(), out)
check("  and still verifies the checksum", "Checksum OK" in out)

print("already up to date")
d = fresh_install()
code, out = run_update(d, "/same")
check("says so and changes nothing", code == 0 and "already up to date" in out and 'VERSION = "1.0"' in (d / "bot.py").read_text() and not (d / "backups").exists(), out)

print("corrupted download")
d = fresh_install()
code, out = run_update(d, "/bad")
check("refuses on checksum mismatch", code == 1 and "corrupted" in out, out)
check("nothing changed, no backup made", 'VERSION = "1.0"' in (d / "bot.py").read_text() and not (d / "backups").exists())

print("no checksum from GitHub (must not install unverified code silently)")
d = fresh_install()
code, out = run_update(d, "/nodigest")
check("refuses by default", code == 1 and "can't be verified" in out, out)
check("  and changes nothing", 'VERSION = "1.0"' in (d / "bot.py").read_text() and not (d / "backups").exists())
d = fresh_install()
p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(d / "update.ps1"),
                    "-ApiUrl", f"http://127.0.0.1:{state['port']}/nodigest", "-Yes", "-SkipDeps", "-AllowUnverified"],
                   capture_output=True, text=True, timeout=120)
check("installs only with -AllowUnverified, with a warning",
      p.returncode == 0 and "WARNING" in p.stdout and 'VERSION = "9.9"' in (d / "bot.py").read_text(), p.stdout + p.stderr)

print("wrong folder")
empty = Path(tempfile.mkdtemp())
shutil.copy(proj / "update.ps1", empty / "update.ps1")
code, out = run_update(empty, "/api")
check("explains it isn't a bot folder", code == 1 and "doesn't look like a bot folder" in out, out)

print("server unreachable")
d = fresh_install()
p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(d / "update.ps1"),
                    "-ApiUrl", "http://127.0.0.1:1/api", "-Yes", "-SkipDeps"], capture_output=True, text=True, timeout=120)
check("fails politely and changes nothing", p.returncode == 1 and "Update failed" in (p.stdout + p.stderr)
      and 'VERSION = "1.0"' in (d / "bot.py").read_text())

print("a release number that can't be read")
d = fresh_install()
code, out = run_update(d, "/weird")
check("is never treated as newer (no surprise downgrade)", code == 0 and "already up to date" in out
      and 'VERSION = "1.0"' in (d / "bot.py").read_text(), out)

print("the bot is still running")
d = fresh_install()
held = open(d / "raidbot.db.lock", "a+")  # what the running bot keeps open
try:
    code, out = run_update(d, "/api")
finally:
    held.close()
if sys.platform == "win32":
    # Update.bat is Windows-only, and the check relies on Windows refusing to open a file another process has open.
    # PowerShell on macOS/Linux can't see that, so there this part only proves the updater still runs.
    check("refuses and changes nothing", code == 1 and "still running" in out
          and 'VERSION = "1.0"' in (d / "bot.py").read_text(), out)
    code, out = run_update(d, "/api")
check("once it's closed the update goes ahead", code == 0 and 'VERSION = "9.9"' in (d / "bot.py").read_text(), out)
check("setup.py isn't brought back after first-run setup deleted it", not (d / "setup.py").exists())

server.shutdown()
print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
sys.exit(1 if failed else 0)
