"""The bot's main loop on a fake clock with a fake site: short rounds (what's due, a batch of pokes a few seconds apart),
then Chrome closes and rests; changes from the dashboard are acted on fast; using the dashboard keeps Chrome open."""
import json
import random
import tempfile
import types
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
from remidex import loop, settings as settings_module  # noqa: E402
tmp = Path(tempfile.mkdtemp())
paths = {n: tmp / (n.lower() + ".json") for n in ("STATE_FILE", "OVERRIDES_FILE", "STATUS_FILE", "FEED_US_FILE", "LOOKING_FILE", "PROFILE_EDIT_FILE", "GAME_FILE", "COOKIES_FILE")}
clock = {"t": 1_000_000.0}
events = []
fake_time = types.SimpleNamespace(time=lambda: clock["t"], sleep=lambda s: clock.update(t=clock["t"] + s), strftime=__import__("time").strftime, mktime=__import__("time").mktime,
                                  gmtime=__import__("time").gmtime, localtime=__import__("time").localtime)
random.seed(7)
real_write_json = pb.write_json


class FakeSite:
    def __init__(self, *a, **k):
        events.append(("chrome opened", clock["t"]))

    def require_sign_in(self):
        return "me"

    def wait(self, s):
        clock["t"] += s

    def save_cookies(self):
        pass

    def close(self):
        events.append(("chrome closed", clock["t"]))


class Ctx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def stub(name, push, every):
    def f(site, st, *rest, **kw):
        events.append((name, clock["t"]))
        st[push] = clock["t"] + every
        return 0
    return f


support.patch(**paths, time=fake_time, log=lambda m: None, Site=FakeSite, sync_playwright=lambda: Ctx(),
              poke_next=lambda site, st, dry, settings: (events.append(("poke", clock["t"])), clock["t"])[1],
              play_beetleboy=stub("claims", "beetle_check_at", 900), beetlecraft=stub("craft", "craft_check_at", 900),
              cache_profile_views=stub("news", "views_check_at", 300), accept_requests=stub("friends", "accept_check_at", 300),
              cache_for_dashboard=lambda site: None, view_feed_users=lambda *a, **k: 0, sync_game=lambda *a: None, sync_chat=lambda site: None, write_json=lambda path, data: None,
              keep_history=lambda: None, report=lambda *a, **k: None)
GAP, BATCH, REST = settings_module.POKE_GAP, settings_module.POKES_PER_SESSION, settings_module.SESSION_REST
real_wait = pb.wait_and_watch
hooks = {"wait": None}


def wait_hook(site, st, secs):
    if hooks["wait"]:
        hooks["wait"]()
    real_wait(site, st, secs)


support.patch(wait_and_watch=wait_hook)


def fresh_state():
    now = clock["t"]
    return {"handle": "me", "next_slot_at": now, "beetle_check_at": now + 60, "craft_check_at": now + 200, "views_check_at": now + 120, "accept_check_at": now + 9999}


def round_events(state):
    """Runs one round; returns its events as (name, seconds from its start)."""
    events.clear()
    start = clock["t"]
    pb.chrome_session(state, pb.current_settings())
    return [(n, round(t - start)) for n, t in events]


at = lambda rel, n: [t for n2, t in rel if n2 == n]  # noqa: E731

# ---- a round: what's due, a batch of pokes a few seconds apart, then Chrome closes
paths["OVERRIDES_FILE"].write_text("{}")
pb.changed(paths["OVERRIDES_FILE"])
touched = {"done": False}
start = clock["t"]


def dashboard_change():
    if not touched["done"] and clock["t"] - start >= 30:
        touched["done"] = True
        events.append(("DASHBOARD CHANGE", clock["t"]))
        paths["OVERRIDES_FILE"].write_text(json.dumps({"make": 1}))


