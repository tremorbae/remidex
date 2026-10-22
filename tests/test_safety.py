"""A change is never sent to RemiliaNET twice, the bot and the dashboard can't undo each other's changes, and every answer
to a craft goes through the whole crafting code without a crash (fake page and site, temp files, no network)."""
import http.server
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
import dashboard  # noqa: E402
from remidex import browser  # noqa: E402
from remidex.core import PlaywrightError  # noqa: E402

tmp = Path(tempfile.mkdtemp())
support.patch(OVERRIDES_FILE=tmp / "overrides.json", PROFILE_EDIT_FILE=tmp / "edit.json", STATE_FILE=tmp / "state.json",
              MY_ACHIEVEMENTS_FILE=tmp / "achievements.json")


# ---- a request cut off by the page reloading: a read is asked again, a change never is
class Page:
    def __init__(self):
        self.calls = 0

    def evaluate(self, script, args):
        self.calls += 1
        if self.calls == 1:
            raise PlaywrightError("Execution context was destroyed, most likely because of a navigation")
        return {"status": 200, "body": {"ok": True}}

    def wait_for_load_state(self, state):
        pass

    def wait_for_timeout(self, ms):
        pass


site = browser.Site.__new__(browser.Site)
site.page = Page()
ok(site.api("GET", "/api/beetle/user") == (200, {"ok": True}) and site.page.calls == 2, "a read cut off by a reload is asked again")
site.page = Page()
ok(site.api("POST", "/api/beetle/action/craft", {}) == (0, None) and site.page.calls == 1, "a craft cut off by a reload is never sent again")

# ---- a profile save doesn't touch overrides.json, so it can't undo a change the bot makes meanwhile
pb.write_json(pb.STATE_FILE, {"handle": "me", "friends": []})
pb.write_json(pb.OVERRIDES_FILE, {"make_queue": [{"keys": ["a"]}]})
original = dashboard.profile_request


def slow_request(request):  # the bot moves the queue on while the dashboard is busy with the save
    pb.write_json(pb.OVERRIDES_FILE, {"make_queue": []})
    return original(request)


dashboard.profile_request = slow_request
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
post = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/profile", data=json.dumps({"bio": "hi"}).encode(),
                              headers={"Host": f"127.0.0.1:{dashboard.PORT}", "Content-Type": "application/json"})
with urllib.request.urlopen(post, timeout=10) as r:
    ok(r.status == 200 and "settings" in json.loads(r.read()), "the save is answered with the settings, as before")
ok(pb.read_json(pb.PROFILE_EDIT_FILE).get("bio") == "hi", "the edit is saved for the bot")
ok(pb.read_json(pb.OVERRIDES_FILE) == {"make_queue": []}, "and what the bot changed meanwhile stays")
dashboard.profile_request = original

