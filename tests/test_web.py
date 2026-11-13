"""The website (hosted/web.py): anyone not signed in gets the sign-in page; a sign-in that works makes a RemiDex of their
own (or brings a new sign-in into theirs); each person only ever reaches their own dashboard; changes only come from the
site's own pages; sign-ins are limited; signing out and deleting work; the scheduler runs everyone's rounds, a few at a
time; recipe results and the activity feed are shared, news about someone goes to them only (an empty disk in a temp
folder, a stand-in for the sign-in, each person's real dashboard run offline, stand-in rounds: nothing reaches RemiliaNET)."""
import http.client
import json
import sys
import threading
import time
import support
web = support.load_site()
ok = support.Checks()
import poke_browser as pb  # noqa: E402
from remidex import loop, recipes, social  # noqa: E402

DATA = web.DATA
web.DESK_COMMAND = [sys.executable, str(support.ROOT / "tests" / "offline.py"), str(support.ROOT / "dashboard.py")]
web.sessions, web.scheduler = web.Sessions(web.SESSIONS_FILE), web.Scheduler(1)
for folder in (web.USERS_DIR, web.SHARED_DIR, web.SIGNING_IN_DIR):
    folder.mkdir(parents=True, exist_ok=True)
server = web.ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
SITE = "https://remidex.test"


