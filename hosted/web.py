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

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.setdefault("REMIDEX_DATA", "/data"))
os.environ.setdefault("REMIDEX_ADDRESS", "https://remidex.xyz")
os.environ.pop("REMIDEX_HOME", None)  # (the website's own folder is DATA/site: see remidex/places.py)
sys.path.insert(0, str(ROOT))
import dashboard  # noqa: E402  (the website's logo and fonts, made as each dashboard makes them)
from remidex import loop, social  # noqa: E402
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
    """Rounds at once: REMIDEX_ROUNDS, else what the server's memory allows (a round's browser takes about 350 MB, and the
    website and the dashboards need some 700 MB besides)."""
    if os.environ.get("REMIDEX_ROUNDS"):
        return max(1, int(os.environ["REMIDEX_ROUNDS"]))
    try:
        memory = int(Path("/sys/fs/cgroup/memory.max").read_text())
    except (OSError, ValueError):
        return 2
    return max(1, min(12, (memory - 700 * 2 ** 20) // (350 * 2 ** 20)))


def stop(proc):
    """Stops a program this started, and everything it started (a bot's browser, say)."""
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
        """No round starts for home while it's held (a sign-in being brought in, an account being deleted)."""
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
                    log(f"Scheduler: stopped a round going on too long (~{home.name}).")
                    stop(proc)
                    del self.rounds[home]
            everyone = homes()
            self.turn = (self.turn + 1) % max(1, len(everyone))
            for home in everyone[self.turn:] + everyone[:self.turn]:  # (everyone gets to go first in turn)
                if len(self.rounds) >= self.most:
                    break
                if home in self.rounds or home in self.held:
                    continue
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
            if not scheduler.running(home) and (run.home / "browser-profile").is_dir():
                shutil.rmtree(home / "browser-profile", ignore_errors=True)  # (signed out: a fresh browser profile with the new sign-in)
                (run.home / "browser-profile").rename(home / "browser-profile")
            with editing(home / "state.json"):
                state = read_json(home / "state.json")
                state.pop("needs_login", None)
                state.pop("rest_until", None)  # (straight to a round)
                write_json(home / "state.json", state)
            shutil.rmtree(run.home, ignore_errors=True)
    finally:
        scheduler.hold(home, False)
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


def site_page(name):
    """The sign-in page (signin.html, worded for the website), or the account page."""
    if name == "account":
        return ACCOUNT_PAGE.encode()
    page = (ROOT / "signin.html").read_text()
    return (page.replace("Sign in with your RemiliaNET account to start the bot.",
                         "Plays BeetleBoy and BeetleCraft on RemiliaNET for you. Sign in with your RemiliaNET account to start your own.")
            .replace("Your password goes straight to RemiliaNET. RemiDex doesn't save it.",
                     "Your password goes straight to RemiliaNET and isn't saved. RemiDex keeps the sign-in RemiliaNET gives, to run your bot: you can delete it any time. Not made by Remilia.")
            .replace("RemiDex isn't answering. Is it running?", "RemiDex isn't answering. Try again in a minute.")).encode()


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
        home = USERS_DIR / handle if handle else None
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
            self.wfile.write(body)

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

    def route(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":  # (the host's own check: nothing in it)
            return self.answer(200, b"ok")
        if not self.our_host():
            return self.answer(403, b"Forbidden")
        if self.command == "POST" and not self.our_origin():
            return self.answer(403, b"Forbidden")
        token, home = self.session()
        if path in ("/logo.svg", "/icon.svg") or path.startswith("/public/"):
            return self.asset(path)
        if path == "/sign-in":
            return self.go_to("/") if signed_in(home) else self.answer(200, site_page("sign-in"), "text/html; charset=utf-8")
        if path.startswith("/api/sign-in"):
            return self.sign_in(path, home)
        if path == "/account":
            return self.answer(200, site_page("account"), "text/html; charset=utf-8") if home else self.go_to("/")
        if path.startswith("/api/account"):
            return self.account(path, token, home)
        if home:
            return self.relay(home)
        if path in ("/", "/index.html"):
            return self.answer(200, site_page("sign-in"), "text/html; charset=utf-8")
        return self.answer(401 if path.startswith("/api/") else 404, b"Sign in first")

    def asset(self, path):
        """The website's logo, tab icon and RemiliaNET's fonts, made and fetched as the dashboards do."""
        name = path.removeprefix("/public/")
        if path == "/logo.svg":
            body, kind = dashboard.remidex_logo(), "image/svg+xml"
        elif path == "/icon.svg":
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

    # ---- your account
    def account(self, path, token, home):
        """POST /api/account/sign-out: signs you out of RemiDex here (your bot carries on). POST /api/account/delete
        {"confirm": your handle}: stops your bot and deletes everything RemiDex keeps for you, your sign-in included.
        GET /api/account: who you are."""
        if home is None:
            return self.answer(401, b"Sign in first")
        if self.command == "GET" and path == "/api/account":
            return self.answer_json({"handle": home.name, "address": os.environ["REMIDEX_ADDRESS"]})
        if self.command != "POST":
            return self.answer(405, b"Not allowed")
        if path == "/api/account/sign-out":
            sessions.drop(token=token)
            return self.answer_json({"ok": True}, cookies=[self.cookie(COOKIE, "", 0)])
        if path == "/api/account/delete":
            try:
                asked = json.loads(self.body() or b"{}")
            except ValueError:
                asked = {}
            if not isinstance(asked, dict) or str(asked.get("confirm") or "").strip().lstrip("~").lower() != home.name:
                return self.answer(400, b"Type your handle to confirm")
            scheduler.hold(home)
            try:
                scheduler.stop(home)
                desks.stop(home)
                shutil.rmtree(home, ignore_errors=True)
            finally:
                scheduler.hold(home, False)
            sessions.drop(handle=home.name)
            log(f"Website: ~{home.name} deleted their RemiDex.")
            return self.answer_json({"ok": True}, cookies=[self.cookie(COOKIE, "", 0)])
        return self.answer(404, b"Not found")

    # ---- your dashboard
    def relay(self, home):
        """Passes the request on to home's dashboard as it is (a picture, a change, the live connection...), and its answer
        back as it comes. The dashboard sees it come from itself; the person's RemiDex cookie stays here."""
        try:
            port = desks.port(home)
        except RuntimeError:
            return self.answer(503, b"Your dashboard is starting. Try again in a moment.")
        desks.using(home, 1)
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
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
            connection.close()
        except (OSError, http.client.HTTPException, ValueError):
            pass  # (the page went away, or the dashboard did: the browser asks again)
        finally:
            desks.using(home, -1)


ACCOUNT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Your RemiDex account</title><link rel="icon" href="/icon.svg" type="image/svg+xml">
<style>
  @font-face { font-family: Hei; src: url(/public/Hei.ttf) format("truetype"); font-display: swap; }
  :root { color-scheme: light dark; --bg: #e9e9e9; --card: #e8f2fa; --text: #222; --text-2: #666; --accent: #3b82c4; --line: #c9d7e3; }
  @media (prefers-color-scheme: dark) { :root { --bg: #1d1d1d; --card: #2a2f35; --text: #e6e6e6; --text-2: #a0a0a0; --line: #3c4651; } }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; background: var(--bg); color: var(--text); font: 14px/1.5 Hei, ui-monospace, Menlo, monospace; }
  main { width: min(440px, 100%); background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 22px; }
  img { display: block; width: 180px; margin: 0 auto 10px; } h1 { margin: 0 0 4px; font-size: 18px; text-align: center; }
  p { margin: 8px 0; color: var(--text-2); font-size: 13px; } .who { text-align: center; color: var(--text); }
  section { border-top: 1px dashed var(--line); margin-top: 16px; padding-top: 12px; }
  button { height: 40px; width: 100%; border: 0; border-radius: 4px; background: var(--accent); color: #fff; font: inherit; font-weight: bold; cursor: pointer; }
  button.danger { background: #c03a3a; } button:disabled { opacity: .5; cursor: default; }
  input { width: 100%; height: 38px; margin: 6px 0 8px; padding: 0 10px; border: 1px solid var(--line); border-radius: 4px; background: transparent; color: inherit; font: inherit; }
  a { color: var(--accent); } .note { min-height: 1.5em; color: #c03a3a; }
</style></head><body><main>
  <img src="/logo.svg" alt="RemiDex">
  <h1>Your RemiDex account</h1>
  <p class="who" id="who">…</p>
  <p style="text-align:center"><a href="/">Back to your dashboard</a></p>
  <section>
    <p>Signing out only ends RemiDex's sign-in on this browser. Your bot carries on, with what you've switched on.</p>
    <button id="out">SIGN OUT</button>
  </section>
  <section>
    <p>Deleting stops your bot and deletes everything RemiDex keeps for you: your RemiliaNET sign-in, switches, history and
       settings. Your RemiliaNET account isn't touched. What your crafts taught about recipes stays shared, without your name.</p>
    <label>Type your handle to confirm<input id="confirm" autocomplete="off" spellcheck="false" autocapitalize="off"></label>
    <button class="danger" id="delete">DELETE MY REMIDEX</button>
    <p class="note" id="note" role="status"></p>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
const post = (path, body) => fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
fetch("/api/account", {cache: "no-store"}).then(r => r.ok ? r.json() : Promise.reject()).then(a => { $("who").textContent = `Signed in as ~${a.handle}`; })
  .catch(() => location.replace("/"));
$("out").onclick = async () => { $("out").disabled = true; await post("/api/account/sign-out").catch(() => {}); location.replace("/"); };
$("delete").onclick = async () => {
  $("delete").disabled = true; $("note").textContent = "";
  const r = await post("/api/account/delete", {confirm: $("confirm").value}).catch(() => null);
  if (r && r.ok) { location.replace("/"); return; }
  $("note").textContent = r && r.status === 400 ? "That isn't your handle." : "That didn't work. Try again.";
  $("delete").disabled = false;
};
</script></body></html>"""


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
    sessions = Sessions(SESSIONS_FILE)
    scheduler = Scheduler(rounds_most())
    scheduler.start()
    threading.Thread(target=social.watch_feed, daemon=True).start()
    threading.Thread(target=housekeeping, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True

    def leave(*_):
        scheduler.stop_all()
        desks.stop_all()
        os._exit(0)
    signal.signal(signal.SIGTERM, leave)
    log(f"Website: RemiDex on port {PORT} for {', '.join(sorted(HOSTS))}; {scheduler.most} rounds at once; {len(homes())} people.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        leave()


if __name__ == "__main__":
    main()