# ---- the dashboard answers to its names (remidex.localhost too), and to nothing else, so other sites can't reach it
def asked(host, path="/api/stream", origin=None, body=None):
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", data=body,
                                     headers={"Host": host, **({"Origin": origin} if origin else {}), **({"Content-Type": "application/json"} if body else {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


port = dashboard.PORT
ok(asked(f"remidex.localhost:{port}", "/favicon.ico") in (200, 404) and asked(f"localhost:{port}", "/favicon.ico") in (200, 404), "remidex.localhost and localhost both reach it")
ok(asked(f"evil.example:{port}", "/favicon.ico") == 403 and asked(f"remidex.localhost.evil.example:{port}", "/favicon.ico") == 403, "any other name is refused")
ok(asked(f"remidex.localhost:{port}", "/api/looking", f"http://remidex.localhost:{port}", b"{}") == 204, "a change from the page at remidex.localhost goes through")
ok(asked(f"remidex.localhost:{port}", "/api/looking", "http://evil.example", b"{}") == 403, "one from another site doesn't")

# ---- the bot (its own process) moves the queue on while you switch an item off: both changes stay
bot_side = subprocess.Popen([sys.executable, "-c", f"""import sys, time
sys.path.insert(0, {str(support.ROOT / 'tests')!r})
import support
from pathlib import Path
pb = support.load()
path = Path({str(pb.OVERRIDES_FILE)!r})
with pb.editing(path):
    overrides = pb.read_json(path)
    print("reading", flush=True)
    time.sleep(1)  # (the bot working out what's next)
    overrides["make_queue"] = []
    pb.write_json(path, overrides)
"""], stdout=subprocess.PIPE, text=True)
pb.write_json(pb.OVERRIDES_FILE, {"make_queue": [{"keys": ["a"]}]})
bot_side.stdout.readline()  # it has read the file and is about to change it
started = time.time()
post = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/lock", data=json.dumps({"key": "pond", "locked": True}).encode(),
                              headers={"Host": f"127.0.0.1:{dashboard.PORT}", "Content-Type": "application/json"})
with urllib.request.urlopen(post, timeout=10) as r:
    ok(r.status == 200, "the switch is saved")
bot_side.wait(timeout=10)
saved = pb.read_json(pb.OVERRIDES_FILE)
ok(saved.get("locked") == ["pond"] and saved.get("make_queue") == [] and time.time() - started > 0.5,
   f"the dashboard waits for the bot's change and keeps it, and the bot's write keeps yours ({saved})")
server.shutdown()


# ---- you ask for another profile change while the bot is saving the last one: the newer one isn't marked done
class ProfileSite:
    def api(self, method, path, body=None):
        return 200, {"user": {"username": "me", "bio": "old", "pfp": "p"}}

    def signed_api(self, method, path, body=None):
        pb.write_json(pb.PROFILE_EDIT_FILE, {"bio": "newer", "asked_at": 2.0})  # sent from the dashboard meanwhile
        return 200, {}


pb.write_json(pb.PROFILE_EDIT_FILE, {"bio": "first", "asked_at": 1.0})
support.patch(log=lambda m: None)
pb.apply_profile_edit(ProfileSite(), {"handle": "me"})
saved = pb.read_json(pb.PROFILE_EDIT_FILE)
ok(saved.get("bio") == "newer" and not saved.get("done_at") and pb.profile_edit_waiting(), f"the newer change waits its turn ({saved})")

# ---- each answer the game gives a craft runs right through: the craft is noted, nothing crashes
logs = []
support.patch(CRAFT_STATS_FILE=tmp / "stats.json", GAME_FILE=tmp / "game.json", LEVELS_FILE=tmp / "levels.json", REMOVED_FILE=tmp / "removed.json",
              LOG_FILE=tmp / "log.txt", log=logs.append, alert=lambda *a, **k: None)
book, catalog = pb.recipe_book()
cards = support.cards(catalog)
moss = "pinecone or moss or gunpowder from @beetle_4 + pollen_rare"


class CraftSite:
    def __init__(self, answer):
        self.answer, self.greens, self.crafts = answer, 3, 0

    def game(self):
        return {"inventory": {"pond": 1, "pollen_rare": 1, "green": self.greens, "hammer_t1": 1}, "crafting_slot3_unlocked": True,
                "hammers": [{"hammer": "hammer_t1", "break_rate": 0, "base_break_rate": 0, "craft_count": 0}]}

    def api(self, method, path, body=None):
        return (200, self.game()) if path == "/api/beetle/user" else (200, cards) if path == "/api/beetle/cards" else (200, {})

    def signed_api(self, method, path, body=None):
        self.crafts += 1
        self.greens -= 1  # each smash sacrifices one, so the run ends
        made = {"beetle": "moss", "beetle_name": "Moss"} if self.answer == "SUCCESS" else {}
        return 200, {"message": self.answer, "result": made, "user": self.game()}

    def wait(self, s):
        pass


for answer in ("UNLUCKY_ROLL", "SUCCESS", "INVALID_RECIPE", "ALREADY_OWNED"):
    pb.write_json(pb.GAME_FILE, {}); pb.write_json(pb.CRAFT_STATS_FILE, {}); pb.write_json(pb.REMOVED_FILE, {}); pb._REMOVED["checked"] = 0
    state, site = {"handle": "me"}, CraftSite(answer)
    settings = {"junk_cubes": False, "goal": None, "goal_again": False, "goal_paused": False, "goal_set_at": None, "locked": [],
                "make": {"keys": ["moss"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False},
                "hammer_order": ["hammer_t1"], "hammer_limits": {"hammer_t1": 95}, "max_break_chance": 25, "hammer_timing": False}
    try:
        pb.beetlecraft(site, state, False, settings)
        crashed = None
    except Exception as e:  # what used to happen to every failed smash: note_craft was called the wrong way
        crashed = e
    seen = pb.read_json(pb.CRAFT_STATS_FILE).get(moss) or {}
    ok(crashed is None and site.crafts >= 1, f"{answer}: the craft runs through ({crashed or f'{site.crafts} crafts'})")
    if answer in ("UNLUCKY_ROLL", "SUCCESS"):
        ok(seen.get("n") == site.crafts, f"{answer}: each try is counted for the odds the planner learns ({seen})")

# ---- someone's first sign-in on this Mac: every switch on the dashboard starts off; nobody who's been here is changed
support.patch(COOKIES_FILE=tmp / "new_cookies.json", STATE_FILE=tmp / "new_state.json", OVERRIDES_FILE=tmp / "new_overrides.json")
ok(browser.first_sign_in_defaults(), "a first sign-in sets the defaults")
fresh = pb.load_settings()
ok(not any(fresh["beetleboy"].values()) and not any(fresh[k] for k in ("pokes", "friend_requests", "view_profiles", "junk_cubes")),
   f"claims, hunts, pokes, friend requests, profile views and junk cubes all start off ({fresh['beetleboy']})")
ok(not browser.first_sign_in_defaults() and pb.read_json(pb.OVERRIDES_FILE) and not fresh["beetleboy"]["beetleHunt"], "someone who's been here keeps what they set")
support.patch(OVERRIDES_FILE=tmp / "nothing_picked.json")
ok(all(pb.load_settings()[k] for k in ("pokes", "view_profiles")), "with nothing picked (an install from before), they stay on")
ok.done()
