"""Where RemiDex keeps things on this computer: on a server, in the folder REMIDEX_HOME names; on a Mac, in Application
Support and Logs (launchd runs it); on Windows, in %LOCALAPPDATA%\\RemiDex (its keeper runs it: see keeper.py). Nothing
else here, so anything can import it."""

import os
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
if os.environ.get("REMIDEX_HOME"):  # (on a server)
    APP_DIR = Path(os.environ["REMIDEX_HOME"])
    LOG_DIR = APP_DIR / "logs"
    VENV_PYTHON = Path(sys.executable)
elif WINDOWS:
    APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "RemiDex"
    LOG_DIR = APP_DIR / "logs"
    VENV_PYTHON = APP_DIR / "venv" / "Scripts" / "python.exe"
else:
    APP_DIR = Path.home() / "Library/Application Support/remilianet-poke-browser"
    LOG_DIR = Path.home() / "Library/Logs"
    VENV_PYTHON = APP_DIR / "venv/bin/python"
LOG_FILE = LOG_DIR / "remilianet-poke-browser.log"  # what the bot and the feed watcher print
DASH_LOG = LOG_DIR / "remilianet-dashboard.log"
