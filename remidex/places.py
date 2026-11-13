"""Where RemiDex keeps things: on a server (and in the tests), in the folder REMIDEX_HOME names; on a Mac, in Application
Support and Logs (launchd runs it). Nothing else here, so anything can import it."""

import os
import sys
from pathlib import Path

if os.environ.get("REMIDEX_HOME"):
    APP_DIR = Path(os.environ["REMIDEX_HOME"])
    LOG_DIR = APP_DIR / "logs"
    VENV_PYTHON = Path(sys.executable)
else:
    APP_DIR = Path.home() / "Library/Application Support/remilianet-poke-browser"
    LOG_DIR = Path.home() / "Library/Logs"
    VENV_PYTHON = APP_DIR / "venv/bin/python"
LOG_FILE = LOG_DIR / "remilianet-poke-browser.log"  # what the bot and the feed watcher print
