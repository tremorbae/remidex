"""Runs one of RemiDex's programs the way the tests load the bot: without a browser, and offline (see support.offline).
For the programs a test has the website start (a person's dashboard, say).

    python3 tests/offline.py SCRIPT [its arguments]
"""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402

support.offline()
script, sys.argv = sys.argv[1], sys.argv[1:]
runpy.run_path(script, run_name="__main__")
