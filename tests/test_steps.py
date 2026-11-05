"""A step's options on the dashboard: skip it (done last, the steps that need it don't hold it up), work on it first (with
the steps it needs), and for a smash, its own beetle and hammer. Only for the Auto make or quest they were picked for, and
the dashboard only saves sensible picks (made-up bags and plans, the real recipe book, temp files)."""
import json
import tempfile
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
import dashboard  # noqa: E402
from remidex import recipes  # noqa: E402

tmp = Path(tempfile.mkdtemp())
support.patch(OVERRIDES_FILE=tmp / "overrides.json", CRAFT_STATS_FILE=tmp / "stats.json", REMOVED_FILE=tmp / "removed.json")
book, catalog = pb.recipe_book()
cards = support.cards(catalog)
tin, bronze = "hammer_t1", "hammer_t2"
fresh = lambda *keys: [{"hammer": k, "break_rate": 0, "base_break_rate": 0} for k in keys]  # noqa: E731
make = {"keys": ["mars_rhino"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}
inv = {"pond": 1, "pollen_rare": 1, "cucumber": 1, "pollen_uncommon": 1, "ladybug": 1, "paperclip": 2, "button": 2, "green": 9, tin: 1}
bag = pb.Bag({"inventory": inv, "crafting_slot3_unlocked": True, "hammers": fresh(tin)}, cards)
base = {"junk_cubes": False, "goal": None, "goal_again": False, "goal_paused": False, "goal_set_at": None, "locked": [], "make": make,
        "hammer_order": [tin, bronze], "hammer_limits": {tin: 95, bronze: 95}, "max_break_chance": 25, "hammer_timing": False, "hammer_off": [], "steps": None}
task = pb.task_id("make", base)


def picked(settings=base, **picks):
    """settings with these picks for its Auto make's steps (skipped, first, sacrifice, hammer)."""
    return {**settings, "steps": {"task": pb.task_id("make", settings), "skipped": [], "first": None, "sacrifice": {}, "hammer": {}, **picks}}


# ---- skip: done last
first = pb.next_craft(bag, True, base, {})
ok(first is not None, f"something to craft to start with ({first and first[0]})")
skipped = picked(skipped=[first[0]])
then = pb.next_craft(bag, True, skipped, {})
ok(then is not None and then[0] != first[0], f"skip it: the bot does another step first ({then and then[0]})")
order = [c[0] for c in pb.craft_candidates(bag, True, skipped)]
ok(first[0] not in order, f"and the skipped one isn't offered while the rest of the plan has steps left ({order})")
only = pb.Bag({"inventory": {"cucumber": 1, "pollen_uncommon": 1, "green": 9, tin: 1}, "crafting_slot3_unlocked": True, "hammers": fresh(tin)}, cards)
ok(pb.next_craft(only, True, picked(skipped=[pb.next_craft(only, True, base, {})[0]]), {}) is None,
   "while the rest of the plan has steps left, a skipped one waits, even when nothing else can go right now")
moss = "pinecone or moss or gunpowder from @beetle_4 + pollen_rare"
lotus_bag = pb.Bag({"inventory": {"gunpowder": 1, "pinecone": 1, "pond": 1, "pollen_rare": 1, "green": 3, tin: 1}, "crafting_slot3_unlocked": True, "hammers": fresh(tin)}, cards)
lotus = {**base, "make": {"keys": ["black_lotus"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}}
step = pb.next_craft(lotus_bag, True, picked(lotus, skipped=[moss]), {})
ok(step and step[0] == moss, f"what's left only needs the skipped step (the Black Lotus needs the Moss): it's done, nothing stalls ({step and step[0]})")
alone_bag = pb.Bag({"inventory": {"pond": 1, "pollen_rare": 1, "green": 3, tin: 1}, "crafting_slot3_unlocked": True, "hammers": fresh(tin)}, cards)
only_moss = {**base, "make": {"keys": ["moss"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}}
ok((pb.next_craft(alone_bag, True, picked(only_moss, skipped=[moss]), {}) or [None])[0] == moss, "and a plan that's only the skipped step does it")
other = {**picked(skipped=[first[0]]), "steps": {**picked(skipped=[first[0]])["steps"], "task": "make something_else@0"}}
ok(pb.next_craft(bag, True, other, {})[0] == first[0], "picks for another Auto make don't count")
ok(pb.step_picks("make", {**base, "make": None, "steps": None})["task"] is None, "with no Auto make, nothing is picked (and nothing breaks)")

# ---- work now: the step, with the steps it needs, before the rest
steps = [{"label": "a", "from": [None]}, {"label": "b", "from": [None, None]}, {"label": "c", "from": [1, None]}, {"unlock": True}, {"label": "d", "from": [2]}]
ok(recipes.needed_for(steps, "d") == {"b", "c", "d"} and recipes.needed_for(steps, "a") == {"a"} and recipes.needed_for(steps, None) == set(),
   "a step needs the steps that make what it uses, further back too")


class Plan:  # (a made-up plan: its steps, and the ones that can go now)
    sacrifice = "green"

    def __init__(self, steps):
        self.steps = steps

    def ready(self):
        return [(s["label"], 1, [], 1) for s in self.steps if not s.get("unlock") and all(f is None for f in s["from"])]


ready = lambda first: [c[0] for c in recipes.ready_crafts(lambda hunted: Plan(steps), fallback=False, first=first)]  # noqa: E731
ok(ready(None) == ["a", "b"] and ready("d") == ["b", "a"] and ready("a") == ["a", "b"], f"picked: what it needs that can go now goes first ({ready('d')})")
waiting, backup, unstick = Plan([{"label": "w", "items": ["@flower_2"], "mode": 1, "from": ["wait"]}]), Plan([{"label": "x", "from": [None]}]), Plan([{"label": "g", "items": [], "mode": 1, "from": [None]}])
unstick.bag = pb.Bag({"inventory": {}}, {})
ok(all(c[4] == 0 for c in recipes.ready_crafts(lambda hunted: Plan(steps), fallback=False))
   and list(recipes.ready_crafts(lambda hunted, *more: unstick if more else waiting if hunted == recipes.HUNTED_COST else backup)) == [("g", 1, [], 1, 2)],
   "nothing in the plan can go: a plan for what a step waits for (no backup plan), and the craft says so")
ok(recipes.waited_for([{"items": ["@junk", "@junk"], "mode": pb.ASSEMBLE, "from": ["wait", "wait"]}, {"unlock": True},
                       {"items": ["x", "@flower_2"], "mode": pb.SMASH, "from": [None, "wait"]}]) == [("@junk", False), ("@flower_2", True)],
   "what the steps wait for, each once, and whether a smash waits for it")
junk, nectar = "junk_cube_t1 from @junk + @junk", "nectar or cattail from cucumber + pollen_uncommon"
smash_first = pb.next_craft(bag, True, picked(first=nectar), {})  # (a low-odds smash: on its own it goes after the sure steps)
ok(first[0] == junk and smash_first[0] == nectar, f"on a real plan, the smash picked goes before the {first[0]}")

# ---- nothing in the plan can go: the bot makes what a waiting step needs from what you hold, even
# the Junk Cube the plan set aside for a later step, and Green Beetles (one stays for the sacrifice)
stuck = lambda greens: pb.Bag({"inventory": {"sunflower": 1, "magnolia": 1, "cucumber": 1, "ladybug": 1, "junk_cube_t1": 1, "green": greens, tin: 1},  # noqa: E731
                               "crafting_slot3_unlocked": True, "hammers": fresh(tin)}, cards)
mithril = {**base, "make": {"keys": ["@flower_4"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}}
step = pb.next_craft(stuck(5), True, mithril, {})
ok(not list(pb.craft_candidates(stuck(5), True, mithril, fallback=False)) and step and step[2] == ["green", "junk_cube_t1"] and step[5:] == ("@flower_2", 2),
   f"the plan waits on a Tin flower: one is made from a Green Beetle and the Junk Cube ({step and step[2]})")
ok(pb.next_craft(stuck(3), True, mithril, {}) and pb.next_craft(stuck(2), True, mithril, {}) is None,
   "only while that leaves a Green Beetle for the smash waiting for it (one goes in the recipe, one is sacrificed)")

# ---- the beetle a smash sacrifices
beetles = pb.Bag({"inventory": {"green": 2, "purple": 1}}, cards)
pick = lambda chosen, purple=True: pb.pick_sacrifice(beetles, "x", pb.SMASH, ["pond", "ladybug"], purple, chosen)  # noqa: E731
ok(pick(None) == "green" and pick("purple") == "purple" and pick("green") == "green", "the bot's choice, or the one picked")
ok(pick("purple", purple=False) is None, "a Purple Beetle picked while the game won't take one: the smash waits")
ok(pb.pick_sacrifice(pb.Bag({"inventory": {"green": 2}}, cards), "x", pb.SMASH, ["pond"], True, "purple") is None, "no Purple Beetle: the smash waits")
ok(pb.pick_sacrifice(beetles, "x", pb.ASSEMBLE, ["pond"], True, "purple") == "", "an assemble sacrifices nothing")
ok(smash_first[1] == pb.SMASH, f"(the step picked is a smash: {nectar})")
greens_only = pb.Bag({"inventory": {"cucumber": 1, "pollen_uncommon": 1, "green": 3, tin: 1}, "crafting_slot3_unlocked": True, "hammers": fresh(tin)}, cards)
ok(pb.next_craft(greens_only, True, picked(sacrifice={nectar: "purple"}), {}) is None
   and pb.craft_wait(greens_only, True, picked(sacrifice={nectar: "purple"}), {}) == "picked_beetle", "waiting for the beetle picked: the dashboard says so")

# ---- the hammer a smash uses
both = pb.Bag({"inventory": {**inv, bronze: 1}, "crafting_slot3_unlocked": True, "hammers": fresh(tin, bronze)}, cards)
ok(pb.next_craft(both, True, picked(first=nectar), {})[3] == tin and pb.next_craft(both, True, picked(first=nectar, hammer={nectar: bronze}), {})[3] == bronze,
   "the hammers in their order, or the one picked")
worn = pb.Bag({"inventory": {**inv, bronze: 1}, "crafting_slot3_unlocked": True, "hammers": [*fresh(tin), {"hammer": bronze, "break_rate": 50, "base_break_rate": 0}]}, cards)
low = {**base, "hammer_limits": {tin: 95, bronze: 25}}  # (the Bronze Hammer at 50%: over its limit)
ok(pb.next_craft(worn, True, picked(low, first=nectar), {})[0] == nectar and pb.next_craft(worn, True, picked(low, first=nectar, hammer={nectar: bronze}), {})[0] != nectar,
   "the one picked is at its limit: that smash waits (though the Tin Hammer could do it), the rest go on")
ok(pb.craft_wait(only, True, picked(hammer={pb.next_craft(only, True, base, {})[0]: "hammer_t5"}), {}) == "picked_hammer", "a hammer you don't have: the dashboard says why it waits")
ok(smash_first[5:] == ("nectar", 0), f"the next craft says what the plan wants from it, and that it's the plan's own ({smash_first[5:]})")
ok(pb.describe_craft(both, pb.SMASH, ["cucumber", "pollen_uncommon"], tin, "green", ["aiming for Nectar"])[1].endswith("break chance; aiming for Nectar)"),
   "a failed smash's line says what it was aiming for")

# ---- the setting, read back from what the dashboard saved
(tmp / "overrides.json").write_text(json.dumps({"steps": {"task": task, "skipped": [junk, 5], "first": nectar, "sacrifice": {nectar: "purple", junk: "red"},
                                                          "hammer": {nectar: bronze, junk: "hammer_t9"}}}))
ok(pb.load_settings()["steps"] == {"task": task, "skipped": [junk], "first": nectar, "sacrifice": {nectar: "purple"}, "hammer": {nectar: bronze}},
   "read back, without anything that isn't a step, a beetle or a hammer")
(tmp / "overrides.json").write_text(json.dumps({"steps": "nonsense"}))
ok(pb.load_settings()["steps"] is None, "a broken one is ignored")

# ---- the dashboard's requests
(tmp / "overrides.json").write_text(json.dumps({"make": make}))
overrides = pb.read_json(pb.OVERRIDES_FILE)
step = lambda **request: dashboard.pick_for_step(overrides, {"task": "make", **request})  # noqa: E731
ok(step(label=nectar, skip=True) and overrides["steps"]["task"] == task and overrides["steps"]["skipped"] == [nectar], "a skip is saved for this Auto make")
ok(step(label=nectar, skip=True) and overrides["steps"]["skipped"] == [nectar], "twice is still once")
ok(step(label=nectar, first=True) and overrides["steps"]["first"] == nectar and overrides["steps"]["skipped"] == [], "work now: it's first, and not skipped")
ok(step(label=junk, first=False) and overrides["steps"]["first"] == nectar, "back in order for another step leaves it be")
ok(step(label=nectar, skip=True) and overrides["steps"]["first"] is None, "skipped: not first any more")
ok(step(label=nectar, skip=False) and overrides["steps"]["skipped"] == [], "unskip puts it back")
ok(step(label=nectar, sacrifice="purple") and step(label=nectar, hammer=bronze) and overrides["steps"]["sacrifice"] == {nectar: "purple"}
   and overrides["steps"]["hammer"] == {nectar: bronze}, "a smash's beetle and hammer are saved")
ok(step(label=nectar, sacrifice=None) and overrides["steps"]["sacrifice"] == {}, "and null gives the choice back to the bot")
ok(not step(label=junk, hammer=bronze) and not step(label=junk, sacrifice="green"), "an assemble has no beetle or hammer to pick")
ok(not step(label=nectar, sacrifice="red") and not step(label=nectar, hammer="hammer_t9") and not step(label=nectar), "only real beetles and hammers, and something to pick")
ok(not step(label="no such recipe", skip=True) and not step(label=nectar, skip="yes"), "only recipes in the book, and skip is true or false")
ok(not dashboard.pick_for_step(overrides, {"task": "goal", "label": nectar, "skip": True}), "only for what's being worked on (no quest picked)")
overrides["steps"] = {"task": "make something_else@0", "skipped": [junk], "first": None, "sacrifice": {}, "hammer": {}}
ok(step(label=nectar, skip=True) and overrides["steps"]["task"] == task and overrides["steps"]["skipped"] == [nectar], "another Auto make's picks are left behind")
ok.done()
