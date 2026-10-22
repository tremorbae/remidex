"""An Auto make keeps its own record of crafts: picking it again carries on, and the crafts from before (kept in the log) are
put back once (made-up log and state, temp files)."""
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
ok(pb.made_count({"done": [[1, "a", "X"], [2, "b", None]]}) == 1, "an older record's steps done are counted from its crafts")

# ---- the rebuild
when = clock("2026-10-18 20:23:00")
make = {**make, "set_at": when}
settings = {"make": make, "goal": None}
pb.LOG_FILE.write_text("\n".join([
    "2026-10-17 21:00:00 BeetleCraft: Assemble Old + Old -> Before.",
    "2026-10-17 22:55:59 BeetleCraft: Auto make is done (Tin Hammer). Next one from the queue.",
    "2026-10-17 23:10:00 BeetleCraft: Assemble Glue Stick + Pebble -> Junk Cube.",
    "2026-10-17 23:10:09 BeetleCraft: Smash Monarch + Junk Cube: smash failed (sacrificing a Green Beetle; Tin Hammer at 5% break chance); the Green Beetle is gone, the rest came back.",
    "2026-10-17 23:10:15 BeetleCraft: Smash Nectar + Ladybug -> Monarch (sacrificing a Green Beetle; Tin Hammer at 10% break chance).",
    "2026-10-17 23:11:00 BeetleCraft: Assemble Hand + Made -> Thing (by you, on RemiliaNET).",
    "2026-10-17 23:12:00 BeetleCraft: verified a recipe: Assemble X -> Y.",
    "2026-10-17 23:13:00 Hunt: Green Beetle (+5 XP). 100 cheese left.",
    "2026-10-18 21:00:00 BeetleCraft: Assemble After + After -> Later.",
]) + "\n")
old = "make mars_rhino@0"
state = {"task_log": {"task": f"make {when}", "count": 1, "done": [[when + 3000, "Smash Daisy + Daisy", "Wort"]]}}
ok(pb.rebuild_task_log(state, settings) is True, "an Auto make with an old-style record is rebuilt")
rec = pb.task_record(state, old)
ok([e[1:] for e in rec["done"]] == [["Assemble Glue Stick + Pebble", "Junk Cube"], ["Smash Monarch + Junk Cube", None], ["Smash Nectar + Ladybug", "Monarch"], ["Smash Daisy + Daisy", "Wort"]],
   f"crafts since the last Auto make finished are back, then the ones it already had ({[e[1:] for e in rec['done']]})")
ok(rec["count"] == 4 and pb.made_count(rec) == 3, f"counted: {rec['count']} crafts, {pb.made_count(rec)} steps")
ok("task_log" not in state and f"make {when}" not in state["task_logs"], "the old record is gone")
ok(pb.rebuild_task_log(state, settings) is False, "and it isn't done twice")
ok(pb.rebuild_task_log({}, settings) is False and pb.rebuild_task_log(state, {"make": None}) is False, "nothing to rebuild: nothing happens")

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
# a record from before they were put away: the clean-up at the start drops what came before the log said it was done
pb.LOG_FILE.write_text("2026-10-22 00:32:23 BeetleCraft: Assemble Junk Cube + Junk Cube -> Tin Hammer.\n"
                       "2026-10-22 00:32:23 BeetleCraft: Auto make is done (Tin Hammer). Next one from the queue.\n")
state = {"task_logs": {task: {"task": task, "count": 3, "made": 2, "done": [[clock("2026-10-21 03:42:00"), "Assemble Junk Cube + Junk Cube", "Tin Hammer"],
                                                                           [clock("2026-10-22 00:32:23") + .4, "Assemble Junk Cube + Junk Cube", "Tin Hammer"],
                                                                           [clock("2026-10-22 06:00:00"), "Assemble Loose + Junk", "Junk Cube"]]}}}
ok(recipes.forget_finished(state, {"make": again}) is True and [d[2] for d in pb.task_record(state, task)["done"]] == ["Junk Cube"],
   f"the earlier hammers' crafts are dropped, this one's kept ({pb.task_record(state, task)['done']})")
ok(recipes.forget_finished(state, {"make": again}) is False, "and it isn't done twice")
state = {"task_logs": {task: {"task": task, "count": 1, "made": 1, "done": [[clock("2026-10-22 06:00:00"), "Assemble Loose + Junk", "Junk Cube"]]}}}
ok(recipes.forget_finished(state, {"make": {**again, "keys": ["mars_rhino"]}}) is False, "another item's finish doesn't touch it")

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
ok.done()
