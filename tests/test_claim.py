"""A card on manual clicked on the dashboard: the bot does it once, straight away, the way the game's own button does (a
fake game, temp files), and the dashboard's side of it (POST /api/claim)."""
import copy
import json
import time
import support
pb = support.load()
ok = support.Checks()
import dashboard  # noqa: E402
from remidex import loop  # noqa: E402
from remidex.beetle import HUNT_GAP  # noqa: E402
from remidex.core import APP_DIR, COOKIES_FILE  # noqa: E402

logs = []
support.patch(log=logs.append, alert=lambda *a, **k: None)
BASE = {"level": 5, "xp": 100, "inventory": {"cheese": 10}, "cooldowns": {}, "beetleHuntsUsed": 0, "lastBeetleHuntDate": 0}


class Game:
    """The game's API: GET your game, POST an action (refused with the game's own word, if refuse says so)."""

    def __init__(self, game=None, refuse=None, status=200, **changes):
        self.game, self.refuse, self.status, self.sent, self.waited = copy.deepcopy({**BASE, **(game or {}), **changes}), refuse or {}, status, [], []

    def signed_api(self, method, path, body=None):
        if method == "GET":
            return (200, copy.deepcopy(self.game)) if self.status == 200 else (self.status, {"error": "down"})
        action = path.rsplit("/", 1)[-1]
        self.sent.append(action)
        if action in self.refuse:
            return 400, {"success": False, "error": self.refuse[action], "user": copy.deepcopy(self.game)}
        game = self.game
        if action == "beetleHunt":
            game["inventory"]["cheese"] -= 20
            game["beetleHuntsUsed"] += 1
            game["lastBeetleHuntDate"] = time.time() * 1000
        else:
            game["cooldowns"][action] = 7_200_000
        if action == "claimUBC":
            game["inventory"]["cheese"] += 50
        game["xp"] += 10
        return 200, {"success": True, "result": {"success": True, "beetleCard": {"beetle_name": "Green Beetle"}, "xp": 10, "cheese": 50, "streak": 4},
                     "user": copy.deepcopy(game)}

    def wait(self, seconds):
        self.waited.append(seconds)


def click(*actions, at=None):
    pb.write_json(pb.CLAIM_FILE, {a: {"asked_at": at or time.time()} for a in actions})


def outcome(action):
    return pb.read_json(pb.CLAIM_FILE)[action]


# ---- a click: done once, logged as the history reads it, and the game after it saved for the dashboard
click("catchBeetle")
ok(list(pb.claims_waiting()) == ["catchBeetle"], "a click waits for the bot")
state, game = {"craft_check_at": 9e9}, Game()
pb.claim_now(game, state)
ok(game.sent == ["catchBeetle"], f"the free beetle is claimed ({game.sent})")
ok(outcome("catchBeetle")["done_at"] and outcome("catchBeetle")["error"] is None and not pb.claims_waiting(), "noted as done, and not waiting any more")
ok(any(line.startswith("Free beetle: Green Beetle") for line in logs), f"in the log the way the history counts it ({logs[-1:]})")
ok(state["craft_check_at"] == 0, "crafting looks at the new beetle straight away")
ok(pb.read_json(pb.GAME_FILE)["game"]["cooldowns"].get("catchBeetle"), "the game after it is saved, so the card shows its next time")
pb.claim_now(game, state)
ok(game.sent == ["catchBeetle"], "done once, not again")

# ---- the cheese and a hunt clicked together: the cheese first, so the hunt can spend it; a hunt is one hunt
click("beetleHunt", "claimUBC")
game = Game()
pb.claim_now(game, {})
ok(game.sent == ["claimUBC", "beetleHunt"], f"the cheese first, then one hunt ({game.sent})")
ok(outcome("beetleHunt")["error"] is None and outcome("claimUBC")["error"] is None, "both noted as done")
ok(any(line.startswith("Daily cheese: +50, streak 4") for line in logs) and any(line.startswith("Hunt: Green Beetle") for line in logs), "both logged")