hooks["wait"] = dashboard_change
state = fresh_state()
rel = round_events(state)
hooks["wait"] = None
pokes = at(rel, "poke")
ok(rel[0][0] == "chrome opened" and rel[-1][0] == "chrome closed" and at(rel, "chrome opened") == [0], "one round: Chrome opens once and closes")
ok(at(rel, "claims")[0] <= 2, "everything is checked once when Chrome opens")
ok(len(pokes) == BATCH, f"a batch of {BATCH} pokes ({len(pokes)})")
gaps = [b - a for a, b in zip(pokes, pokes[1:])]
ok(all(GAP[0] <= g <= GAP[1] + pb.TICK + 1 for g in gaps), f"a few seconds apart ({min(gaps)}–{max(gaps)} s)")
change = at(rel, "DASHBOARD CHANGE")[0]
after = [t for n, t in rel if n in ("claims", "craft") and t >= change]
ok(after and after[0] - change <= pb.TICK + 1, f"a change from the dashboard is acted on within {pb.TICK + 1}s ({after[0] - change}s)")
ok(rel[-1][1] - pokes[-1] <= pb.TICK + 1 and rel[-1][1] < 5 * 60, f"Chrome closes right after the batch ({rel[-1][1]} s open)")
ok(state["next_slot_at"] >= clock["t"] - (rel[-1][1] - pokes[-1]) + REST[0] - 1, "the next poke is the next round's")
state["rest_until"] = clock["t"] + REST[0]
ok(abs(loop.chrome_due_in(state, pb.current_settings()) - REST[0]) < 1, "nothing opens Chrome before the rest is over, even with checks due sooner")
state.pop("rest_until")
ok(loop.chrome_due_in(state, pb.current_settings()) < REST[0] - 60, "(without the rest, the news check would have opened it sooner)")

# ---- using the dashboard keeps Chrome open, and the next batch comes after the usual rest
clock["t"] += 3600
start = clock["t"]


def looking():
    if clock["t"] - start < 1500:
        paths["LOOKING_FILE"].write_text(json.dumps({"at": clock["t"]}))


hooks["wait"] = looking
rel = round_events(fresh_state())
hooks["wait"] = None
pokes = at(rel, "poke")
first, second = [t for t in pokes if t < 600], [t for t in pokes if t >= 600]
ok(len(at(rel, "chrome opened")) == 1 and rel[-1][1] >= 1500 - 2 * pb.TICK, f"Chrome stays open while you're using the dashboard ({rel[-1][1]} s)")
ok(len(first) == BATCH and second and second[0] - first[-1] >= REST[0], f"the next batch after the usual rest ({second[0] - first[-1] if second else None} s)")
ok(len(second) >= 10 and all(b - a <= GAP[1] + pb.TICK + 1 for a, b in zip(second, second[1:])), f"a whole batch again, a few seconds apart ({len(second)} pokes)")
ok(rel[-1][1] <= 1500 + pb.LOOKING_FOR + pb.TICK + 1, f"Chrome closes once you stop using the dashboard ({rel[-1][1]} s)")

# ---- a round never runs on and on
clock["t"] += 3600
settings_module.POKES_PER_SESSION, settings_module._SETTINGS["key"] = 10_000, None
rel = round_events(fresh_state())
settings_module.POKES_PER_SESSION, settings_module._SETTINGS["key"] = BATCH, None
ok(loop.SESSION_MAX <= rel[-1][1] <= loop.SESSION_MAX + GAP[1] + pb.TICK + 1, f"a round ends after {loop.SESSION_MAX // 60} minutes at most ({rel[-1][1]} s)")

# ---- between rounds (run_forever): Chrome rests; a failed round is tried again in a minute, then after the usual rest
clock["t"] += 3600
support.patch(write_json=real_write_json, TICK=30)
real_write_json(paths["COOKIES_FILE"], {})
real_write_json(paths["STATE_FILE"], {**fresh_state(), "beetle_check_at": clock["t"] + 10 ** 6, "craft_check_at": clock["t"] + 10 ** 6,
                                      "views_check_at": clock["t"] + 10 ** 6, "accept_check_at": clock["t"] + 10 ** 6})
rounds, outcomes = [], iter(["fail", "fail", "ok", "ok", "ok"])


class Done(BaseException):  # (not an Exception: the loop carries on after those)
    pass


def fake_round(state, settings):
    rounds.append(clock["t"])
    state["next_slot_at"] = clock["t"]  # (always something due: only the rest holds the next round back)
    if next(outcomes, "stop") == "fail":
        raise RuntimeError("couldn't check the sign-in (HTTP 0)")
    if len(rounds) > 5:
        raise Done


support.patch(chrome_session=fake_round)
try:
    loop.run_forever()
except Done:
    pass
waits = [round(b - a) for a, b in zip(rounds, rounds[1:])]
ok(len(waits) == 5, f"rounds keep coming ({len(rounds)})")
ok(loop.ERROR_RETRY <= waits[0] <= loop.ERROR_RETRY + 30, f"after a failed round: again in about a minute ({waits[0]} s)")
ok(all(REST[0] <= w <= REST[1] + 30 for w in waits[1:]), f"otherwise the usual rest, with Chrome closed ({waits[1:]} s)")
ok(not hasattr(loop, "stay_awake") and "caffeinate" not in Path(loop.__file__).read_text(), "it never keeps the Mac awake")
ok.done()
