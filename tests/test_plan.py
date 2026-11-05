"""An Auto make keeps its own record of crafts: picking it again carries on, and a finished one's record is put away
(made-up state, temp files)."""
import tempfile
import time
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
support.patch(CRAFT_STATS_FILE=tmp / "stats.json", LOG_FILE=tmp / "log.txt", log=lambda m: None)
clock = lambda s: time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))  # noqa: E731

make = {"keys": ["mars_rhino"], "recipe": 63, "set_at": 1000.0, "start": 0, "paused": False}
settings = {"make": make, "goal": None, "goal_set_at": None}
ok(pb.task_id("make", settings) == "make mars_rhino@0", "an Auto make is its item and how many you held")
ok(pb.task_id("make", {**settings, "make": {**make, "set_at": 9999.0, "recipe": None}}) == pb.task_id("make", settings), "picked again later: the same record")
ok(pb.task_id("make", {**settings, "make": {**make, "start": 1}}) != pb.task_id("make", settings), "once you've made one, the next is a fresh record")
ok(pb.task_id("make", {"make": None}) is None, "no Auto make, no id")

# ---- records
state, task = {}, pb.task_id("make", settings)
cube = {"beetle": "junk_cube_t1", "beetle_name": "Junk Cube"}
pb.note_craft(state, task, "x", "Assemble A + B", cube)
pb.note_craft(state, task, "x", "Smash C + D", None)
record = pb.task_record(state, task)
ok(record["count"] == 2 and pb.made_count(record) == 1 and len(record["done"]) == 2, "an unlucky smash is a craft tried, not a step done")
pb.note_craft(state, "goal quest 5", "x", "Assemble E + F", cube)
ok(pb.task_record(state, task)["count"] == 2 and pb.task_record(state, "goal quest 5")["count"] == 1, "crafts for something else don't wipe it")
for n in range(pb.TASK_LOGS_KEPT + 2):
    pb.note_craft(state, f"goal other {n}", "x", "Assemble G + H", cube)
ok(len(state["task_logs"]) == pb.TASK_LOGS_KEPT and task not in state["task_logs"], "the least recently worked on records go first")
pb.note_craft(state, "goal aims", "x", "Smash Pond Beetle + Mithril Pollen", None, "Pinecone", 2)
ok(pb.task_record(state, "goal aims")["done"][-1][1:] == ["Smash Pond Beetle + Mithril Pollen", None, "Pinecone", 2], "a craft keeps what it was aiming for, and that it was made for a waiting step")

# ---- a Tin Hammer made, then broken, then made again: each Auto make shows only its own crafts
from remidex import recipes  # noqa: E402
support.patch(alert=lambda *a, **k: None, OVERRIDES_FILE=tmp / "overrides.json")  # (no Mac notification from a test)
book = pb.recipe_book()[0]
hammer = {"keys": ["hammer_t1"], "recipe": None, "set_at": clock("2026-10-22 00:10:00"), "start": 0, "paused": False}
settings, tin = {"make": hammer, "goal": None}, {"beetle": "hammer_t1", "beetle_name": "Tin Hammer"}
state = {}
task = pb.task_id("make", settings)
pb.note_craft(state, task, "x", "Assemble Junk Cube + Junk Cube", tin)
bag = pb.Bag({"inventory": {"hammer_t1": 1}}, {"hammer_t1": {"category": 4, "rarity": 2, "beetle_name": "Tin Hammer"}})
recipes.note_make_done(state, settings, bag, book)
ok(not pb.task_record(state, task) and pb.task_record(state, pb.finished_key(task, hammer))["count"] == 1, "made: its record is put away (still there for the dashboard)")
again = {**hammer, "set_at": clock("2026-10-22 05:33:00")}  # (it broke: 0 held again, so the same id)
pb.note_craft(state, pb.task_id("make", {"make": again}), "x", "Assemble Junk Cube + Junk Cube", None)
ok(pb.task_record(state, task)["count"] == 1, "the next Tin Hammer starts afresh")

