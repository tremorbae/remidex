"""The bot's main loop on a fake clock with a fake site: things happen when due, changes are acted on fast, Chrome closes."""
import json
import tempfile
import types
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
paths = {n: tmp / (n.lower() + ".json") for n in ("STATE_FILE", "OVERRIDES_FILE", "STATUS_FILE", "FEED_US_FILE", "LOOKING_FILE", "PROFILE_EDIT_FILE", "GAME_FILE", "COOKIES_FILE")}
clock = {"t": 1_000_000.0}
events = []
fake_time = types.SimpleNamespace(time=lambda: clock["t"], sleep=lambda s: clock.update(t=clock["t"] + s), strftime=__import__("time").strftime, mktime=__import__("time").mktime,
                                  gmtime=__import__("time").gmtime, localtime=__import__("time").localtime)


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


def stub(name, push=None, every=100):
    def f(site, st, *rest, **kw):
        events.append((name, clock["t"]))
        if push:
            st[push] = clock["t"] + every
        return 0
    return f


state = {"handle": "me", "beetle_check_at": clock["t"] + 60, "craft_check_at": clock["t"] + 200, "views_check_at": clock["t"] + 120, "accept_check_at": clock["t"] + 9999}
support.patch(**paths, time=fake_time, log=lambda m: None, Site=FakeSite, sync_playwright=lambda: Ctx(),
              poke_next=lambda site, st, dry, settings: (events.append(("poke", clock["t"])), None)[1],
              schedule_next=lambda st, settings, at: st.update(next_slot_at=clock["t"] + 300),
              play_beetleboy=stub("claims", "beetle_check_at", 100), beetlecraft=stub("craft", "craft_check_at", 150),
              cache_profile_views=stub("news", "views_check_at", 120), accept_requests=stub("friends", "accept_check_at", 5000),
              cache_for_dashboard=lambda site: None, view_feed_users=lambda *a, **k: 0, sync_game=lambda *a: None, write_json=lambda path, data: None,
              keep_history=lambda: None, report=lambda *a, **k: None)
start = clock["t"]
real_wait = pb.wait_and_watch
touched = {"done": False}


def wait_hook(site, st, secs):
    if clock["t"] - start > 1500:
        raise SystemExit("ran away")
    if not touched["done"] and clock["t"] - start >= 30:
        touched["done"] = True
        events.append(("DASHBOARD CHANGE", clock["t"]))
        paths["OVERRIDES_FILE"].write_text(json.dumps({"make": 1}))
    real_wait(site, st, secs)


support.patch(wait_and_watch=wait_hook)
paths["OVERRIDES_FILE"].write_text("{}")
pb.changed(paths["OVERRIDES_FILE"])
state["next_slot_at"] = clock["t"] + 250
pb.chrome_session(state, pb.current_settings())
rel = [(n, round(t - start)) for n, t in events]
at = lambda n: [t for n2, t in rel if n2 == n]
ok(rel[0][0] == "chrome opened" and rel[-1][0] == "chrome closed", "Chrome opens and closes")
ok(at("claims")[0] <= 2, "everything is checked once when Chrome opens")
change = at("DASHBOARD CHANGE")[0]
after = [t for n, t in rel if n in ("claims", "craft") and t >= change]
ok(after and after[0] - change <= pb.TICK + 1, f"a change from the dashboard is acted on within {pb.TICK + 1}s ({after[0] - change}s)")
ok(any(abs(t - 250) <= 3 for t in at("poke")), f"the poke slot ran at its due time (250s): {at('poke')}")
gaps = [b - a for a, b in zip(at("claims"), at("claims")[1:])]
ok(all(abs(g - 100) <= 3 or g <= 3 or g < 100 for g in gaps), f"claims ran 100 s after the last one: {gaps}")
ok(rel[-1][1] >= 800, f"Chrome stays open while things are due, and restarts after its session ({rel[-1][1]} s)")
ok.done()
