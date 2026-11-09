"""Checks that every slash command definition obeys Discord's rules (a bad one makes the whole sync fail).

Run:  python -I tests/test_commands.py
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

for group in (B.character, B.raid, B.config, B.stats, B.alerts):
    B.bot.tree.add_command(group)

problems = []


def walk(d, path="", depth=0):
    name = d["name"]
    if not (1 <= len(name) <= 32 and name == name.lower()):
        problems.append(f"bad name: {path}{name}")
    if not (1 <= len(d.get("description", "")) <= 100):
        problems.append(f"bad description length: {path}{name}")
    if depth > 2:
        problems.append(f"nested too deep: {path}{name}")
    seen_optional = False
    for o in d.get("options", []):
        if o["type"] in (1, 2):  # sub-command / sub-command group
            walk(o, f"{path}{name} ", depth + 1)
            continue
        if o.get("required"):
            if seen_optional:
                problems.append(f"required option after optional: {path}{name} {o['name']}")
        else:
            seen_optional = True
        if not (1 <= len(o.get("description", "")) <= 100):
            problems.append(f"bad option description: {path}{name} {o['name']}")
        if len(o.get("choices", [])) > 25:
            problems.append(f"too many choices: {path}{name} {o['name']}")


for group in (B.character, B.raid, B.config, B.stats, B.alerts):
    if len(group.commands) > 25:  # Discord refuses the whole sync past 25 (discord.py raises while loading)
        problems.append(f"/{group.name} has {len(group.commands)} subcommands, the limit is 25")

commands = B.bot.tree.get_commands()
for c in commands:
    walk(c.to_dict(B.bot.tree))
top = {c.name for c in commands}
expected = {"help", "character", "raid", "config", "stats", "myraids", "status", "alerts"}
if not expected <= top:
    problems.append(f"missing commands: {expected - top}")
print(f"{len(commands)} top-level commands checked, {len(problems)} problems")
for p in problems:
    print("  FAIL", p)
sys.exit(1 if problems else 0)