def ask(path, body=None, cookie=None, host="remidex.test", origin=SITE, address="203.0.113.7", stream=False):
    """One request to the website, as a browser at address sends it through the site's proxies: (status, headers, body)."""
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=30)
    headers = {"Host": host, "X-Forwarded-Proto": "https", "CF-Connecting-IP": address, **({"Cookie": cookie} if cookie else {})}
    if body is not None:
        headers.update({"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    connection.request("GET" if body is None else "POST", path, body=None if body is None else json.dumps(body).encode(), headers=headers)
    answer = connection.getresponse()
    if stream:
        return answer.status, answer.headers, answer.read1(200)
    data = answer.read()
    connection.close()
    return answer.status, answer.headers, data


def set_cookies(headers):
    """The cookies an answer sets: {name: (value, the rest)}."""
    found = {}
    for line in headers.get_all("Set-Cookie") or []:
        pair, _, rest = line.partition(";")
        name, _, value = pair.partition("=")
        found[name.strip()] = (value, rest)
    return found


# ---- anyone: the sign-in page, and nothing else
ok(ask("/healthz", host="anything")[0] == 200, "the host's health check is answered, whatever name it uses")
ok(ask("/", host="evil.example")[0] == 403, "the site only answers to its own names")
status, _, page = ask("/")
ok(status == 200 and b"Sign in with your RemiliaNET account to start your own" in page and b"you can delete it any time" in page, "anyone not signed in gets the sign-in page, worded for the website")
ok(json.loads(ask("/api/sign-in")[2])["signed_in"] is False, "it says they're not signed in")
ok(ask("/api/status")[0] == 401 and ask("/art/green")[0] == 404, "nobody's dashboard for anyone not signed in")
ok(ask("/account")[:2][0] == 303, "nor an account page")
ok(ask("/api/sign-in", {"email": "a@b.c", "password": "x"}, origin="https://evil.example")[0] == 403, "another site can't start a sign-in")
ok(ask("/api/sign-in", {"email": "a@b.c", "password": "x"}, origin=None)[0] == 403, "nor can anything that won't say where it's from")


# ---- signing in: the sign-in's own dashboard is a stand-in here (the real one is tested in test_sign_in)
class FakeDesk:
    """Answers as a sign-in's dashboard would: started, then working, then signed in as handle (writing the sign-in)."""

    def __init__(self):
        self.handle, self.asked, self.polls = "Tester", [], 0

    def __call__(self, home, method, path, body=None):
        self.asked.append((method, path))
        if method == "POST" and path == "/api/sign-in":
            return 200, {"started": True, "attempt": 7}
        if method == "POST":
            return 200, {"ok": True}
        self.polls += 1
        if self.polls < 2:
            return 200, {"signed_in": False, "running": True, "step": None, "error": None, "attempt": 7, "handle": None, "hue": 204}
        pb.write_json(home / "cookies.json", {"cookies": [{"name": "session", "value": self.handle}]})
        pb.write_json(home / "overrides.json", {"social": {"pokes": False}})  # (a first sign-in's switches: all off)
        (home / "browser-profile").mkdir(exist_ok=True)
        return 200, {"signed_in": True, "running": False, "step": None, "error": None, "attempt": 7, "handle": self.handle, "hue": 204}


fake = FakeDesk()
web.Handler.ask_desk = lambda handler, *asked: fake(*asked)


def sign_in(handle, address="203.0.113.7"):
    """A whole sign-in as the page goes through it: returns the RemiDex cookie it ends with ("remidex=..."), or None."""
    fake.handle, fake.polls = handle, 0
    status, headers, body = ask("/api/sign-in", {"email": "me@example.com", "password": "pw"}, address=address)
    pending = set_cookies(headers).get("remidex_signin", ("",))[0]
    if status != 200:
        return None
    for _ in range(3):
        status, headers, body = ask("/api/sign-in", cookie=f"remidex_signin={pending}")
        if json.loads(body).get("signed_in"):
            return f"remidex={set_cookies(headers)['remidex'][0]}"
    return None


me = sign_in("Tester")
home = web.USERS_DIR / "tester"
ok(me and home.is_dir() and pb.read_json(home / "cookies.json")["cookies"][0]["value"] == "Tester", "a sign-in that works makes their own RemiDex, with their sign-in")
_, headers, _ = ask("/api/sign-in")
ok(not list(web.SIGNING_IN_DIR.iterdir()), "and the sign-in's own folder is gone (it became theirs)")
saved = pb.read_json(web.SESSIONS_FILE)
ok(len(saved) == 1 and me.split("=", 1)[1] not in json.dumps(saved) and list(saved.values())[0]["handle"] == "tester", "RemiDex's own sign-in is kept only as a hash")
fake.polls = 0
status, headers, _ = ask("/api/sign-in", {"email": "me@example.com", "password": "pw"}, address="198.51.100.1")
rest = set_cookies(headers)["remidex_signin"][1]
ok("HttpOnly" in rest and "Secure" in rest and "SameSite=Lax" in rest, f"its cookies can't be read by pages, only go over HTTPS, and stay on this site ({rest.strip()})")
with web.signins_lock:
    web.signins.clear()

# ---- their dashboard: theirs, live, and changes only from the site's own pages
status, headers, page = ask("/", cookie=me)
ok(status == 200 and b"<title>RemiDex</title>" in page, f"signed in: the dashboard ({status})")
status, _, body = ask("/api/status", cookie=me)
data = json.loads(body) if status == 200 else {}
ok(data.get("hosted") is True and data.get("jobs") == {"bot": False}, f"it knows it's on the website (the scheduler isn't running in this test: {data.get('jobs')})")
ok(ask("/api/switch", {"action": "pokes", "auto": True}, cookie=me)[0] == 200 and pb.read_json(home / "overrides.json")["social"]["pokes"] is True,
   "a switch saved from their page is saved for their bot")
ok(ask("/api/switch", {"action": "pokes", "auto": False}, cookie=me, origin="https://evil.example")[0] == 403, "but never from another site")
status, headers, first = ask("/api/stream", cookie=me, stream=True)
ok(status == 200 and first.startswith(b"retry:"), f"the live connection comes through as it goes ({first[:20]})")
ok(ask("/sign-in", cookie=me)[0] == 303 and json.loads(ask("/api/sign-in", cookie=me)[2])["signed_in"] is True, "signed in, the sign-in page sends them to their dashboard")

# ---- someone else: only ever their own
them = sign_in("other_one", address="192.0.2.50")
status, _, body = ask("/api/me", cookie=them)
ok(them and (web.USERS_DIR / "other_one").is_dir() and status == 200 and json.loads(body).get("handle") is None, "someone else gets their own RemiDex, not yours")
ok(ask("/api/switch", {"action": "view_profiles", "auto": False}, cookie=them)[0] == 200 and "view_profiles" not in json.dumps(pb.read_json(home / "overrides.json")),
   "and their switches are theirs alone")
ok(ask("/api/status", cookie="remidex=made-up")[0] == 401, "a made-up cookie is nobody")

# ---- signed out of RemiliaNET: signing in again brings the new sign-in in, and keeps everything else
pb.write_json(home / "state.json", {"handle": "tester", "needs_login": True, "friends": ["a"]})
status, headers, _ = ask("/", cookie=me)
ok(status == 303 and headers["Location"] == "/sign-in", "their bot signed out: their dashboard sends them to sign in")
ok(json.loads(ask("/api/sign-in", cookie=me)[2])["signed_in"] is False, "and the sign-in page asks")
me = sign_in("Tester")
state = pb.read_json(home / "state.json")
ok(me and "needs_login" not in state and state["friends"] == ["a"] and pb.read_json(home / "overrides.json")["social"]["pokes"] is True,
   "signed in again: back in, with their history and switches as they were")

# ---- limits: RemiliaNET's accounts mustn't be guessed at through here
web.started_from.clear()
codes = []
for n in range(8):
    fake.polls = 0
    codes.append(ask("/api/sign-in", {"email": f"x{n}@example.com", "password": "pw"}, address="203.0.113.99")[0])
    with web.signins_lock:
        web.signins.clear()
ok(codes[:6] == [200] * 6 and codes[6:] == [429, 429], f"six sign-ins from one address in a while, then no more ({codes})")
codes = [ask("/api/sign-in", {"email": "a@b.c", "password": "pw"}, address=f"198.51.100.{n}")[0] for n in range(10, 10 + web.SIGN_INS_MOST + 1)]
ok(codes[-1] == 503 and set(codes[:-1]) == {200}, f"and only {web.SIGN_INS_MOST} at once ({codes})")
with web.signins_lock:
    web.signins.clear()

# ---- the scheduler: everyone's rounds, a few at a time, never for someone signed out
marks = DATA / "rounds.txt"
web.ROUND_COMMAND = [sys.executable, "-c", f"import os, time; open({str(marks)!r}, 'a').write(os.environ['REMIDEX_HOME'] + chr(10)); time.sleep(1.5)"]
pb.write_json(home / "state.json", {"handle": "tester", "next_slot_at": 0})
pb.write_json(web.USERS_DIR / "other_one" / "state.json", {"handle": "other_one", "next_slot_at": 0, "needs_login": True})
web.scheduler.look()
ok(web.scheduler.running(home) and not web.scheduler.running(web.USERS_DIR / "other_one"), "a round for the one whose bot wants one, none for one signed out")
third = web.USERS_DIR / "third"
third.mkdir()
pb.write_json(third / "cookies.json", {"cookies": []})
pb.write_json(third / "state.json", {"handle": "third", "next_slot_at": 0})
web.scheduler.look()
ok(not web.scheduler.running(third), "one at a time here: the next waits its turn")
time.sleep(2)
web.scheduler.look()
web.scheduler.hold(home)
time.sleep(2)
web.scheduler.look()
ok(web.scheduler.running(third) or str(third) in marks.read_text(), "then it gets its round")
ok(pb.read_json(web.SCHEDULER_FILE).get("most") == 1, "and says it's there, for the dashboards' green light")
web.scheduler.hold(home, False)
web.scheduler.stop_all()
ok(set(marks.read_text().split()) <= {str(home), str(third)}, "each round ran in its own person's folder")

# ---- shared by everyone: recipe results and the activity feed; news about someone goes to them only
ok(recipes.CRAFT_STATS_FILE.parent == web.SHARED_DIR and recipes.REMOVED_FILE.parent == web.SHARED_DIR, "recipe results are kept for everyone")
pb.write_json(home / "overrides.json", {"make": {"keys": ["x"], "recipe": 5, "set_at": 1.0, "start": 0}})
pb.write_json(third / "overrides.json", {"make_queue": [{"keys": ["y"], "recipe": 9}]})
recipes.remove_recipe(recipes.recipe_label(recipes.recipe_book()[0]["recipes"][3]))
ok(pb.read_json(home / "overrides.json")["make"]["recipe"] == 4 and pb.read_json(third / "overrides.json")["make_queue"][0]["recipe"] == 8,
   "a recipe the game says doesn't exist goes for everyone, and everyone's Auto makes keep pointing at the same recipes")
social._PEOPLE["at"] = 0
event = {"type": "poke", "from": {"userHandle": "someone"}, "to": {"userHandle": "tester"}}
social.share_feed([event, {"type": "join", "user": {"userHandle": "third"}}], time.time(), social.collections.deque(maxlen=5))
ok(len(pb.read_json(home / "feed_us.json").get("events") or []) == 1 and not (web.USERS_DIR / "other_one" / "feed_us.json").exists(), "news about you goes to you only")
ok(len(pb.read_json(web.SHARED_DIR / "remilia_activity.json")["events"]) == 2, "and what RemiliaNET's feed shows is shared")
ok(loop.round_wanted(home) is True and not loop.round_wanted(web.USERS_DIR / "other_one"), "news about you: your bot wants a round")

# ---- your account: signing out, and deleting everything
status, _, body = ask("/api/account", cookie=me)
ok(status == 200 and json.loads(body)["handle"] == "tester" and ask("/account", cookie=me)[0] == 200, "the account page knows who you are")
ok(ask("/api/account/delete", {"confirm": "other_one"}, cookie=me)[0] == 400 and home.is_dir(), "deleting needs your own handle typed")
status, headers, _ = ask("/api/account/sign-out", {}, cookie=me)
ok(status == 200 and set_cookies(headers)["remidex"][0] == "" and ask("/api/status", cookie=me)[0] == 401 and home.is_dir(),
   "signing out ends RemiDex's sign-in here; your bot stays")
me = sign_in("Tester")
ok(ask("/api/account/delete", {"confirm": "~Tester"}, cookie=me)[0] == 200 and not home.exists() and ask("/api/status", cookie=me)[0] == 401,
   "deleting stops your bot and deletes everything RemiDex kept for you")
ok(all(v["handle"] != "tester" for v in pb.read_json(web.SESSIONS_FILE).values()), "every RemiDex sign-in you had goes too")
web.desks.stop_all()
server.shutdown()
ok.done()