# ---- what can't be done isn't sent, and the card says why
click("beetleHunt", "junkFaucet")
game = Game(cooldowns={"junkFaucet": 60_000})
pb.claim_now(game, {})
ok(game.sent == [], "nothing sent for what isn't ready")
ok(outcome("beetleHunt")["error"] == "it takes 20 cheese" and outcome("junkFaucet")["error"] == "not ready yet",
   f"why not ({outcome('beetleHunt')['error']}; {outcome('junkFaucet')['error']})")
click("beetleHunt")
game = Game(inventory={"cheese": 100}, beetleHuntsUsed=3, lastBeetleHuntDate=time.time() * 1000)
pb.claim_now(game, {})
ok(game.sent == [] and outcome("beetleHunt")["error"] == "hunting is recharging", "no hunt after the third, until they recharge")

# ---- the game says no: its own word, in the log too
click("catchBeetle")
game = Game(refuse={"catchBeetle": "Already claimed"})
pb.claim_now(game, {})
ok(outcome("catchBeetle")["error"] == "Already claimed" and "refused (Already claimed)" in logs[-1], f"the game's refusal is noted ({logs[-1]})")
click("claimUBC")
game = Game(status=503)
pb.claim_now(game, {})
ok(game.sent == [] and outcome("claimUBC")["error"] == "couldn't load the game (HTTP 503)", "the game not loading is noted, not tried again and again")

# ---- a hunt right after the last one waits as long as the site's own animation would
click("beetleHunt")
game = Game(inventory={"cheese": 100}, beetleHuntsUsed=1, lastBeetleHuntDate=(time.time() - 2) * 1000)
pb.claim_now(game, {})
ok(game.sent == ["beetleHunt"] and game.waited and HUNT_GAP[0] - 3 <= game.waited[0] <= HUNT_GAP[0] - 1,
   f"waited {game.waited[0] if game.waited else 0:.1f}s first")

# ---- a click from long ago is never done
click("catchBeetle", at=time.time() - pb.CLAIM_KEPT - 5)
ok(not pb.claims_waiting(), "a click the bot didn't get to in time is dropped")

# ---- a click wakes the bot: a round starts for it at once (see loop.round_wanted)
far = time.time() + 10 ** 6
pb.write_json(pb.STATE_FILE, {"next_slot_at": far, "beetle_check_at": far, "craft_check_at": far, "views_check_at": far, "accept_check_at": far, "rest_until": far})
pb.write_json(COOKIES_FILE, {"cookies": []})
pb.CLAIM_FILE.unlink()
ok(loop.round_wanted() is False and loop.round_wanted(APP_DIR) is False, "resting, nothing due: no round")
click("junkFaucet")
ok(loop.round_wanted() is True, "a click: a round now")
ok(loop.round_wanted(APP_DIR) is True, "the website's scheduler sees it too (it asks about each person's folder)")

# ---- the dashboard: POST /api/claim
ask = support.serve(dashboard)
post = lambda body, **k: ask("/api/claim", body, **k)  # noqa: E731
pb.CLAIM_FILE.unlink()
ask("/api/status")  # (the page had looked just before the click)
status, _, body = post({"action": "catchBeetle"})
ok(status == 200 and json.loads(body)["claims"] == {"catchBeetle": {"waiting": True}}, f"a click is asked for ({status} {body[:80]})")
asked = outcome("catchBeetle")["asked_at"]
time.sleep(0.01)
post({"action": "catchBeetle"})
ok(outcome("catchBeetle")["asked_at"] == asked, "clicking again before it's done changes nothing")
ok(post({"action": "openTheBank"})[0] == 400 and post({"action": ["catchBeetle"]})[0] == 400, "only the four cards")
ok(post({"action": "claimUBC"}, origin="https://elsewhere.example")[0] == 403, "another site can't click for you")
status, _, body = ask("/api/status")
ok(status == 200 and json.loads(body)["claims"] == {"catchBeetle": {"waiting": True}}, "the page sees it waiting")
pb.claim_now(Game(), {})
dashboard._status_cache["at"] = 0  # (a second on: the page never asks sooner than that after the last time)
status, _, body = ask("/api/status")
done = json.loads(body)["claims"]["catchBeetle"]
ok(not done.get("waiting") and done["done_at"] and done["error"] is None, f"and then done ({done})")
pb.write_json(pb.STATE_FILE, {"needs_login": True})
ok(post({"action": "claimUBC"})[0] == 409, "signed out: nothing to claim with")
ok.done()
