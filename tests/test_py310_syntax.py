"""Guard: the code must stay valid on Python 3.10 and 3.11, even when developed on a newer Python.

Python 3.12 relaxed f-string rules (PEP 701): a nested string with the same quote as the enclosing f-string, or a
backslash inside {...}, is legal on 3.12+ but a SyntaxError on 3.10/3.11. Developing on a new Python hides that, and the
bot would crash at startup for anyone on an older one. On 3.12+ this test scans the source with the tokenizer and fails on
those patterns; on older Pythons the compile() below is the real check.

Run:  python -I tests/test_py310_syntax.py
"""
import io
import sys
import tokenize
from pathlib import Path

root = Path(__file__).resolve().parent.parent
failed = 0


def check(name, cond, extra=""):
    global failed
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if not cond else ""))
    failed += (not cond)


def delimiter(token_text: str) -> str:
    """The quote characters of a string token, without any prefix (f, r, b, ...)."""
    body = token_text.lstrip("fFrRbBuU")
    return body[:3] if body[:3] in ('"""', "'''") else body[:1]


def pre312_problems(source: str):
    """(line, why) for f-string constructs that only work on Python 3.12+. Needs the 3.12+ tokenizer."""
    problems = []
    stack = []  # delimiters of the f-strings we are currently inside
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        kind = tokenize.tok_name.get(tok.type, "")
        if kind == "FSTRING_START":
            d = delimiter(tok.string)
            if d in stack:
                problems.append((tok.start[0], f"f-string nested inside an f-string that uses the same quote ({d})"))
            stack.append(d)
        elif kind == "FSTRING_END":
            stack.pop()
        elif kind == "STRING" and stack:
            d = delimiter(tok.string)
            if d in stack:
                problems.append((tok.start[0], f"string inside an f-string that uses the same quote ({d})"))
            if "\\" in tok.string:
                problems.append((tok.start[0], "backslash inside an f-string expression"))
    return problems


files = sorted(list(root.glob("*.py")) + list((root / "tests").glob("*.py")))
check("found the source files", len(files) >= 8, [f.name for f in files])

if sys.version_info >= (3, 12):
    # make sure the detector itself works before trusting a clean result
    bad = 'x = 1\nprint(f"{f\'{x[\'a\']}\'}")\nprint(f"{\'\\n\'.join([])}")\n'
    found = pre312_problems(bad)
    check("detector flags nested same-quote f-strings and backslashes in expressions", len(found) >= 2, found)
    good = 'name = "a"\nprint(f"{name} and {len(name)!r:>3} {\'ok\'}")\n'
    check("detector accepts valid 3.10-style f-strings", pre312_problems(good) == [], pre312_problems(good))
    for path in files:
        found = pre312_problems(path.read_text(encoding="utf-8"))
        check(f"{path.name}: nothing that needs Python 3.12+", not found, "; ".join(f"line {n}: {why}" for n, why in found))
else:
    print(f"running on Python {sys.version_info.major}.{sys.version_info.minor}: compiling instead (this is the real 3.10 check)")
    for path in files:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            ok, why = True, ""
        except SyntaxError as e:
            ok, why = False, str(e)
        check(f"{path.name}: compiles on this Python", ok, why)

print(f"\n{'all passed' if not failed else str(failed) + ' failed'}")
sys.exit(1 if failed else 0)
