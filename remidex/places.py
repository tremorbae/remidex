"""Where RemiDex keeps things. Nothing else here, so anything can import it.

On a Mac (launchd runs it): Application Support and Logs, for the one person using it.
On the website (hosted/web.py runs it): REMIDEX_DATA is the server's disk. Each person's bot, dashboard and sign-in work
in their own folder, DATA/users/<handle> (REMIDEX_HOME names it for each), and what everyone shares (the recipe results,
what the activity feed shows) is in DATA/shared.
REMIDEX_HOME alone (a test) is one person's folder on its own."""

import os
import sys
from pathlib import Path

DATA_DIR = Path(os.environ["REMIDEX_DATA"]) if os.environ.get("REMIDEX_DATA") else None
HOSTED = DATA_DIR is not None  # (the website: many people, each in their own folder)
if os.environ.get("REMIDEX_HOME"):
    APP_DIR = Path(os.environ["REMIDEX_HOME"])
    LOG_DIR = APP_DIR / "logs"
    VENV_PYTHON = Path(sys.executable)
elif HOSTED:
    APP_DIR = DATA_DIR / "site"  # (the website itself: its own pictures and fonts, no account)
    LOG_DIR = APP_DIR / "logs"
    VENV_PYTHON = Path(sys.executable)
else:
    APP_DIR = Path.home() / "Library/Application Support/remilianet-poke-browser"
    LOG_DIR = Path.home() / "Library/Logs"
    VENV_PYTHON = APP_DIR / "venv/bin/python"
LOG_FILE = LOG_DIR / "remilianet-poke-browser.log"  # what the bot and the feed watcher print
SHARED_DIR = DATA_DIR / "shared" if HOSTED else APP_DIR  # what everyone shares (on a Mac, that's just you)
USERS_DIR = DATA_DIR / "users" if HOSTED else None


def homes():
    """Every bot's folder: on the website, everyone's; on a Mac, yours."""
    if not HOSTED:
        return [APP_DIR]
    try:
        return sorted(d for d in USERS_DIR.iterdir() if d.is_dir())
    except OSError:
        return []
