"""Paths, timings and constants, plus the small helpers every other part uses: log, notify, read_json, write_json."""

import contextlib
import html
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from remidex.places import APP_DIR, LOG_FILE, VENV_PYTHON, WINDOWS  # noqa: F401 (other parts import these from here)


SITE = "https://www.remilia.net"
USER_AGENT = "remilianet-poke (personal script)"
COOKIES_FILE = APP_DIR / "cookies.json"
STATE_FILE = APP_DIR / "state.json"
OVERRIDES_FILE = APP_DIR / "overrides.json"  # the switches, picks and hammer limits set on the dashboard
GAME_FILE = APP_DIR / "game.json"  # the last BeetleBoy state and cards seen, for the dashboard
FEED_US_FILE = APP_DIR / "feed_us.json"  # live news about you from the activity feed (written by --watch-feed)
LOGIN_HELP = "To sign in again, paste the RemiDex install line again (see its README)"
DASH_DIR = APP_DIR / "dashboard"  # your theme, profile picture and item icons, for the dashboard
PEOPLE_PICS = DASH_DIR / "viewers"  # profile pictures for the dashboard's activity: who viewed you, and who was poked
ICONS_PER_RUN = 25  # item icons fetched per run, so a big inventory fills in over a few runs
VIEWS_KEPT = 300


try:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright  # noqa: F401 (other parts import these from here)
except ImportError:
    # Playwright lives in the installer's Python environment; hop into it so `python3 poke_browser.py` just works.
    if VENV_PYTHON.exists() and Path(sys.executable) != VENV_PYTHON:
        if WINDOWS:  # (Windows has no exec: run it there, and finish when it does)
            sys.exit(subprocess.call([str(VENV_PYTHON), *sys.argv]))
        os.execv(VENV_PYTHON, [str(VENV_PYTHON), *sys.argv])
    sys.exit("Playwright isn't installed yet. Run the installer first.")


DELAY = 0.5
BEETLE_RETRY = 15 * 60  # after the game refuses an action, leave it alone this long
# BeetleCraft: crafts toward the Auto makes and quests picked on the dashboard, with recipes the community has found
# (beetle.wiki, BeetleBoy Advance). With none picked, it crafts nothing.
# Item tiers come from the game's own cards; rarity 2 is tin, 3 bronze, 4 mithril, 5 adamantine, 6 diamond.
ASSEMBLE, SMASH = 1, 2
KEY = "beetleboy_key"
HAMMERS = ["hammer_t1", "hammer_t2", "hammer_t3", "hammer_t4", "hammer_t5"]  # Tin to Diamond: the order they're used in unless picked
SACRIFICE = "green"  # what a smash sacrifices: a Green Beetle, the commonest catch...
PURPLE = "purple"  # ...or a Purple Beetle, which is rarer: kept for smashes proven to be low odds (see proven_low_odds)
_HEALTH = {"ok_at": 0, "errors": 0}  # how the site's answering, for the dashboard


def note_health(status):
    """Notes an answer from the site: when it last worked, and how many in a row didn't (no answer or a server error)."""
    if 200 <= status < 500 and status != 429:
        _HEALTH.update(ok_at=time.time(), errors=0)
    else:
        _HEALTH["errors"] += 1


class NeedsLogin(Exception):
    """Something only a new --login can solve; scheduled runs pause."""


def log(message):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def notify(message, subtitle=""):
    """A notification from RemiDex: a Mac one, or on Windows a toast (shown as coming from Windows PowerShell)."""
    if sys.platform == "darwin":
        script = f"display notification {json.dumps(message, ensure_ascii=False)} with title \"RemiDex\"" + (f" subtitle {json.dumps(subtitle, ensure_ascii=False)}" if subtitle else "")
        subprocess.run(["osascript", "-e", script], capture_output=True)
    elif WINDOWS:
        lines = "".join(f"<text>{html.escape(t)}</text>" for t in ("RemiDex", subtitle, message) if t)
        toast = f"<toast><visual><binding template='ToastGeneric'>{lines}</binding></visual></toast>".replace("'", "''")
        script = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
                  "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null; "
                  f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('{toast}'); "
                  "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
                  "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show([Windows.UI.Notifications.ToastNotification]::new($x))")
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def http_error(status, body):
    """An HTTP failure in words, for the log: "HTTP 400 {...}"."""
    return f"HTTP {status} {json.dumps(body)[:120] if body else ''}".strip()


def as_dict(value):
    """value if it's a JSON object (a dict), else an empty one: for the parts of the site's answers the bot reads, so an
    answer of an unexpected shape is like an empty one, not a crash."""
    return value if isinstance(value, dict) else {}


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def write_json(path, data):
    """Atomic write, readable only by you (cookies.json holds the sign-in). Each writer has its own temporary file, so two
    writing at once (the dashboard answers requests side by side) can't take each other's."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}-{threading.get_ident()}.tmp")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # (Windows: someone's reading it this moment; it's free again in a blink)
            if not WINDOWS or attempt == 19:
                raise
            time.sleep(.05)


@contextlib.contextmanager
def editing(path):
    """A lock the bot and the dashboard share, held while one of them reads a file both change (overrides.json,
    profile_edit.json), changes it and writes it back: so neither undoes what the other saved a moment before."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(f"{path.name}.lock"), "a+b") as lock:
        if WINDOWS:
            import msvcrt
            lock.seek(0)
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)  # (waits up to 10 seconds a try)
                    break
                except OSError:
                    continue
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)  # (let go when the file closes)
            yield


LOOKING_FILE = APP_DIR / "dashboard_open.json"  # the dashboard writes this while it's open and visible: someone's looking at it
LOOKING_FOR = 90  # ... and it counts as open this long after its last ask


def someone_looking():
    """Whether the dashboard is open and visible (it says so every few seconds while it is)."""
    return time.time() - (read_json(LOOKING_FILE).get("at") or 0) < LOOKING_FOR


def today(state):
    """Today's poke and friend-request counts, which start again from zero at midnight."""
    day = datetime.now().strftime("%Y-%m-%d")
    if state.get("today", {}).get("day") != day:
        state["today"] = {"day": day, "pokes": 0, "friend_requests": 0}
    return state["today"]


def stamp(path):
    """A file's mark of change (when it was last written and how big), or None if it isn't there."""
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


_SEEN = {}


def changed(path):
    """Whether a file has changed since the last time this asked about it (the first time: no)."""
    now = stamp(path)
    was = _SEEN.setdefault(path, now)
    _SEEN[path] = now
    return now != was


QUEUE_MAX = 9  # Auto makes and quests that can wait in line on the dashboard
JUNK_CUBE_CRAFT = "junk_cube_t1 from @junk + @junk"