# ---- the plan's recipe tree shows the whole recipe: what you hold, it shows how it's made (done)
import dashboard  # noqa: E402
book, catalog = pb.recipe_book()
key = lambda name: next(k for k, v in catalog.items() if v.get("name") == name)  # noqa: E731
cards = support.cards(catalog)
cube, monarch = key("Junk Cube"), key("Monarch")
bag = pb.Bag({"inventory": {cube: 1, monarch: 1, "green": 3}, "hammers": [{"hammer": "hammer_t1", "break_rate": 0}]}, cards)
steps = [{"items": [monarch, cube], "from": [None, None]}, {"items": ["green", "@junk"], "from": [None, "wait"]}, {"unlock": True}]
held = dashboard.held_plans(steps, bag, True, book, catalog)
ok(set(held) == {cube, monarch}, f"what the plan takes from your bag, and is crafted, gets its recipe ({sorted(held)})")
ok(held[cube][-1]["out"] == cube and held[cube][-1]["mode"] == "assemble", "a Junk Cube: the assemble that makes it, last")
ok(held[monarch][-1]["out"] == monarch and all(set(s) >= {"recipe", "mode", "in", "from", "out"} for s in held[monarch]), "a Monarch: the steps that make it, the way the page shows steps")
ok("green" not in held, "what only hunts and claims bring has none")
ok(dashboard.held_plans([], bag, True, book, catalog) == {}, "nothing held, nothing to show")
fit = pb.craft_specs("smash", [key("Ladybug"), key("Nectar")], monarch, book, catalog)
ok(fit and monarch in fit[0]["out"] and fit[1] == ["ladybug", "nectar"], f"a craft from the log is matched to its recipe ({fit and fit[1]})")
# ---- "any ..." slots leave alone what a later step asks for by name
bronze = pb.Bag({"inventory": {"ladybug": 2, "purple": 1, "hammer_t1": 1}, "hammers": []}, cards)
ok(pb.Planner(bronze, book, catalog, True).take("@beetle_3")[0] == "ladybug", "an any slot takes what you hold most of")
ok(pb.Planner(bronze, book, catalog, True, reserve={"ladybug": 2}).take("@beetle_3")[0] == "purple", "but not a Ladybug that's reserved for a step that names it")
ok(pb.Planner(bronze, book, catalog, True, reserve={"ladybug": 2, "purple": 1}).take("@beetle_3") is None, "with all reserved, an any slot takes none: it's made instead")
only = pb.Bag({"inventory": {"pincushion": 1}, "hammers": []}, cards)
ok(pb.Planner(only, book, catalog, True, reserve={"pincushion": 1}).take("@flower_5") is None, "an Adamantine Pincushion kept for a later step isn't taken by an any-Adamantine-flower slot")
ok(pb.Planner(only, book, catalog, True, reserve={"pincushion": 1}).take("pincushion")[0] == "pincushion", "but the step that names it gets it")
ok(pb.Planner(only, book, catalog, True).take("@flower_5")[0] == "pincushion", "with nothing reserved it's an Adamantine flower like any other")
both = pb.Bag({"inventory": {"pincushion": 1, "gazania": 1}, "hammers": []}, cards)
ok(pb.Planner(both, book, catalog, True, reserve={"pincushion": 1}).take("@flower_5")[0] == "gazania", "with another to spare, the any slot takes that one")
first = pb.Planner(bronze, book, catalog, True)
first.obtain("@beetle_3"); first.obtain("ladybug"); first.obtain("ladybug"); first.obtain("ladybug")
ok(first.named == {"ladybug": 3}, f"the first pass notes every Ladybug asked for by name, even ones it had to find elsewhere ({dict(first.named)})")
seen = []
plan = pb.two_pass(lambda reserve: seen.append(reserve) or (pb.Planner(bronze, book, catalog, True, reserve=reserve) if reserve else first))
ok(len(seen) == 2 and seen[1] == {"ladybug": 2}, f"two passes, reserving what you hold of it: 2, not 3 ({seen})")
none = pb.Planner(bronze, book, catalog, True)
ok(pb.two_pass(lambda reserve: none) is none, "one pass when nothing was")
# ---- quests plan the same way: a held Gazania the Sunset Moth needs isn't rerolled as "any Adamantine flower"
inv = {"gazania": 1, "junk_cube_t2": 1, "junk_cube_t1": 4, "green": 30, "hammer_t1": 1, "goliath": 1, "bombardier": 1, "cattail": 1}
held = pb.Bag({"inventory": inv, "crafting_slot3_unlocked": True, "hammers": [{"hammer": "hammer_t1", "break_rate": 0, "base_break_rate": 0}]},
              {k: cards[k] for k in inv})  # (the game has cards only for what you've found)
