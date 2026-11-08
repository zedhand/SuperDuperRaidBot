"""Offline tests for v1.13/v1.14: the WoW version presets (more suites are added below as features land).

Run:  python -I tests/test_v113.py        (or python tests/run_all.py for every suite)
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

proj = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
os.environ["DB_PATH"] = str(tmp / "t.db")
os.environ["WOW_VERSION"] = "retail"
os.chdir(proj)
sys.path.insert(0, str(proj))

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {extra}")


import checks  # noqa: E402
import wow_data as wd  # noqa: E402

print("presets")
check("the wizard menu and the preset table list exactly the same versions", list(checks.PRESET_LABELS) == list(wd.PRESETS),
      (list(checks.PRESET_LABELS), list(wd.PRESETS)))
check("all eight WoW versions are present", set(wd.PRESETS) == {"retail", "classic_era", "season_of_discovery", "tbc_classic",
      "wrath_classic", "cata_classic", "mists_classic", "wow_forever"})
for name, preset in wd.PRESETS.items():
    groups = {g for specs in preset.values() for g in specs.values()}
    check(f"{name}: every spec maps to a known group and the raid can field tank, healer and dps",
          groups <= set(wd.GROUPS) and {"tank", "healer"} <= groups and groups & {"melee", "ranged"}, groups)
check("Death Knight arrives with Wrath and Monk with Mists, not before",
      "Death Knight" not in wd.PRESETS["tbc_classic"] and "Death Knight" in wd.PRESETS["wrath_classic"]
      and "Monk" not in wd.PRESETS["cata_classic"] and "Monk" in wd.PRESETS["mists_classic"])
check("Season of Discovery adds its tank options, Era does not have them",
      "Demonology (Tank)" in wd.PRESETS["season_of_discovery"]["Warlock"] and "Demonology (Tank)" not in wd.PRESETS["classic_era"]["Warlock"])
check("a derived preset is a copy: changing one never changes Era",
      wd.PRESETS["tbc_classic"]["Mage"] is not wd.PRESETS["classic_era"]["Mage"])
check("utility entries only name classes that exist in their preset",
      all(c in wd.PRESETS[n] for n, u in wd.UTILITY_BY_PRESET.items() for _, cl in u.values() for c in cl if n != "retail"))
check("the utility table has an entry for every preset", set(wd.UTILITY_BY_PRESET) == set(wd.PRESETS))

for name in wd.PRESETS:
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); import wow_data as w;" % str(proj) + " print(w.PRESET_NAME, len(w.class_names()))"],
                       cwd=proj, env={**os.environ, "WOW_VERSION": name}, capture_output=True, text=True)
    check(f"WOW_VERSION={name} loads", r.returncode == 0 and r.stdout.split()[0] == name, r.stderr[-200:])
r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); import wow_data" % str(proj)], cwd=proj, env={**os.environ, "WOW_VERSION": "nope"},
                   capture_output=True, text=True)
check("an unknown version stops with the list of valid ones", r.returncode != 0 and "wow_forever" in (r.stderr + r.stdout))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
