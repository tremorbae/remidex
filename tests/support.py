"""Helpers for the offline tests: the bot is loaded without a browser, in an empty folder of its own, and names (a file's
path, a function to stub, the clock) are replaced everywhere they're used, whichever part of the bot uses them."""
import http.client
import http.server
import json
import os
import sys
import tempfile
import threading
import types
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def offline():
    """Playwright stubbed out (no browser), and nothing but this computer can be reached: a test is never online. (Also
    for the programs a test starts: see offline.py.)"""
    sys.modules.setdefault("playwright", types.ModuleType("playwright"))
    sync = types.ModuleType("playwright.sync_api")
    sync.Error, sync.sync_playwright = Exception, None
    sys.modules["playwright.sync_api"] = sync
    online = urllib.request.urlopen

    def only_here(request, *args, **kwargs):
        url = request if isinstance(request, str) else request.full_url
        if urllib.parse.urlsplit(url).hostname not in ("127.0.0.1", "localhost"):
            raise OSError(f"the tests are offline ({url})")
        return online(request, *args, **kwargs)
    urllib.request.urlopen = only_here
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def load():
    """The bot (poke_browser), offline (see offline). It keeps everything in a new, empty folder (REMIDEX_HOME), so a test
    never reads or changes what RemiDex has saved on this computer, and gives the same result on any computer."""
    os.environ["REMIDEX_HOME"] = tempfile.mkdtemp()  # (before the bot is loaded: it's where it keeps things)
    offline()
    import poke_browser
    return poke_browser


def load_site(hosts="remidex.test"):
    """The website (hosted/web.py), offline, with its disk a new, empty folder: returns the module. hosts: the names it
    answers to. Before anything else of RemiDex is loaded (it's the website's folders they all use)."""
    os.environ.pop("REMIDEX_HOME", None)
    os.environ.update(REMIDEX_DATA=tempfile.mkdtemp(), REMIDEX_HOSTS=hosts, REMIDEX_ADDRESS=f"https://{hosts}")
    offline()
    sys.path.insert(0, str(ROOT / "hosted"))
    import web
    return web


def serve(dashboard):
    """The dashboard's server on a free port of this computer, for one test (it stops with the test). Returns
    ask(path, body=None, kind="application/json", host=None, origin=None): one request to it, the way the page makes
    them (a GET, or with a body a POST: bytes as they are, anything else as JSON; addressed to the dashboard's own name
    unless host says another; origin: the site the request says it comes from). Answers (status, headers, body). A
    redirect is an answer like any other: it isn't followed."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def ask(path, body=None, kind="application/json", host=None, origin=None):
        data = body if body is None or isinstance(body, bytes) else json.dumps(body).encode()
        headers = {"Host": host or f"127.0.0.1:{dashboard.PORT}", **({"Content-Type": kind} if data is not None else {}), **({"Origin": origin} if origin else {})}
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        try:
            connection.request("GET" if data is None else "POST", path, body=data, headers=headers)
            answer = connection.getresponse()
            return answer.status, answer.headers, answer.read()
        finally:
            connection.close()
    return ask


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
