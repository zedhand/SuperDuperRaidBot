"""Run every test suite, each in its own isolated interpreter.  Usage:  python tests/run_all.py"""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
suites = sorted(p for p in here.glob("test_*.py"))
failed = []
for suite in suites:
    print(f"\n=== {suite.name} ===", flush=True)
    result = subprocess.run([sys.executable, "-I", str(suite)], cwd=here.parent)
    if result.returncode:
        failed.append(suite.name)
print("\n" + ("ALL SUITES PASSED" if not failed else "FAILED: " + ", ".join(failed)))
sys.exit(1 if failed else 0)
