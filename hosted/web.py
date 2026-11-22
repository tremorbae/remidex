#!/usr/bin/env python3
"""RemiDex on the web (remidex.xyz). Anyone signs in with their RemiliaNET account and gets a RemiDex of their own: their
own bot, run on this server, and the dashboard they know. What everyone's crafts teach about recipes is shared.

This one program runs it all:
  - the website: the sign-in page for anyone not signed in to RemiDex, and each person's own dashboard, which it passes
    their requests on to. Each person's dashboard is dashboard.py, run in their own folder on a port of its own: started
    when they visit, stopped a while after they've gone.
  - the scheduler: each person's bot, a round at a time (poke_browser.py --round in their folder), a few at once.
  - one watcher of RemiliaNET's activity feed, for everyone.
Signing in is dashboard.py's own sign-in, run in a new folder of its own; once it has worked, that folder becomes the
person's (or brings their new sign-in into theirs). Their password goes only to RemiliaNET: what's kept is the sign-in
RemiliaNET gives, so their bot can run. RemiDex's own sign-in is a cookie (a random token; only a hash of it is kept).

    REMIDEX_DATA=/data PORT=10000 python3 hosted/web.py
REMIDEX_DATA is the server's disk. REMIDEX_HOSTS: the names the site answers to (comma-separated). REMIDEX_ROUNDS: rounds
at once (else worked out from the server's memory). REMIDEX_SIGN_INS: sign-ins at once.
"""

import hashlib
import http.client
import http.cookies
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ThreadingHTTPServer.request_queue_size = 128

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.setdefault("REMIDEX_DATA", "/data"))
os.environ.setdefault("REMIDEX_ADDRESS", "https://remidex.xyz")
os.environ.pop("REMIDEX_HOME", None)  # (the website's own folder is DATA/site: see remidex/places.py)
sys.path.insert(0, str(ROOT))
import dashboard  # noqa: E402  (the website's logo and fonts, made as each dashboard makes them)
from remidex import loop, recipes, social  # noqa: E402
from remidex.core import editing, log, read_json, write_json  # noqa: E402
from remidex.places import SHARED_DIR, USERS_DIR, homes  # noqa: E402

PORT = int(os.environ.get("PORT") or 10000)
HOSTS = {h.strip().lower() for h in (os.environ.get("REMIDEX_HOSTS") or "remidex.xyz,www.remidex.xyz,remidex.onrender.com").split(",") if h.strip()}
SIGNING_IN_DIR = DATA / "signing-in"  # each sign-in's own folder, until it has worked
SESSIONS_FILE = DATA / "sessions.json"  # RemiDex's sign-ins: {hash of the cookie's token: {"handle", "at", "seen"}}
SCHEDULER_FILE = dashboard.SCHEDULER_FILE  # the scheduler says it's there (each dashboard's green light)
COOKIE, PENDING = "remidex", "remidex_signin"  # RemiDex's sign-in; a sign-in under way (before it has worked)
SESSION_FOR = 30 * 86400  # unused this long, a RemiDex sign-in ends
HANDLE = re.compile(r"^[a-z0-9_]{1,40}$")  # a RemiliaNET handle, as a folder name
# Invite-only: the RemiliaNET handles that may use RemiDex (comma-separated; "*" lets anyone in). Anyone else is turned
# away before typing a password, and isn't let in (nor their bot run) even with an old RemiDex sign-in.
# Invites come from REMIDEX_INVITES (static env var) and Neon (DATABASE_URL, refreshed periodically).
DATABASE_URL = os.environ.get("DATABASE_URL")  # Neon connection string
INVITE_REFRESH = int(os.environ.get("REMIDEX_INVITE_REFRESH") or 60)  # seconds between Neon re-fetches
ADMINS = {h.strip().lstrip("~").lower() for h in (os.environ.get("REMIDEX_ADMINS") or os.environ.get("REMIDEX_INVITES") or "").split(",") if h.strip() and h.strip() != "*"}  # who can manage invites

_invites_lock = threading.Lock()
INVITES = set()  # the invite cache (env + Neon); mutated in place
INVITE_DETAILS = {}  # handle -> {"code": str or None, "redeemed": bool}
_db = None  # Neon connection, lazy


def env_invites():
    """Handles from the REMIDEX_INVITES env var (comma-separated, "*" lets anyone in)."""
    return {h.strip().lstrip("~").lower() for h in (os.environ.get("REMIDEX_INVITES") or "").split(",") if h.strip()}


def db_connect():
    """A Neon connection, or None if DATABASE_URL isn't set or Neon can't be reached."""
    global _db
    if not DATABASE_URL:
        return None
    try:
        import psycopg2
        if _db is None or _db.closed:
            _db = psycopg2.connect(DATABASE_URL)
        return _db
    except ImportError:
        log("Website: psycopg2 not installed; install it to use Neon invites.")
        return None
    except Exception as e:
        log(f"Website: can't reach Neon ({type(e).__name__}: {e}); using cached invites.")
        _db = None
        return None


