"""Build the release zip, scan it for anything private, and print its checksum.

Used by .github/workflows/release.yml when a version tag is pushed, and safe to run by hand:

    python .github/scripts/build_release.py              # builds dist/SuperDuperRaidBot-v<VERSION>.zip
    python .github/scripts/build_release.py --tag v1.13.0  # also checks the tag matches bot.py's VERSION
    python .github/scripts/build_release.py --notes notes.md  # also writes this version's CHANGELOG section

The zip mirrors the hand-made releases: one top-level SuperDuperRaidBot/ folder holding the program files, the
user-facing docs, icons/README.txt and a blank .env made from .env.example. Nothing else goes in: no tests, no git
files, no databases, logs, backups or icon artwork. The scan fails the build (exit code 1) if anything outside that
list sneaks in, or if any file holds something that looks like a token, a Discord ID, a filled-in setting or a
personal folder path.
"""
import argparse
import hashlib
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOP = "SuperDuperRaidBot"

# Exactly what a release contains (update.ps1 installs this same set). A new shipped file must be added here.
FILES = [
    "CHANGELOG.md", "DOCKER.md", "LICENSE", "README.md", "SECURITY.md",
    "SETUP.bat", "Shortcuts.bat", "Update.bat", "start.bat", "update.ps1",
    "bot.py", "checks.py", "db.py", "embeds.py", "setup.py", "wow_data.py",
    "requirements.txt", "icons/README.txt",
]
ENV_SOURCE = ".env.example"  # shipped as .env so SETUP.bat has a template to fill in

# --- leak scan -------------------------------------------------------------------------------------------------
TOKEN = re.compile(r"[MNO][A-Za-z0-9_-]{23,27}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,40}")  # Discord bot token shape
SECRETS = [
    ("Discord bot token", TOKEN),
    ("Discord webhook URL", re.compile(r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]+", re.I)),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("personal email address", re.compile(
        r"[A-Za-z0-9._%+-]+@(?:gmail|googlemail|outlook|hotmail|live|msn|yahoo|icloud|me|aol|proton|protonmail|gmx)\.[a-z.]{2,6}\b",
        re.I)),
    ("Discord ID (17-20 digit number)", re.compile(r"(?<![\w.])\d{17,20}(?![\w.])")),
    ("personal Windows folder", re.compile(r"[A-Za-z]:\\+Users\\+(?!Public\\|Default\\|<|%|you|YOUR)[^\\\s\"']+", re.I)),
    ("personal home folder", re.compile(r"/(?:home|Users)/(?!youruser\b|you\b|<|\$)[A-Za-z0-9._-]+")),
]
# Optional private name blocklist: SHA-256 hashes of lower-cased words or two-word pairs (friends' names, Discord
# contacts), whitespace-separated, given in the LEAK_BLOCKLIST environment variable (a repository secret in CI).
# Only the hashes are ever stored, and a match is reported by file and line, never by the word itself.
def load_blocklist() -> set:
    raw = os.environ.get("LEAK_BLOCKLIST", "")
    return {h.lower() for h in raw.split() if re.fullmatch(r"[0-9a-fA-F]{64}", h)}


def blocked_names(line: str, hashes: set) -> int:
    if not hashes:
        return 0
    words = re.findall(r"[A-Za-z0-9]+", line)
    hits = 0
    for i, w in enumerate(words):
        if len(w) >= 3 and hashlib.sha256(w.lower().encode()).hexdigest() in hashes:
            hits += 1
        if i + 1 < len(words) and hashlib.sha256(f"{w} {words[i + 1]}".lower().encode()).hexdigest() in hashes:
            hits += 1
    return hits


# Settings that must ship empty: a value here would be someone's real token or server.
MUST_BE_EMPTY = ("DISCORD_TOKEN", "GUILD_ID")
FORBIDDEN_NAMES = re.compile(r"(^|/)(\.env\.|\.venv/|__pycache__/|backups/|\.git/)|\.(db|log|pyc|sqlite3?)$|"
                             r"^icons/.+\.(png|jpe?g|webp|gif)$", re.I)


