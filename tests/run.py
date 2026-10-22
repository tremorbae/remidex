#!/usr/bin/env python3
"""Runs every offline test (tests/test_*.py), each in its own process, and says which passed.
    python3 tests/run.py
They use made-up data and temporary files: nothing touches your account or your saved data."""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
results = []
for test in sorted(here.glob("test_*.py")):
    done = subprocess.run([sys.executable, str(test)], capture_output=True, text=True, timeout=300)
    lines = [l for l in done.stdout.splitlines() if l.startswith(("ok", "FAIL"))]
    bad = [l for l in lines if l.startswith("FAIL")]
    results.append((test.name, done.returncode == 0 and not bad, len(lines), bad or done.stderr.strip().splitlines()[-1:] if done.returncode else []))
for name, passed, n, why in results:
    print(f"{'PASS' if passed else 'FAIL'}  {name}  ({n} checks)" + ("" if passed else f"\n      {why}"))
print(f"\n{sum(p for _, p, _, _ in results)} of {len(results)} test files passed")
sys.exit(0 if all(p for _, p, _, _ in results) else 1)