def ensure_invites_table():
    """Create the invites table in Neon if it isn't there yet, and ensure code & redeemed_at columns exist."""
    global _db
    db = db_connect()
    if not db:
        return
    try:
        with db.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS invites (
                handle TEXT PRIMARY KEY,
                code TEXT,
                redeemed_at TIMESTAMPTZ,
                added_at TIMESTAMPTZ DEFAULT NOW()
            )""")
            cur.execute("ALTER TABLE invites ADD COLUMN IF NOT EXISTS code TEXT")
            cur.execute("ALTER TABLE invites ADD COLUMN IF NOT EXISTS redeemed_at TIMESTAMPTZ")
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        _db = None
        log(f"Website: couldn't create/update invites table ({type(e).__name__}: {e}).")


def load_invites_from_db():
    """All invited handles and details from Neon (or None if Neon couldn't be reached)."""
    global _db
    db = db_connect()
    if not db:
        return None
    try:
        with db.cursor() as cur:
            cur.execute("SELECT handle, code, redeemed_at FROM invites")
            details = {}
            for row in cur.fetchall():
                h = row[0].strip().lstrip("~").lower()
                code = row[1] if row[1] else None
                redeemed = row[2] is not None
                details[h] = {"code": code, "redeemed": redeemed}
            return details
    except Exception as e:
        log(f"Website: couldn't read invites from Neon ({type(e).__name__}: {e}).")
        _db = None
        return None


def refresh_invites():
    """Re-sync the invite cache from Neon (called on startup and periodically)."""
    db_inv = load_invites_from_db()
    with _invites_lock:
        if db_inv is not None:
            INVITES.clear()
            INVITE_DETAILS.clear()
            env_set = env_invites()
            INVITES.update(env_set | set(db_inv.keys()))
            INVITE_DETAILS.update(db_inv)
            for h in env_set:
                INVITE_DETAILS.setdefault(h, {"code": None, "redeemed": True})
        else:
            env_set = env_invites()
            INVITES.update(env_set)
            for h in env_set:
                INVITE_DETAILS.setdefault(h, {"code": None, "redeemed": True})
    log(f"Website: invite list refreshed ({len(INVITES)} invited).")


def invited(handle):
    """Whether this RemiliaNET handle may use RemiDex (see INVITES)."""
    with _invites_lock:
        return "*" in INVITES or str(handle or "").strip().lstrip("~").lower() in INVITES


def generate_invite_code(handle):
    """Generate and store a new one-time invite code for this handle."""
    global _db
    name = str(handle or "").strip().lstrip("~").lower()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    suffix = "".join(secrets.choice(alphabet) for _ in range(6))
    code = f"REMI-{suffix}"
    with _invites_lock:
        INVITES.add(name)
        INVITE_DETAILS[name] = {"code": code, "redeemed": False}
    db = db_connect()
    if db:
        try:
            with db.cursor() as cur:
                cur.execute("""INSERT INTO invites (handle, code, redeemed_at)
                               VALUES (%s, %s, NULL)
                               ON CONFLICT (handle) DO UPDATE SET code = EXCLUDED.code, redeemed_at = NULL""",
                            (name, code))
            db.commit()
            log(f"Website: generated invite code {code} for ~{name}.")
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            _db = None
            log(f"Website: couldn't save invite code for ~{name} to Neon ({e}); kept in cache.")
    return code


def redeem_invite(handle):
    """Marks an invite as redeemed upon successful sign-in."""
    global _db
    name = str(handle or "").strip().lstrip("~").lower()
    with _invites_lock:
        if name in INVITE_DETAILS:
            INVITE_DETAILS[name]["redeemed"] = True
    db = db_connect()
    if db:
        try:
            with db.cursor() as cur:
                cur.execute("UPDATE invites SET redeemed_at = NOW() WHERE handle = %s", (name,))
            db.commit()
            log(f"Website: invite redeemed for ~{name}.")
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            _db = None


def add_invite(handle):
    """Add a handle to the invite list (cache + Neon if configured). Returns whether it worked."""
    global _db
    name = str(handle or "").strip().lstrip("~").lower()
    with _invites_lock:
        INVITES.add(name)
        INVITE_DETAILS[name] = {"code": None, "redeemed": False}
    db = db_connect()
    if not db:
        log(f"Website: invited ~{name} (cache only, no Neon).")
        return True
    try:
        with db.cursor() as cur:
            cur.execute("""INSERT INTO invites (handle, code, redeemed_at)
                           VALUES (%s, NULL, NULL)
                           ON CONFLICT (handle) DO UPDATE SET code = NULL, redeemed_at = NULL""",
                        (name,))
        db.commit()
        log(f"Website: invited ~{name}.")
        return True
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        _db = None
        log(f"Website: couldn't save ~{name} to Neon ({type(e).__name__}: {e}); kept in cache.")
        return True


def remove_invite(handle):
    """Remove a handle from the invite list (cache + Neon if configured). Returns whether it worked."""
    global _db
    name = str(handle or "").strip().lstrip("~").lower()
    with _invites_lock:
        INVITES.discard(name)
        INVITE_DETAILS.pop(name, None)
    sessions.drop(handle=name)
    home = USERS_DIR / name
    if home.exists():
        scheduler.hold(home)
        desks.stop(home)
    db = db_connect()
    if not db:
        log(f"Website: uninvited ~{name} (cache only, no Neon).")
        return True
    try:
        with db.cursor() as cur:
            cur.execute("DELETE FROM invites WHERE handle = %s", (name,))
        db.commit()
        log(f"Website: uninvited ~{name}.")
        return True
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        _db = None
        log(f"Website: couldn't remove ~{name} from Neon ({type(e).__name__}: {e}); kept out of cache.")
        return True


def is_admin(handle):
    """Whether this handle may add or remove invites."""
    return str(handle or "").strip().lower() in ADMINS


def _invite_refresh_loop():
    """Re-fetch invites from Neon every INVITE_REFRESH seconds."""
    while True:
        try:
            time.sleep(INVITE_REFRESH)
            refresh_invites()
        except Exception as e:
            log(f"Website: invite refresh loop: {type(e).__name__}: {e}")


TICK = 2  # seconds between the scheduler's looks: how soon a change on someone's dashboard starts a round
DESK_IDLE = 10 * 60  # a dashboard nobody has asked anything of for this long is stopped (it starts again on the next visit)
DESK_START = 25  # how long a dashboard gets to start (seconds)
SIGN_IN_KEPT = 15 * 60  # a sign-in's folder is kept this long (to show how it went), then cleared away
SIGN_IN_TIME = 8 * 60  # a sign-in counts toward SIGN_INS_MOST this long (it takes about one, plus typing a code)
SIGN_INS_MOST = int(os.environ.get("REMIDEX_SIGN_INS") or 2)  # each opens a browser
SIGN_INS_PER_ADDRESS = (6, 15 * 60)  # sign-ins started from one address, at most, in this many seconds (RemiliaNET's accounts' sake)
ROUND_LIMIT = 30 * 60  # a round still going after this long is stopped (rounds end on their own long before)
LOOK_EVERY = 30  # the housekeeping's pace (seconds)
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authenticate", "proxy-authorization"}
PASSED_ON = ("Content-Type", "Content-Length", "Accept", "Accept-Encoding", "Range", "Last-Event-ID", "Cache-Control")  # what a dashboard is told
HEADERS = (*dashboard.SAFE_HEADERS, ("Strict-Transport-Security", "max-age=31536000"))
DESK_COMMAND = [sys.executable, str(ROOT / "dashboard.py")]  # a person's dashboard (a test runs a stand-in)
ROUND_COMMAND = [sys.executable, str(ROOT / "poke_browser.py"), "--round"]  # a round of a person's bot


def rounds_most():
    """Rounds at once: REMIDEX_ROUNDS, else what the server's memory allows."""
    if os.environ.get("REMIDEX_ROUNDS"):
        return max(1, int(os.environ["REMIDEX_ROUNDS"]))
    try:
        memory = int(Path("/sys/fs/cgroup/memory.max").read_text())
    except (OSError, ValueError):
        return 10
    return max(1, min(50, (memory - 100 * 2 ** 20) // (25 * 2 ** 20)))


def stop(proc):
    """Stops a program this started, and everything it started."""
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sessions:
    """RemiDex's own sign-ins: a random token in a cookie, kept here only as a hash, for SESSION_FOR after it was last used."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        saved = read_json(path)
        self.known = {k: v for k, v in saved.items() if isinstance(v, dict) and isinstance(v.get("handle"), str)}

    @staticmethod
    def key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def save(self):
        write_json(self.path, self.known)

    def new(self, handle):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.known[self.key(token)] = {"handle": handle, "at": time.time(), "seen": time.time()}
            self.save()
        return token

    def who(self, token):
        """Whose sign-in token is (their handle), or None."""
        if not token or len(token) > 100:
            return None
        with self.lock:
            entry = self.known.get(self.key(token))
            if not entry or time.time() - entry["seen"] > SESSION_FOR:
                return None
            if time.time() - entry["seen"] > 3600:  # (noted at most hourly)
                entry["seen"] = time.time()
                self.save()
            return entry["handle"]

    def drop(self, token=None, handle=None):
        """Ends one sign-in (token), or every one someone has (handle)."""
        with self.lock:
            for key in [k for k, v in self.known.items() if (token and k == self.key(token)) or (handle and v["handle"] == handle)]:
                del self.known[key]
            self.save()

    def tidy(self):
        with self.lock:
            old = [k for k, v in self.known.items() if time.time() - v["seen"] > SESSION_FOR]
            for key in old:
                del self.known[key]
            if old:
                self.save()


class Desk:
    """One person's dashboard (dashboard.py in their folder), or a sign-in's."""

    def __init__(self, home):
        self.home, self.port, self.proc = home, free_port(), None
        self.used, self.busy, self.ready = time.time(), 0, threading.Event()


class Desks:
    """The dashboards running now: one per folder, started when asked for, stopped when nobody has used it for a while."""

    def __init__(self):
        self.desks, self.lock = {}, threading.Lock()

    def port(self, home):
        """The port of home's dashboard, started if it isn't running. Raises RuntimeError if it won't start."""
        with self.lock:
            desk = self.desks.get(home)
            if desk is None or (desk.proc and desk.proc.poll() is not None):
                desk = self.desks[home] = Desk(home)
                (home / "logs").mkdir(parents=True, exist_ok=True)
                with open(home / "logs" / "dashboard.log", "ab") as out:
                    desk.proc = subprocess.Popen(DESK_COMMAND, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=out,
                                                 stderr=subprocess.STDOUT, start_new_session=True,
                                                 env={**os.environ, "REMIDEX_HOME": str(home), "REMIDEX_PORT": str(desk.port)})
                threading.Thread(target=self.wait_for, args=(desk,), daemon=True).start()
            desk.used = time.time()
        if not desk.ready.wait(DESK_START) or desk.proc.poll() is not None:
            raise RuntimeError("the dashboard didn't start")
        return desk.port

    @staticmethod
    def wait_for(desk):
        deadline = time.time() + DESK_START
        while time.time() < deadline and desk.proc.poll() is None:
            try:
                socket.create_connection(("127.0.0.1", desk.port), timeout=1).close()
                desk.ready.set()
                return
            except OSError:
                time.sleep(0.2)

    def using(self, home, busy):
        """Notes a request to home's dashboard starting (busy 1) or ending (-1): one that's open (the live connection,
        say) keeps it running."""
        with self.lock:
            if desk := self.desks.get(home):
                desk.busy += busy
                desk.used = time.time()

    def stop(self, home):
        with self.lock:
            desk = self.desks.pop(home, None)
        if desk:
            stop(desk.proc)

    def tidy(self):
        with self.lock:
            idle = [h for h, d in self.desks.items() if d.proc.poll() is not None or (d.busy <= 0 and time.time() - d.used > DESK_IDLE)]
        for home in idle:
            self.stop(home)

    def stop_all(self):
        for home in list(self.desks):
            self.stop(home)


class Scheduler(threading.Thread):
    """Everyone's bots: a round for each when it wants one (see loop.round_wanted), ROUNDS at once at most, everyone in
    turn. Each round is poke_browser.py --round in the person's folder, its output in their log."""

    def __init__(self, most):
        super().__init__(daemon=True)
        self.most, self.rounds, self.held, self.lock, self.turn = most, {}, set(), threading.Lock(), 0

    def running(self, home):
        with self.lock:
            return home in self.rounds

    def hold(self, home, held=True):
        """No round starts for home while it's held (a new sign-in being brought in)."""
        with self.lock:
            (self.held.add if held else self.held.discard)(home)

    def stop(self, home):
        with self.lock:
            proc = self.rounds.pop(home, (None, 0))[0]
        stop(proc)

    def start_round(self, home):
        log_file = home / "logs" / dashboard.LOG_FILE.name
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "ab") as out:
            return subprocess.Popen(ROUND_COMMAND, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True, env={**os.environ, "REMIDEX_HOME": str(home)})

    def look(self):
        """One look: rounds that have ended are let go (one going on too long is stopped), and new ones start."""
        with self.lock:
            for home, (proc, started) in list(self.rounds.items()):
                if proc.poll() is not None:
                    del self.rounds[home]
                elif time.time() - started > ROUND_LIMIT:
                    look_file = home / "dashboard_open.json"
                    if look_file.exists() and loop.someone_looking(look_file):
                        continue  # user still has the app open: let the round continue
                    log(f"Scheduler: stopped a round going on too long (~{home.name}).")
                    stop(proc)
                    del self.rounds[home]
            everyone = homes()
            self.turn = (self.turn + 1) % max(1, len(everyone))
            for home in everyone[self.turn:] + everyone[:self.turn]:  # (everyone gets to go first in turn)
                if len(self.rounds) >= self.most:
                    break
                if home in self.rounds or home in self.held or not invited(home.name):  # (not invited: no rounds)
                    continue
                look_file = home / "dashboard_open.json"
                if look_file.exists() and not loop.someone_looking(look_file):
                    continue  # app closed in browser: no rounds
                try:
                    wanted = loop.round_wanted(home)
                except Exception as e:  # (one person's odd files mustn't stop everyone's bots)
                    log(f"Scheduler: couldn't look at ~{home.name}'s bot ({type(e).__name__}: {e}).")
                    continue
                if wanted:
                    self.rounds[home] = (self.start_round(home), time.time())
        write_json(SCHEDULER_FILE, {"at": time.time(), "rounds": len(self.rounds), "most": self.most})

    def run(self):
        while True:
            try:
                self.look()
            except Exception as e:
                log(f"Scheduler: {type(e).__name__}: {e}")
            time.sleep(TICK)

    def stop_all(self):
        for home in list(self.rounds):
            self.stop(home)


class SignIn:
    """A sign-in under way: its own folder, and the address it came from."""

    def __init__(self, address):
        self.home, self.address, self.at = SIGNING_IN_DIR / secrets.token_hex(12), address, time.time()
        self.home.mkdir(parents=True)


sessions, desks, scheduler = None, Desks(), None
signins, signins_lock, started_from = {}, threading.Lock(), {}  # pending token -> SignIn; address -> [when each sign-in started]


def signed_in(home):
    """Whether home's bot has a RemiliaNET sign-in that works (as far as it knows)."""
    return home is not None and (home / "cookies.json").exists() and not read_json(home / "state.json").get("needs_login")


def welcome(run, handle):
    """A sign-in that worked: its folder becomes the person's bot folder (someone new), or their new sign-in goes into
    theirs (with their switches, history and everything else as it was). Returns their folder, or None."""
    name = handle.lower()
    if not HANDLE.fullmatch(name):
        return None
    home = USERS_DIR / name
    desks.stop(run.home)  # (its dashboard and sign-in are done)
    scheduler.hold(home)
    try:
        new = not home.exists()
        if new:
            USERS_DIR.mkdir(parents=True, exist_ok=True)
            run.home.rename(home)
        else:
            write_json(home / "cookies.json", read_json(run.home / "cookies.json"))
            with editing(home / "state.json"):
                state = read_json(home / "state.json")
                state.pop("needs_login", None)
                state.pop("rest_until", None)  # (straight to a round)
                write_json(home / "state.json", state)
            shutil.rmtree(run.home, ignore_errors=True)
    finally:
        scheduler.hold(home, False)
    redeem_invite(name)
    log(f"Website: ~{name} signed in{' for the first time' if new else ''}.")
    return home


def tidy_sign_ins():
    """Clears away sign-ins long done with: their dashboards stopped, their folders deleted."""
    with signins_lock:
        old = [t for t, r in signins.items() if time.time() - r.at > SIGN_IN_KEPT]
        gone = [signins.pop(t) for t in old]
    for run in gone:
        desks.stop(run.home)
        shutil.rmtree(run.home, ignore_errors=True)
    for folder in SIGNING_IN_DIR.iterdir() if SIGNING_IN_DIR.is_dir() else []:  # (left by a restart)
        if folder not in {r.home for r in signins.values()} and time.time() - folder.stat().st_mtime > SIGN_IN_KEPT:
            shutil.rmtree(folder, ignore_errors=True)
    for address in [a for a, times in started_from.items() if all(time.time() - t > SIGN_INS_PER_ADDRESS[1] for t in times)]:
        del started_from[address]


def housekeeping():
    while True:
        time.sleep(LOOK_EVERY)
        try:
            desks.tidy()
            tidy_sign_ins()
            sessions.tidy()
        except Exception as e:
            log(f"Website: housekeeping: {type(e).__name__}: {e}")


def sign_in_page():
    """The sign-in page (signin.html)."""
    return (ROOT / "signin.html").read_bytes()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # (the log is for what happens, not for every request)

    # ---- who's asking
    def cookies(self):
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie") or "")
        except http.cookies.CookieError:
            return {}
        return {k: m.value for k, m in jar.items()}

    def address(self):
        """Where the request came from: the site's proxies say (Cloudflare, then the host's own)."""
        forwarded = [a.strip() for a in (self.headers.get("X-Forwarded-For") or "").split(",") if a.strip()]
        return self.headers.get("CF-Connecting-IP") or (forwarded[-1] if forwarded else self.client_address[0])

    def session(self):
        """(RemiDex sign-in token, the person's folder), or (None, None)."""
        token = self.cookies().get(COOKIE)
        handle = sessions.who(token)
        home = USERS_DIR / handle if handle and invited(handle) else None  # (not invited any more: signed out)
        return (token, home) if home and home.is_dir() else (None, None)

    def our_host(self):
        return (self.headers.get("Host") or "").lower() in HOSTS

    def our_origin(self):
        """A change must come from the site's own pages (browsers say where a request comes from)."""
        origin = (self.headers.get("Origin") or "").lower()
        return origin.split("//", 1)[-1] in HOSTS

    def cookie(self, name, value, seconds):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
        return f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={seconds}{secure}"

    # ---- answers
    def answer(self, status, body=b"", kind="text/plain; charset=utf-8", cookies=(), cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for value in cookies:
            self.send_header("Set-Cookie", value)
        for name, value in HEADERS:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def answer_json(self, value, status=200, cookies=()):
        self.answer(status, json.dumps(value).encode(), "application/json", cookies)

    def go_to(self, where, cookies=()):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for value in cookies:
            self.send_header("Set-Cookie", value)
        self.end_headers()

    def body(self, most=4000):
        try:
            return self.rfile.read(min(int(self.headers.get("Content-Length") or 0), most))
        except ValueError:
            return b""

    # ---- the way in
    def do_GET(self):
        self.route()

    def do_HEAD(self):
        self.route()

    def do_POST(self):
        self.route()

    def do_DELETE(self):
        self.route()

    def route(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":  # (the host's own check: nothing in it)
            return self.answer(200, b"ok")
        if not self.our_host():
            return self.answer(403, b"Forbidden")
        if self.command in ("POST", "DELETE") and not self.our_origin():
            return self.answer(403, b"Forbidden")
        token, home = self.session()
        if path in ("/logo.svg", "/icon.svg", "/favicon.ico") or path.startswith("/public/"):
            return self.asset(path)
        if path == "/sign-in":
            return self.go_to("/") if signed_in(home) else self.answer(200, sign_in_page(), "text/html; charset=utf-8")
        if path == "/api/invited" and self.command == "POST":
            return self.check_invite()
        if path == "/api/invites" and self.command == "GET":
            return self.list_invites(token, home)
        if path == "/api/invites" and self.command == "POST":
            return self.add_invite_api(token, home)
        if path == "/api/invites/code" and self.command == "POST":
            return self.generate_code_api(token, home)
        if path.startswith("/api/invites/") and self.command == "DELETE":
            return self.remove_invite_api(token, home, path)
        if path.startswith("/api/sign-in"):
            return self.sign_in(path, home)
        if path == "/api/sign-out":
            return self.sign_out(token, home)
        if home:
            return self.relay(home)
        if path in ("/", "/index.html"):
            return self.answer(200, sign_in_page(), "text/html; charset=utf-8")
        return self.answer(401 if path.startswith("/api/") else 404, b"Sign in first")

    def asset(self, path):
        """The website's logo, tab icon and RemiliaNET's fonts, made and fetched as the dashboards do."""
        name = path.removeprefix("/public/")
        if path == "/logo.svg":
            body, kind = dashboard.remidex_logo(), "image/svg+xml"
        elif path in ("/icon.svg", "/favicon.ico"):
            body, kind = dashboard.remidex_icon(), "image/svg+xml"
        elif name in dashboard.PUBLIC:
            body, kind = dashboard.public_asset(name), dashboard.PUBLIC[name][1]
        else:
            body, kind = None, None
        return self.answer(200, body, kind, cache="max-age=86400") if body else self.answer(404, b"Not found")

    # ---- signing in
    def sign_in(self, path, home):
        """The sign-in page's questions (see signin.html and dashboard.Handler.sign_in): passed on to the sign-in's own
        dashboard, in its own folder. GET /api/sign-in says how it's going; once it has worked, the person is let in."""
        pending = self.cookies().get(PENDING)
        with signins_lock:
            run = signins.get(pending) if pending else None
        if self.command == "GET" and path == "/api/sign-in":
            if run is None:
                return self.answer_json({"signed_in": signed_in(home), "running": False, "step": None, "error": None, "attempt": None, "handle": None, "hue": 204})
            status, said = self.ask_desk(run.home, "GET", "/api/sign-in")
            if status != 200:
                return self.answer_json({"signed_in": False, "running": False, "step": None, "error": "RemiDex lost track of the sign-in. Try again", "attempt": None, "hue": 204})
            if said.get("signed_in") and said.get("handle"):
                with signins_lock:
                    mine = signins.pop(pending, None) is run  # (two asking at once: only one brings it in)
                if not mine:
                    return self.answer_json({"signed_in": signed_in(home), "running": True, "step": None, "error": None, "attempt": said.get("attempt"), "hue": 204})
                if not invited(said["handle"]):  # (someone not on the list got this far: their sign-in isn't kept)
                    desks.stop(run.home)
                    shutil.rmtree(run.home, ignore_errors=True)
                    log(f"Website: turned away ~{said['handle']} (not invited); their sign-in was deleted.")
                    return self.answer_json({**said, "signed_in": False, "running": False, "error": "RemiDex is invite-only right now, and that account isn't on the list"},
                                            cookies=[self.cookie(PENDING, "", 0)])
                welcomed = welcome(run, said["handle"])
                if welcomed is None:
                    return self.answer_json({**said, "signed_in": False, "running": False, "error": "RemiliaNET's handle for you isn't one RemiDex can use"})
                token = sessions.new(welcomed.name)
                return self.answer_json({**said, "signed_in": True}, cookies=[self.cookie(COOKIE, token, SESSION_FOR), self.cookie(PENDING, "", 0)])
            return self.answer_json({**said, "signed_in": False})
        if self.command != "POST":
            return self.answer(405, b"Not allowed")
        body = self.body()
        if path != "/api/sign-in":  # (a code, or "start over": for the sign-in under way)
            if run is None:
                return self.answer(409, b"No sign-in under way")
            status, said = self.ask_desk(run.home, "POST", path, body)
            return self.answer_json(said, status) if status else self.answer(503, b"Try again in a minute")
        if run is None:
            try:
                asked = json.loads(body or b"{}")
            except ValueError:
                asked = {}
            target_handle = str(asked.get("handle") or "").strip().lstrip("~").lower() if isinstance(asked, dict) else ""
            if not (target_handle and invited(target_handle)):
                return self.answer(403, b"RemiDex is invite-only right now")
            with _invites_lock:
                info = INVITE_DETAILS.get(target_handle, {})
                stored_code = (info.get("code") or "").strip().upper()
                is_redeemed = info.get("redeemed", False) or (USERS_DIR / target_handle / "cookies.json").exists()
            if stored_code and not is_redeemed and not is_admin(target_handle):
                provided_code = str(asked.get("code") or "").strip().upper()
                if provided_code != stored_code:
                    return self.answer_json({"error": "Valid invite code required to sign in"}, status=403)
            address = self.address()
            with signins_lock:
                recent = [t for t in started_from.get(address, []) if time.time() - t < SIGN_INS_PER_ADDRESS[1]]
                if len(recent) >= SIGN_INS_PER_ADDRESS[0]:
                    return self.answer(429, b"Too many sign-ins from here. Try again in a few minutes")
                if sum(time.time() - r.at < SIGN_IN_TIME for r in signins.values()) >= SIGN_INS_MOST:
                    return self.answer(503, b"RemiDex is busy signing other people in. Try again in a minute")
                started_from[address] = recent + [time.time()]
                run, pending = SignIn(address), secrets.token_urlsafe(24)
                signins[pending] = run
        status, said = self.ask_desk(run.home, "POST", path, body)
        if not status:
            return self.answer(503, b"Try again in a minute")
        return self.answer_json(said, status, cookies=[self.cookie(PENDING, pending, SIGN_IN_KEPT)])

    def check_invite(self):
        """POST /api/invited {"handle", "code"}: whether that handle may sign in (the sign-in page asks before the password)."""
        try:
            asked = json.loads(self.body() or b"{}")
        except ValueError:
            asked = {}
        if not isinstance(asked, dict):
            return self.answer_json({"invited": False, "error": "Invalid request"})
        h = str(asked.get("handle") or "").strip().lstrip("~").lower()
        if not h:
            return self.answer_json({"invited": False, "error": "Type your handle"})
        if "*" in INVITES:
            return self.answer_json({"invited": True, "needs_code": False})
        if not invited(h):
            return self.answer_json({"invited": False, "error": f"RemiDex is invite-only right now, and ~{h} isn't on the list."})

        with _invites_lock:
            info = INVITE_DETAILS.get(h, {})
            stored_code = (info.get("code") or "").strip().upper()
            is_redeemed = info.get("redeemed", False) or (USERS_DIR / h / "cookies.json").exists()

        if is_redeemed or is_admin(h):
            return self.answer_json({"invited": True, "needs_code": False})

        if not stored_code:
            if not DATABASE_URL:
                return self.answer_json({"invited": True, "needs_code": False})
            return self.answer_json({"invited": False, "needs_code": True, "error": f"~{h} is on the list, but an invite code hasn't been generated yet. Ask your admin."})

        code = str(asked.get("code") or "").strip().upper()
        if not code:
            return self.answer_json({"invited": True, "needs_code": True})

        if code != stored_code:
            return self.answer_json({"invited": False, "needs_code": True, "error": "That invite code didn't match. Check the code with your admin."})

        return self.answer_json({"invited": True, "needs_code": False, "verified": True})

    def _require_admin(self, token, home):
        """403 if the requester isn't an admin; None if they are."""
        if not is_admin(sessions.who(token) if token else None):
            return self.answer_json({"error": "invite management is admin-only"}, status=403)
        return None

    def list_invites(self, token, home):
        """GET /api/invites: the current invite list (admin only)."""
        blocked = self._require_admin(token, home)
        if blocked:
            return blocked
        with _invites_lock:
            invites_list = []
            for h in sorted(INVITES):
                if h == "*":
                    continue
                info = INVITE_DETAILS.get(h, {})
                is_redeemed = info.get("redeemed", False) or (USERS_DIR / h / "cookies.json").exists()
                invites_list.append({
                    "handle": h,
                    "code": info.get("code"),
                    "redeemed": is_redeemed,
                    "admin": is_admin(h),
                })
            return self.answer_json({"invites": invites_list, "admins": sorted(ADMINS)})

    def add_invite_api(self, token, home):
        """POST /api/invites {"handle": "calm"}: invite someone (admin only)."""
        blocked = self._require_admin(token, home)
        if blocked:
            return blocked
        try:
            asked = json.loads(self.body() or b"{}")
        except ValueError:
            asked = {}
        handle = str((asked or {}).get("handle", "")).strip().lstrip("~").lower()
        if not handle:
            return self.answer_json({"error": "handle required"}, status=400)
        ok = add_invite(handle)
        return self.answer_json({"ok": ok, "handle": handle})

    def generate_code_api(self, token, home):
        """POST /api/invites/code {"handle": "calm"}: generate a new code for someone (admin only)."""
        blocked = self._require_admin(token, home)
        if blocked:
            return blocked
        try:
            asked = json.loads(self.body() or b"{}")
        except ValueError:
            asked = {}
        handle = str((asked or {}).get("handle", "")).strip().lstrip("~").lower()
        if not handle or not HANDLE.fullmatch(handle):
            return self.answer_json({"error": "valid handle required"}, status=400)
        code = generate_invite_code(handle)
        return self.answer_json({"ok": True, "handle": handle, "code": code})

    def remove_invite_api(self, token, home, path):
        """DELETE /api/invites/{handle}: uninvite someone (admin only)."""
        blocked = self._require_admin(token, home)
        if blocked:
            return blocked
        handle = path.split("/api/invites/", 1)[-1].split("?", 1)[0]
        if not handle:
            return self.answer_json({"error": "handle required"}, status=400)
        ok = remove_invite(handle)
        return self.answer_json({"ok": ok, "handle": handle})

    def ask_desk(self, home, method, path, body=None):
        """One question for home's dashboard, answered there: (status, its answer as JSON, or {}); (0, {}) if it can't be asked."""
        try:
            port = desks.port(home)
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            headers = {"Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=body, headers=headers)
            answer = connection.getresponse()
            raw = answer.read()
            connection.close()
        except (OSError, RuntimeError, http.client.HTTPException):
            return 0, {}
        try:
            said = json.loads(raw) if raw else {}
        except ValueError:
            said = {}
        return answer.status, said if isinstance(said, dict) else {}

    # ---- signing out
    def sign_out(self, token, home):
        """POST /api/sign-out (the dashboard's Sign out button): ends RemiDex's sign-in in this browser. The person's bot
        carries on, with what they've switched on."""
        if home is None:
            return self.answer(401, b"Sign in first")
        if self.command != "POST":
            return self.answer(405, b"Not allowed")
        sessions.drop(token=token)
        return self.answer_json({"ok": True}, cookies=[self.cookie(COOKIE, "", 0)])

    # ---- your dashboard
    def relay(self, home):
        """Passes the request on to home's dashboard as it is (a picture, a change, the live connection...), and its answer
        back as it comes. The dashboard sees it come from itself; the person's RemiDex cookie stays here."""
        try:
            port = desks.port(home)
        except RuntimeError:
            return self.answer(503, b"Your dashboard is starting. Try again in a moment.")
        desks.using(home, 1)
        connection = None
        answered = False
        try:
            for attempt in range(4):
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                    connection.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
                    connection.putheader("Host", f"127.0.0.1:{port}")
                    if self.headers.get("Origin"):
                        connection.putheader("Origin", f"http://127.0.0.1:{port}")  # (checked above: it's the site's own page)
                    for name in PASSED_ON:
                        if self.headers.get(name) is not None:
                            connection.putheader(name, self.headers[name])
                    connection.endheaders()
                    left = int(self.headers.get("Content-Length") or 0)
                    while left > 0 and (chunk := self.rfile.read(min(left, 1 << 16))):
                        connection.send(chunk)
                        left -= len(chunk)
                    answer = connection.getresponse()
                    break
                except (ConnectionRefusedError, ConnectionResetError, http.client.CannotSendRequest, http.client.ResponseNotReady):
                    if connection:
                        try:
                            connection.close()
                        except Exception:
                            pass
                        connection = None
                    if attempt < 3:
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    return self.answer(503, b"Dashboard busy. Try again in a moment.")

            answered = True
            self.send_response(answer.status, answer.reason)
            for name, value in answer.getheaders():
                if name.lower() not in HOP and name.lower() not in ("server", "date"):
                    self.send_header(name, value)
            if "Strict-Transport-Security" not in answer.headers:
                self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.end_headers()
            while chunk := answer.read1(1 << 16):  # (as it comes: the live connection never ends while the page is open)
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # (client disconnected)
        except Exception as e:
            if not answered:
                try:
                    self.answer(502, f"Dashboard error: {type(e).__name__}".encode())
                except Exception:
                    pass
        finally:
            if connection:
                try:
                    connection.close()
                except Exception:
                    pass
            desks.using(home, -1)


SEED_DIR = ROOT / "seed"  # recipe results from before the website (the old app's): added to everyone's once (see add_seed)
SEEDED_FILE = SHARED_DIR / "seeded.json"  # fingerprints of the seed files already added


def add_seed():
    """Adds the recipe results in seed/ (craft_stats.json, removed_recipes.json) to everyone's shared ones, once: the
    counts are added to what everyone's crafts have shown since (see recipes.add_results). A fingerprint of the files
    keeps a restart from adding them again."""
    files = [SEED_DIR / "craft_stats.json", SEED_DIR / "removed_recipes.json"]
    if not any(f.exists() for f in files):
        return
    mark = hashlib.sha256(b"".join(f.read_bytes() if f.exists() else b"-" for f in files)).hexdigest()
    done = read_json(SEEDED_FILE).get("added") or []
    if mark in done:
        return
    had = len(read_json(recipes.CRAFT_STATS_FILE))
    added = recipes.add_results(read_json(files[0]), read_json(files[1]))
    write_json(SEEDED_FILE, {"added": [*done, mark]})
    log(f"Website: added the recipe results from before the website ({added} recipes) to the {had} everyone's crafts had shown.")


def warm_art_cache():
    """Background thread to pre-fetch catalog item icons into SHARED_DIR / 'fan-art' so dashboard visits never wait."""
    try:
        shared_art = SHARED_DIR / "fan-art"
        shared_art.mkdir(parents=True, exist_ok=True)
        catalog_file = ROOT / "catalog.json"
        if not catalog_file.exists():
            return
        catalog = json.loads(catalog_file.read_text()).get("items", {})
        for item in catalog.values():
            path = item.get("art")
            if not path:
                continue
            name = path.rsplit("/", 1)[-1]
            local = shared_art / name
            if not local.exists():
                url = f"https://raw.githubusercontent.com/sneedragon/beetleboy-advance/HEAD/{path}"
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "remilianet-poke (personal script)"})
                    with urllib.request.urlopen(req, timeout=5) as r:
                        data = r.read()
                    local.write_bytes(data)
                except Exception:
                    pass
                time.sleep(0.02)
    except Exception as e:
        log(f"Website: art cache warming: {type(e).__name__}: {e}")


def drop_root():
    """Started as root (the server's disk belongs to root): the disk is handed to the remidex user, and everything runs
    as that user from here on."""
    if os.geteuid() != 0:
        return
    import pwd
    try:
        user = pwd.getpwnam("remidex")
    except KeyError:
        return
    DATA.mkdir(parents=True, exist_ok=True)
    if DATA.stat().st_uid != user.pw_uid:
        for path in [DATA, *DATA.rglob("*")]:
            os.chown(path, user.pw_uid, user.pw_gid, follow_symlinks=False)
    os.setgroups([])
    os.setgid(user.pw_gid)
    os.setuid(user.pw_uid)
    os.environ["HOME"] = user.pw_dir


def main():
    global sessions, scheduler
    drop_root()
    for folder in (DATA, USERS_DIR, SHARED_DIR, SIGNING_IN_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    add_seed()
    sessions = Sessions(SESSIONS_FILE)
    scheduler = Scheduler(rounds_most())
    scheduler.start()
    refresh_invites()  # load invites from Neon + env var
    ensure_invites_table()  # make sure the Neon invites table exists
    threading.Thread(target=social.watch_feed, daemon=True).start()
    threading.Thread(target=housekeeping, daemon=True).start()
    threading.Thread(target=_invite_refresh_loop, daemon=True).start()  # keep Neon invites fresh
    threading.Thread(target=warm_art_cache, daemon=True).start()  # pre-fetch catalog item icons
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True

    def leave(*_):
        scheduler.stop_all()
        desks.stop_all()
        os._exit(0)
    signal.signal(signal.SIGTERM, leave)
    log(f"Website: RemiDex on port {PORT} for {', '.join(sorted(HOSTS))}; {scheduler.most} rounds at once; {len(homes())} people; "
        + ("open to anyone." if "*" in INVITES else f"invite-only: {', '.join(sorted(INVITES)) or 'nobody'}."))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        leave()


if __name__ == "__main__":
    main()