quest = {"goal": "mars_rhino", "goal_again": False, "goal_set_at": 1, "locked": []}
plan = pb.plan_goal(held, True, quest, book, catalog)
uses = [(book["recipes"][s["recipe"]]["in"][i], s["out"]) for s in plan.steps if not s.get("unlock") for i, (k, f) in enumerate(zip(s["items"], s["from"])) if k == "gazania" and f is None]
ok(uses == [("gazania", "sunset_moth")], f"the quest plan keeps the Gazania for the Sunset Moth ({uses})")
ok(all(s[2] != ["junk_cube_t2", "gazania"] for s in pb.goal_crafts(held, True, quest, book, catalog)), "and the bot never offers to reroll it")
locked = pb.plan_goal(held, True, {**quest, "locked": ["gazania"]}, book, catalog)
ok(not any(k == "gazania" and f is None for s in locked.steps if not s.get("unlock") for k, f in zip(s["items"], s["from"])), "a switched-off item is never used")
start = {"junk_cube_t1": 2, "marigold": 1}
key = pb.plan_goal(pb.Bag({"inventory": start, "hammers": []}, {k: cards[k] for k in start}), False, {"goal": "slot3", "goal_again": False, "locked": []}, book, catalog)
ok(any(s.get("unlock") for s in key.steps) and list(key.ready()), f"a plan with the slot-3 unlock in it still lists its ready crafts ({list(key.ready())[:1]})")

# ---- an older record's failed smash: the console shows what its recipe makes, from the items it used
old = [[1.0, "Smash Pond Beetle + Mithril Pollen", None], [2.0, "Assemble Pebble + Pebble", "Junk Cube"], [3.0, "Smash Pond Beetle + Mithril Pollen", None, "Pinecone", True]]
shown = dashboard.aimed(old, pb.Bag({"inventory": {}}, cards), book, catalog)
ok(shown[0][3:] == ["Pinecone or Moss or Gunpowder", None] and shown[1:] == old[1:], f"older failed smashes get it, the rest stay as they are ({shown[0][3:]})")

# ---- an Auto make of something only a recipe that burns Green Beetles makes (a Purple Beetle): it's planned anyway
greens = pb.Bag({"inventory": {"green": 6, "hammer_t1": 1}, "crafting_slot3_unlocked": True, "hammers": [{"hammer": "hammer_t1", "break_rate": 0, "base_break_rate": 0}]}, cards)
purple = pb.plan_make(greens, True, {"keys": ["purple"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}, book, catalog)
ok([s["label"] for s in purple.steps] == ["@beetle_3 from @beetle_2 + @beetle_2"] and purple.steps[0]["items"] == ["green", "green"],
   f"a Purple Beetle: smashed from two Green Beetles ({[s['label'] for s in purple.steps]})")
inside = pb.Planner(greens, book, catalog, True)
ok(inside.obtain("purple") == ("purple", "wait") and not inside.steps, "as a step toward something else, it still waits for hunts")
ok.done()
