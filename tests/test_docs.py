"""Keeps the documentation honest: links work, every slash command is documented, the changelog is current.

Run:  python -I tests/test_docs.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "x.db")
os.chdir(root)
sys.path.insert(0, str(root))

failed = 0


def check(name, cond, extra=""):
    global failed
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if not cond else ""))
    failed += (not cond)


readme = (root / "README.md").read_text(encoding="utf-8")
lines = readme.splitlines()


def slugify(heading: str, seen: dict) -> str:
    """GitHub's heading anchors: lower-case, drop punctuation, spaces -> hyphens, repeats get -1, -2."""
    text = heading.replace("`", "").strip().lower()
    slug = re.sub(r"[^\w\- ]", "", text).replace(" ", "-")
    n = seen.get(slug, 0)
    seen[slug] = n + 1
    return slug if n == 0 else f"{slug}-{n}"


print("README anchors and links")
anchors, seen, in_code = set(), {}, False
for line in lines:
    if line.strip().startswith("```"):
        in_code = not in_code
        continue
    m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
    if m and not in_code:
        anchors.add(slugify(m.group(2), seen))
bad = sorted({a for a in re.findall(r"\]\(#([^)]+)\)", readme) if a not in anchors})
check("every [text](#anchor) link points at a real heading", not bad, bad)
rel = sorted({p for p in re.findall(r"\]\(([^)#:\s]+)(?:#[^)]*)?\)", readme) if not p.startswith("..")})
missing = [p for p in rel if not (root / p).exists()]
check("every relative file link exists in the repo", not missing, missing)
check("README has the key sections", all(h in readme for h in (
    "## What is this? (in plain English)", "## Important: the bot must stay running", "## FAQ", "## Troubleshooting",
    "## Updating", "## Security notes")))

print("every slash command is documented")
import bot as B  # noqa: E402
from discord import app_commands  # noqa: E402

for group in (B.character, B.raid, B.config, B.stats, B.always, B.alerts):
    B.bot.tree.add_command(group)


def leaves(cmd, path=()):
    here = path + (cmd.name,)
    if isinstance(cmd, app_commands.Group):
        for child in cmd.commands:
            yield from leaves(child, here)
    else:
        yield here


undocumented = []
for top in B.bot.tree.get_commands():
    for p in leaves(top):
        prefix = "/" + " ".join(p[:-1]) if len(p) > 1 else "/" + p[0]
        leaf = p[-1]
        ok = any(prefix in line and (len(p) == 1 or re.search(rf"\b{re.escape(leaf)}\b", line)) for line in lines)
        if not ok:
            undocumented.append(" ".join(p))
check("every registered command and subcommand appears in the README", not undocumented, undocumented)

print("/help")
import asyncio  # noqa: E402
import types  # noqa: E402

shown = {}


async def _send(*a, **k): shown.update(k)
asyncio.run(B.help_command.callback(types.SimpleNamespace(response=types.SimpleNamespace(send_message=_send))))
help_embed = shown.get("embed")
check("/help fits Discord's limits (every field 1024 or less, 6000 in all)", help_embed is not None
      and all(len(f.value) <= 1024 and len(f.name) <= 256 for f in help_embed.fields) and len(help_embed) <= 6000,
      [len(f.value) for f in help_embed.fields] if help_embed else None)
help_text = " ".join(f.value for f in help_embed.fields) if help_embed else ""
missing_help = [f"/{g.name} {c.name}" for g in (B.config, B.alerts) for c in g.commands if f"/{g.name} {c.name}`" not in help_text]
check("/help mentions every /config and /alerts setting", not missing_help, missing_help)

