"""Helpers for the offline tests: the bot is loaded without a browser, and names (a file's path, a function to stub, the
clock) are replaced everywhere they're used, whichever part of the bot uses them."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    """The bot (poke_browser), with Playwright stubbed out. Nothing here touches the network or your data."""
    sys.modules.setdefault("playwright", types.ModuleType("playwright"))
    sync = types.ModuleType("playwright.sync_api")
    sync.Error, sync.sync_playwright = Exception, None
    sys.modules["playwright.sync_api"] = sync
    sys.path.insert(0, str(ROOT))
    import poke_browser
    import store
    import tempfile
    away = Path(tempfile.mkdtemp())
    store.DB_FILE = away / "test.db"  # never your real history
    # nor your chat, what you wrote in it, or whether your dashboard is open (a test can still point them elsewhere)
    patch(CHAT_FILE=away / "chat.json", CHAT_OUT_FILE=away / "chat_out.json", CHAT_FILES=away / "chat_files",
          REMILIA_FEED_FILE=away / "remilia_activity.json", LOOKING_FILE=away / "looking.json", CRAFT_NOW_FILE=away / "craft_now.json")
    return poke_browser


def cards(catalog):
    """Every item in the catalog as the game's card for it would say: what a bag that has found everything knows."""
    return {k: {"category": v.get("category"), "rarity": v.get("tier"), "beetle_name": v.get("name")} for k, v in catalog.items()}


def parts():
    """Every module of the bot that's been loaded."""
    return [m for name, m in list(sys.modules.items()) if m and (name == "poke_browser" or name.startswith("remidex."))]


def patch(**values):
    """Sets each name in every module that has it (a file's path, a function to stub out, the clock...)."""
    for module in parts():
        for name, value in values.items():
            if hasattr(module, name):
                setattr(module, name, value)


class Checks:
    """Prints ok/FAIL lines and remembers the failures."""

    def __init__(self):
        self.failed = []

    def __call__(self, cond, what):
        print(("ok   " if cond else "FAIL ") + what)
        if not cond:
            self.failed.append(what)

    def done(self):
        print(f"FAILED: {self.failed}" if self.failed else "all passed")
        sys.exit(1 if self.failed else 0)
