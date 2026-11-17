"""Where RemiDex keeps things: the server's disk, REMIDEX_DATA (/data on the server). Nothing else here, so anything can
import it.

Each person's bot, dashboard and sign-in work in their own folder, DATA/users/<handle> (REMIDEX_HOME names it for each
of them); what everyone shares (the recipe results, what the activity feed shows) is in DATA/shared; and the website's
own pictures and fonts are in DATA/site."""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("REMIDEX_DATA") or "/data")
APP_DIR = Path(os.environ["REMIDEX_HOME"]) if os.environ.get("REMIDEX_HOME") else DATA_DIR / "site"  # this one person's folder
LOG_DIR = APP_DIR / "logs"
LOG_FILE = LOG_DIR / "remilianet-poke-browser.log"  # what the bot prints
SHARED_DIR = DATA_DIR / "shared"  # what everyone shares
USERS_DIR = DATA_DIR / "users"


def homes():
    """Everyone's bot folders."""
    try:
        return sorted(d for d in USERS_DIR.iterdir() if d.is_dir())
    except OSError:
        return []