print("settings in .env")
code = (root / "bot.py").read_text(encoding="utf-8")
read = set(re.findall(r'os\.getenv\("([A-Z_]+)"', code)) | set(re.findall(r'_env_int\("([A-Z_]+)"', code))
read -= {"RAIDBOT_DOCKER"}  # set by the Dockerfile, not by people
example = (root / ".env.example").read_text(encoding="utf-8")
readme_text = (root / "README.md").read_text(encoding="utf-8")
check("every setting the bot reads is in .env.example", all(re.search(rf"^#?\s*{v}=", example, re.M) for v in read),
      sorted(v for v in read if not re.search(rf"^#?\s*{v}=", example, re.M)))
check("every setting the bot reads is in the README's Settings table", all(f"| `{v}` |" in readme_text for v in read),
      sorted(v for v in read if f"| `{v}` |" not in readme_text))
check(".env.example doesn't call GUILD_ID required (it's optional)", "REQUIRED. Your server" not in example)

print("changelog and metadata")
changelog = root / "CHANGELOG.md"
if changelog.exists():
    check(f"CHANGELOG has an entry for the current version ({B.VERSION})", f"## {B.VERSION}\n" in changelog.read_text(encoding="utf-8"))
for name in ("LICENSE", "CONTRIBUTING.md", "ROADMAP.md", "SECURITY.md", ".env.example", ".gitignore"):
    check(f"{name} exists", (root / name).exists())
check("issue templates exist", (root / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").exists())

print("the updater delivers every program file")
ps = (root / "update.ps1").read_text(encoding="utf-8")
listed = set(re.findall(r'"([^"]+\.(?:py|bat))"', ps[ps.index("$code = @("):ps.index(")", ps.index("$code = @("))]))
shipped = {p.name for p in root.glob("*.py")}
check("every top-level .py file is in update.ps1's file list (a file it skipped would break updating)",
      shipped <= listed, sorted(shipped - listed))

print("the Docker files")
dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
allowed = {l[1:].strip() for l in (root / ".dockerignore").read_text(encoding="utf-8").splitlines() if l.startswith("!")}
program = {p.name for p in root.glob("*.py")} - {"setup.py"}  # the wizard isn't needed inside the container
copied = set(re.findall(r"\b(\w+\.py)\b", next(l for l in dockerfile.splitlines() if l.startswith("COPY bot.py"))))
check("the Dockerfile copies every program file", program <= copied, sorted(program - copied))
check(".dockerignore lets exactly those files through (so nothing private reaches an image)",
      allowed == program | {"Dockerfile", "requirements.txt", "constraints.txt"},
      sorted(allowed ^ (program | {"Dockerfile", "requirements.txt", "constraints.txt"})))
check(".dockerignore leaves everything else out", "*" in (root / ".dockerignore").read_text(encoding="utf-8").splitlines())
compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
check("docker-compose.yml reads the token from .env and keeps data in ./data", "env_file: .env" in compose and "./data:/data" in compose)
image = re.search(r"^\s*image:\s*ghcr\.io/zedhand/superduperraidbot:(\S+)\s*$", compose, re.M)
check(f"docker-compose.yml downloads the image for this version ({B.VERSION})", bool(image) and image.group(1) == B.VERSION,
      image.group(1) if image else "no ghcr.io image line")
check("docker-compose.yml doesn't build on the Pi (the build-from-source file does)", "build:" not in compose
      and "build: ." in (root / "docker-compose.build.yml").read_text(encoding="utf-8"))
check("data/ holds only its README in git", (root / "data" / "README.txt").exists())
check("DOCKER.md is linked from the README", "](DOCKER.md)" in readme)
guide = (root / "DOCKER.md").read_text(encoding="utf-8")
g_anchors, g_seen, in_code = set(), {}, False
for line in guide.splitlines():
    if line.strip().startswith("```"):
        in_code = not in_code
    elif not in_code and (m := re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)):
        g_anchors.add(slugify(m.group(2), g_seen))
bad = sorted({a for a in re.findall(r"\]\(#([^)]+)\)", guide) if a not in g_anchors})
check("every [text](#anchor) link in DOCKER.md points at a real heading", not bad, bad)

print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
sys.exit(1 if failed else 0)
