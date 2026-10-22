"""Hammers switched off for smashing: the bot never smashes with them, but they stay in the bag to be upgraded or go into a
recipe, and the dashboard only saves sensible choices (made-up bags, the real recipe book, temp files, a local test server)."""
import http.server
import json
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
import dashboard  # noqa: E402

tmp = Path(tempfile.mkdtemp())
support.patch(OVERRIDES_FILE=tmp / "overrides.json", CRAFT_STATS_FILE=tmp / "stats.json", REMOVED_FILE=tmp / "removed.json",
              STATE_FILE=tmp / "state.json")
book, catalog = pb.recipe_book()
cards = support.cards(catalog)
H = lambda k, rate=0: {"hammer": k, "craft_count": 0, "break_rate": rate, "base_break_rate": 0}  # noqa: E731
bag = lambda inv, hammers: pb.Bag({"inventory": inv, "crafting_slot3_unlocked": True, "hammers": hammers}, cards)  # noqa: E731
make = lambda key: {"keys": [key], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}  # noqa: E731
settings = lambda key, **kw: {"junk_cubes": False, "goal": None, "goal_again": False, "goal_paused": False, "goal_set_at": None, "locked": [],  # noqa: E731
                              "make": make(key), "skipped": None, "hammer_order": pb.HAMMERS, "hammer_limits": {h: 95 for h in pb.HAMMERS}, "max_break_chance": 25,
                              "hammer_timing": False, "hammer_off": [], **kw}

# ---- the setting
(tmp / "overrides.json").write_text(json.dumps({"hammers": {"off": ["hammer_t4", "hammer_t9", 3]}}))
ok(pb.load_settings()["hammer_off"] == ["hammer_t4"], "read back: only real hammers")
(tmp / "overrides.json").write_text(json.dumps({"hammers": {"off": "all"}}))
ok(pb.load_settings()["hammer_off"] == [], "a broken one is ignored: every hammer smashes")

# ---- smashing: never with a switched-off hammer
flowers = {"daisy": 2, "green": 3, "hammer_t1": 1, "hammer_t4": 1}  # two Tin Flowers to smash into a Bronze one
both = [H("hammer_t1"), H("hammer_t4")]
order = settings("@flower_3", hammer_order=["hammer_t4", "hammer_t1", "hammer_t2", "hammer_t3", "hammer_t5"])
ok(pb.next_craft(bag(flowers, both), True, order, {})[3] == "hammer_t4", "all on: it smashes with the first in your order (Adamantine)")
off = {**order, "hammer_off": ["hammer_t4"]}
ok(pb.next_craft(bag(flowers, both), True, off, {})[3] == "hammer_t1", "Adamantine switched off: it smashes with the Tin Hammer instead")
only = {"daisy": 2, "green": 3, "hammer_t4": 1}
ok(pb.next_craft(bag(only, [H("hammer_t4")]), True, off, {}) is None, "the only hammer is switched off: no smash")
sure = {"nectar": 1, "ladybug": 1, "green": 3, "hammer_t4": 1}  # a sure smash, ready but for a hammer
why = pb.craft_wait(bag(sure, [H("hammer_t4")]), True, settings("monarch", hammer_off=["hammer_t4"]), {})
ok(why == "hammers_off", f"and the screen says your hammers are switched off ({why})")
ok(pb.craft_wait(bag({"daisy": 2, "green": 3}, []), True, off, {}) != "hammers_off", "no hammer at all is a different wait")

# ---- still upgraded: a switched-off hammer goes into the recipe that names it
upgrade = {"hammer_t2": 1, "junk_cube_t2": 1, "pollen_uncommon": 1}
plan = pb.plan_make(pb.usable(bag(upgrade, [H("hammer_t2")]), settings("hammer_t3", hammer_off=["hammer_t2"])), True, make("hammer_t3"), book, catalog)
first = next(plan.ready(), None)
ok(first and first[2] == ["hammer_t2", "junk_cube_t2", "pollen_uncommon"], f"Bronze Hammer switched off: still upgraded into the Mithril Hammer ({first and first[2]})")
step = pb.next_craft(bag(upgrade, [H("hammer_t2")]), True, settings("hammer_t3", hammer_off=["hammer_t2"]), {})
ok(step and step[1] == pb.ASSEMBLE, "and the bot does it (an assemble needs no hammer)")
# with every hammer you have switched off, the plan doesn't make a second Tin Hammer you already have
tin_off = pb.plan_make(pb.usable(bag({"daisy": 2, "green": 3, "hammer_t1": 1}, [H("hammer_t1")]), settings("@flower_3", hammer_off=["hammer_t1"])), True, make("@flower_3"), book, catalog)
ok(not any(s.get("out") == "hammer_t1" for s in tin_off.steps), "a Tin Hammer you have but switched off isn't made again (you can only own one)")
no_tin = pb.plan_make(pb.usable(bag({"daisy": 2, "green": 3, "hammer_t4": 1, "junk_cube_t1": 2}, [H("hammer_t4")]), settings("@flower_3", hammer_off=["hammer_t4"])), True, make("@flower_3"), book, catalog)
ok(any(s.get("out") == "hammer_t1" for s in no_tin.steps), "only an Adamantine Hammer, switched off: the plan forges a Tin Hammer to smash with")

# ---- the dashboard's request
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


def save(body):
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}/api/hammers", data=json.dumps(body).encode(), method="POST",
                                     headers={"Host": f"127.0.0.1:{dashboard.PORT}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


base = {"order": pb.HAMMERS, "limits": {h: 25 for h in pb.HAMMERS}, "timing": True}
(tmp / "overrides.json").write_text("{}")
ok(save({**base, "off": ["hammer_t3", "hammer_t4", "hammer_t4"]}) == 200 and pb.read_json(pb.OVERRIDES_FILE)["hammers"]["off"] == ["hammer_t3", "hammer_t4"], "switched off and saved")
ok(save({**base, "off": ["hammer_t9"]}) == 400 and save({**base, "off": "hammer_t1"}) == 400, "only real hammers, in a list")
ok(save(base) == 200 and pb.read_json(pb.OVERRIDES_FILE)["hammers"]["off"] == [], "left out: every hammer smashes")
rack = dashboard.hammer_rack(bag({}, []), cards, {**pb.load_settings(), "hammer_off": ["hammer_t5"]})
ok([h["smash"] for h in rack] == [True, True, True, True, False], "the dialog is told which are switched off")
server.shutdown()
ok.done()