def version() -> str:
    m = re.search(r'^VERSION = "([^"]+)"', (ROOT / "bot.py").read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit("bot.py has no VERSION line")
    return m.group(1)


def compose_image_tag() -> str:
    """The version docker-compose.yml downloads (ghcr.io/.../superduperraidbot:<this>), so a release can't point
    the Pi at an older image."""
    m = re.search(r"^\s*image:\s*ghcr\.io/[^\s:]+/superduperraidbot:(\S+)\s*$",
                  (ROOT / "docker-compose.yml").read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else ""


def changelog_section(ver: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(ver)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def build(out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo(TOP + "/", (2026, 1, 1, 0, 0, 0)), "")
        for name in sorted(FILES, key=str.lower):
            src = ROOT / name
            if not src.is_file():
                sys.exit(f"missing release file: {name}")
            z.write(src, f"{TOP}/{name}")
        z.write(ROOT / ENV_SOURCE, f"{TOP}/.env")


def scan(zip_path: Path, hashes=None) -> list:
    """Return a list of problems found in the zip (empty means it is safe to publish)."""
    hashes = load_blocklist() if hashes is None else hashes
    problems = []
    expected = {f"{TOP}/{n}" for n in FILES} | {f"{TOP}/.env"}
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        for name in names:
            inner = name[len(TOP) + 1:] if name.startswith(TOP + "/") else name
            if FORBIDDEN_NAMES.search(inner):
                problems.append(f"{name}: this kind of file must never be released")
            elif name not in expected:
                problems.append(f"{name}: not on the release file list (add it to FILES if it should ship)")
        for name in sorted(expected - set(names)):
            problems.append(f"{name}: missing from the zip")
        for name in names:
            text = z.read(name).decode("utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                for label, rx in SECRETS:
                    if rx.search(line):
                        problems.append(f"{name}:{lineno}: looks like a {label}")
                if blocked_names(line, hashes):
                    problems.append(f"{name}:{lineno}: contains a name from the private blocklist")
            if name.endswith("/.env"):
                for key in MUST_BE_EMPTY:
                    m = re.search(rf"^{key}=(.*)$", text, re.M)
                    if m is None:
                        problems.append(f"{name}: {key}= line is missing")
                    elif m.group(1).strip():
                        problems.append(f"{name}: {key} must be empty in a release")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", help="the pushed git tag; must be v<VERSION from bot.py>")
    ap.add_argument("--out-dir", default=str(ROOT / "dist"))
    ap.add_argument("--notes", help="write this version's CHANGELOG section to this file")
    ap.add_argument("--github-output", help="append zip=, sha256= and version= lines here (GitHub Actions)")
    args = ap.parse_args(argv)

    ver = version()
    if args.tag and args.tag != f"v{ver}":
        print(f"Tag {args.tag} does not match bot.py VERSION {ver} (expected v{ver}). Nothing was built.")
        return 1
    if args.tag and compose_image_tag() != ver:
        print(f"docker-compose.yml uses image tag {compose_image_tag()!r}, not {ver}. Update its image: line before tagging.")
        return 1
    if args.tag and not changelog_section(ver):
        print(f"CHANGELOG.md has no '## {ver}' section. Add one before tagging.")
        return 1

    out = Path(args.out_dir) / f"{TOP}-v{ver}.zip"
    build(out)
    problems = scan(out)
    if problems:
        out.unlink()
        print("Release scan FAILED, the zip was deleted:")
        for p in problems:
            print("  - " + p)
        return 1

    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    names = len(load_blocklist())
    print(f"Built {out} ({out.stat().st_size} bytes)\nsha256 {sha}\nScan: clean"
          + (f" (including {names} blocked names)" if names else " (no LEAK_BLOCKLIST given, so names were not checked)"))
    if args.notes:
        Path(args.notes).write_text(changelog_section(ver) + "\n", encoding="utf-8")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"zip={out}\nsha256={sha}\nversion={ver}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
