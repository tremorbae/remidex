"""RemiDex's memory: a small SQLite file (remidex.db) next to the bot's other data.

It keeps what grows or changes quickly, which used to live in big JSON files that were rewritten all the time:
  daily      a row per day of the dashboard's counts (pokes, friend requests, hunts, crafts, junk cubes, views and pokes of you)
  viewed     who the bot viewed in the last day (so it doesn't view them again)
  feed_seen  who the activity feed showed lately
  views_seen how many times each person (a notification) has looked at your profile, so growth is counted once
  cheeseman  every line the Cheeseman has had (the activity feed only holds the last day or so)
  meta       small facts (how far into the log it has read, the cheese streak)

The bot and the dashboard both use it (SQLite handles that). The log is read into it once, in order, remembering how far
it got, so a log line is never counted twice; the first time, that fills in the days since the bot started.
"""
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from remidex.places import APP_DIR, SHARED_DIR

DB_FILE = APP_DIR / "remidex.db"
FEED_DB = SHARED_DIR / "remidex.db"  # who the activity feed showed lately: the same for everyone (on a Mac, that's DB_FILE)
VIEW_EVERY = 24 * 3600  # someone viewed is left alone this long
LOG_MOST = 4_000_000  # bytes: past this, the log is trimmed (see trim_log)...
LOG_KEEP = 2_000_000  # ...to about its newest this many
FEED_KEEP = 24 * 3600  # someone the feed showed counts as lately active this long

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, pokes INTEGER NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0,
  hunts INTEGER NOT NULL DEFAULT 0, crafts INTEGER NOT NULL DEFAULT 0, junk_cubes INTEGER NOT NULL DEFAULT 0,
  viewed_you INTEGER NOT NULL DEFAULT 0, pokes_received INTEGER NOT NULL DEFAULT 0, smashes INTEGER NOT NULL DEFAULT 0,
  hammers_broken INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS viewed (handle TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS feed_seen (handle TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS views_seen (id TEXT PRIMARY KEY, count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cheeseman (stamp TEXT NOT NULL, line TEXT NOT NULL, PRIMARY KEY (stamp, line));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
COUNTERS = ("pokes", "requests", "hunts", "crafts", "junk_cubes", "viewed_you", "pokes_received", "smashes", "hammers_broken")

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.*)$")  # a log line: when (local time) and what
STREAK = re.compile(r"^Daily cheese: \+\d+, streak (\d+)")
SMASH_FAILED = re.compile(r"^BeetleCraft: Smash failed: (a|\d+) Green Beetles? lost")  # (yours on RemiliaNET: how many failed is all it shows)
_ready = set()


def connect(path=None):
    """A connection to the database (DB_FILE, or path), made (with its tables) if it isn't there yet."""
    path = path or DB_FILE
    db = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    if str(path) not in _ready:
        path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(8):  # the bot and the feed watcher can start it at the same moment: the loser waits a little
            try:
                db.execute("PRAGMA journal_mode=WAL")  # the bot and the dashboard can use it at the same time
                db.executescript(SCHEMA)
                break
            except sqlite3.OperationalError:
                if attempt == 7:
                    raise
                time.sleep(0.4)
        _ready.add(str(path))
    db.execute("PRAGMA synchronous=NORMAL")
    return db


def get_meta(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(db, key, value):
    db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))


def log_time(when):
    """A log line's time ("2026-10-17 03:35:00", local) as seconds; 0 if it isn't one."""
    try:
        return datetime.fromisoformat(when).timestamp()  # (a lot faster than strptime, and the dashboard reads thousands)
    except (TypeError, ValueError):
        return 0


def smash_count(text):
    """(smashes, hammers broken) a log line tells of: a smash that made something, failed or did NOTHING, by the bot or by
    you on RemiliaNET."""
    if not text.startswith("BeetleCraft: Smash "):
        return 0, 0
    failed = SMASH_FAILED.match(text)
    n = (1 if failed[1] == "a" else int(failed[1])) if failed else int(any(s in text for s in (" -> ", ": smash failed", ": NOTHING HAPPENED")))
    return n, int(bool(n) and text.endswith("The hammer shattered!"))


def add_days(db, days):
    """Adds {day: {counter: n}} to the daily counts."""
    for day, counts in days.items():
        db.execute("INSERT OR IGNORE INTO daily(day) VALUES (?)", (day,))
        for name, n in counts.items():
            if n:
                db.execute(f"UPDATE daily SET {name} = {name} + ? WHERE day = ?", (n, day))  # (name is one of COUNTERS)


def ingest_log(path):
    """Reads whatever has been added to the log since last time into the daily counts, the Cheeseman's lines
    and the streak, and remembers how far it got (in the same transaction, so two readers can't count a line twice). Returns the number of
    lines it read."""
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            size = Path(path).stat().st_size  # (looked at in the lock: one from before the other reader read more would look like a cleared log)
        except OSError:
            db.execute("ROLLBACK")
            return 0
        offset = int(get_meta(db, "log_offset", 0))
        if size < offset:
            offset = 0  # the log was cleared: read what's there now
        if size == offset:
            db.execute("COMMIT")
            return 0
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read()
        complete = chunk.rfind(b"\n") + 1  # a line still being written waits for next time
        days, streak = defaultdict(lambda: defaultdict(int)), None
        handed = []  # the Cheeseman's lines
        lines = 0
        for raw in chunk[:complete].decode("utf-8", "replace").splitlines():
            match = LINE.match(raw)
            if not match:
                continue
            lines += 1
            when, text = match.groups()
            row = days[when[:10]]
            if text.startswith("~"):
                row["pokes"] += ": poked" in text
                row["requests"] += "friend request sent" in text
            elif text.startswith(("Hunt:", "Free beetle:", "Cheeseman:")):
                row["hunts"] += text.startswith("Hunt:")
                if text.startswith("Cheeseman:"):
                    handed.append((when, text))
            elif cheese := STREAK.match(text):
                streak = int(cheese[1])
            elif text.startswith(("BeetleCraft: Assemble", "BeetleCraft: Smash")) and " -> " in text:
                row["crafts"] += 1
                row["junk_cubes"] += bool(re.search(r"-> Junk Cube\b", text))
            smashed, broke = smash_count(text)
            row["smashes"] += smashed
            row["hammers_broken"] += broke
        add_days(db, days)
        db.executemany("INSERT OR IGNORE INTO cheeseman(stamp, line) VALUES (?, ?)", handed)
        if streak is not None:
            set_meta(db, "streak", streak)
        set_meta(db, "log_offset", offset + complete)
        db.execute("COMMIT")
        return lines
    except Exception:
        db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def trim_log(path, most=LOG_MOST, keep=LOG_KEEP):
    """Keeps the log from growing for ever: once it's past `most` bytes, all but its newest `keep` (from the start of a line)
    moves to <log>.old (in place of the one before). Only what's been read in already is cut, and how far it has read moves
    along with it, so nothing is counted twice or missed. The bot and the feed watcher add to the log as they go (it's open
    for appending), so a line they add meanwhile is kept too. Returns whether it trimmed."""
    path = Path(path)
    try:
        if path.stat().st_size <= most:
            return False
    except OSError:
        return False
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        offset = int(get_meta(db, "log_offset", 0))
        with open(path, "r+b") as f:
            data = f.read()
            cut = data.find(b"\n", max(0, len(data) - keep)) + 1
            if cut <= 0 or cut > offset:  # (nothing to cut that's been read in)
                db.execute("ROLLBACK")
                return False
            path.with_name(path.name + ".old").write_bytes(data[:cut])
            f.seek(0)
            f.write(data[cut:])
            f.seek(len(data))
            added = f.read()  # (written while it was busy: after the old end, which is still there)
            f.seek(len(data) - cut)
            f.write(added)
            f.truncate()
        set_meta(db, "log_offset", offset - cut)
        db.execute("COMMIT")
        return True
    except Exception:
        db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def streak():
    """The cheese streak as of the last daily claim, or None (also when the database can't be read)."""
    try:
        db = connect()
        try:
            value = get_meta(db, "streak")
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return int(value) if value else None


def totals():
    """Everything the bot has done since it started: {"hunts", "crafts", ...} summed over the days."""
    db = connect()
    try:
        row = db.execute(f"SELECT {', '.join(f'COALESCE(SUM({c}), 0)' for c in COUNTERS)} FROM daily").fetchone()
    finally:
        db.close()
    return dict(zip(COUNTERS, row))


def record_views(notes, column="viewed_you"):
    """Counts the views of your profile. notes: (id, count, when) for each view notification (a person's count is how many
    times they've looked); only what's grown since last time is added, to the day it was seen (a notification new to this
    database counts on the day it started). The first time, that's everything the bot has saved."""
    if not notes or column not in COUNTERS:
        return
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        today = time.strftime("%Y-%m-%d")
        for note_id, count, at in notes:
            row = db.execute("SELECT count FROM views_seen WHERE id = ?", (note_id,)).fetchone()
            if count > (row[0] if row else 0):
                day = today if row else time.strftime("%Y-%m-%d", time.localtime(at or time.time()))
                db.execute("INSERT OR IGNORE INTO daily(day) VALUES (?)", (day,))
                db.execute(f"UPDATE daily SET {column} = {column} + ? WHERE day = ?", (count - (row[0] if row else 0), day))
                db.execute("INSERT INTO views_seen(id, count) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET count = excluded.count", (note_id, count))
        db.execute("COMMIT")
    except sqlite3.Error:
        db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def day_counts(names, day=None):
    """Some of COUNTERS for a day (today by default), in one look: {name: n}."""
    if not names or any(name not in COUNTERS for name in names):
        raise ValueError(names)
    db = connect()
    try:
        row = db.execute(f"SELECT {', '.join(names)} FROM daily WHERE day = ?", (day or time.strftime("%Y-%m-%d"),)).fetchone()
    finally:
        db.close()
    return dict(zip(names, row or [0] * len(names)))


def cheeseman(before=None, limit=200):
    """The Cheeseman's lines as (when, text), oldest first: the newest `limit`, and only those before `before` (a log time
    "YYYY-MM-DD HH:MM:SS") if given, which is how the feed adds what has scrolled out of its own window."""
    db = connect()
    try:
        rows = db.execute("SELECT stamp, line FROM cheeseman WHERE stamp < ? ORDER BY stamp DESC LIMIT ?", (before or "9999", limit)).fetchall()
    finally:
        db.close()
    return rows[::-1]


# Who the bot has viewed lately, and who the activity feed has shown lately (these used to be JSON files rewritten on
# every change).

_pruned = {"viewed": 0.0, "feed_seen": 0.0}


def _prune(db, table, keep):
    """Drops rows older than `keep` seconds, at most once a minute per table."""
    now = time.time()
    if now - _pruned[table] >= 60:
        _pruned[table] = now
        db.execute(f"DELETE FROM {table} WHERE at <= ?", (now - keep,))


def viewed_lately():
    db = connect()
    try:
        return {h for (h,) in db.execute("SELECT handle FROM viewed WHERE at > ?", (time.time() - VIEW_EVERY,))}
    finally:
        db.close()


def mark_viewed(handle):
    db = connect()
    try:
        db.execute("INSERT OR REPLACE INTO viewed(handle, at) VALUES (?, ?)", (handle, time.time()))
        _prune(db, "viewed", VIEW_EVERY)
    finally:
        db.close()


def feed_touch(handles):
    """Notes that the activity feed just showed these people."""
    now = time.time()
    db = connect(FEED_DB)
    try:
        db.executemany("INSERT OR REPLACE INTO feed_seen(handle, at) VALUES (?, ?)", [(h, now) for h in handles])
        _prune(db, "feed_seen", FEED_KEEP)
    finally:
        db.close()


def feed_recent():
    """{handle: when the feed last showed them} for the last day, newest first."""
    db = connect(FEED_DB)
    try:
        return dict(db.execute("SELECT handle, at FROM feed_seen WHERE at > ? ORDER BY at DESC", (time.time() - FEED_KEEP,)).fetchall())
    finally:
        db.close()
