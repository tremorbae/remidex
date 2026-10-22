"""Skipping a step of the Auto make or quest: it waits until the rest of the plan is done (the steps that need it don't
hold it up), only for that Auto make or quest, and the dashboard only saves sensible skips (made-up bags, the real recipe
book, temp files)."""
import json
import tempfile
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
import dashboard  # noqa: E402

tmp = Path(tempfile.mkdtemp())
support.patch(OVERRIDES_FILE=tmp / "overrides.json", CRAFT_STATS_FILE=tmp / "stats.json", REMOVED_FILE=tmp / "removed.json")
book, catalog = pb.recipe_book()
cards = support.cards(catalog)
tin = "hammer_t1"
make = {"keys": ["mars_rhino"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}
inv = {"pond": 1, "pollen_rare": 1, "cucumber": 1, "pollen_uncommon": 1, "ladybug": 1, "paperclip": 2, "button": 2, "green": 9, tin: 1}
bag = pb.Bag({"inventory": inv, "crafting_slot3_unlocked": True, "hammers": [{"hammer": tin, "break_rate": 0, "base_break_rate": 0}]}, cards)
base = {"junk_cubes": False, "goal": None, "goal_again": False, "goal_paused": False, "goal_set_at": None, "locked": [], "make": make,
        "hammer_order": [tin], "hammer_limits": {tin: 95}, "max_break_chance": 25, "hammer_timing": False, "skipped": None}
task = pb.task_id("make", base)

first = pb.next_craft(bag, True, base, {})
ok(first is not None, f"something to craft to start with ({first and first[0]})")
skipped = {**base, "skipped": {"task": task, "labels": [first[0]]}}
then = pb.next_craft(bag, True, skipped, {})
ok(then is not None and then[0] != first[0], f"skip it: the bot does another step first ({then and then[0]})")
order = [c[0] for c in pb.craft_candidates(bag, True, skipped)]
ok(first[0] not in order, f"and the skipped one isn't offered while the rest of the plan has steps left ({order})")
only = pb.Bag({"inventory": {"cucumber": 1, "pollen_uncommon": 1, "green": 9, tin: 1}, "crafting_slot3_unlocked": True,
               "hammers": [{"hammer": tin, "break_rate": 0, "base_break_rate": 0}]}, cards)
alone = pb.next_craft(only, True, {**base, "skipped": {"task": task, "labels": [pb.next_craft(only, True, base, {})[0]]}}, {})
ok(alone is None, "while the rest of the plan has steps left, a skipped one waits, even when nothing else can go right now")
moss = "pinecone or moss or gunpowder from @beetle_4 + pollen_rare"
lotus_bag = pb.Bag({"inventory": {"gunpowder": 1, "pinecone": 1, "pond": 1, "pollen_rare": 1, "green": 3, tin: 1}, "crafting_slot3_unlocked": True,
                    "hammers": [{"hammer": tin, "break_rate": 0, "base_break_rate": 0}]}, cards)
lotus = {**base, "make": {"keys": ["black_lotus"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}}
lotus["skipped"] = {"task": pb.task_id("make", lotus), "labels": [moss]}
step = pb.next_craft(lotus_bag, True, lotus, {})
ok(step and step[0] == moss, f"what's left only needs the skipped step (the Black Lotus needs the Moss): it's done, nothing stalls ({step and step[0]})")
alone_bag = pb.Bag({"inventory": {"pond": 1, "pollen_rare": 1, "green": 3, tin: 1}, "crafting_slot3_unlocked": True, "hammers": [{"hammer": tin, "break_rate": 0, "base_break_rate": 0}]}, cards)
only_moss = {**base, "make": {"keys": ["moss"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}}
only_moss["skipped"] = {"task": pb.task_id("make", only_moss), "labels": [moss]}
ok((pb.next_craft(alone_bag, True, only_moss, {}) or [None])[0] == moss, "and a plan that's only the skipped step does it")
other = {**base, "skipped": {"task": "make something_else@0", "labels": [first[0]]}}
ok(pb.next_craft(bag, True, other, {})[0] == first[0], "a skip for another Auto make doesn't count")

# ---- the setting, read back from what the dashboard saved
(tmp / "overrides.json").write_text(json.dumps({"skipped": {"task": task, "labels": [first[0], 5]}}))
ok(pb.load_settings()["skipped"] == {"task": task, "labels": [first[0]]}, "read back, without anything that isn't a step")
(tmp / "overrides.json").write_text(json.dumps({"skipped": "nonsense"}))
ok(pb.load_settings()["skipped"] is None, "a broken one is ignored")

# ---- the dashboard's request
(tmp / "overrides.json").write_text(json.dumps({"make": make}))
overrides = pb.read_json(pb.OVERRIDES_FILE)
ok(dashboard.skip_step(overrides, {"task": "make", "label": first[0], "skip": True}) and overrides["skipped"] == {"task": task, "labels": [first[0]]}, "a skip is saved for this Auto make")
ok(dashboard.skip_step(overrides, {"task": "make", "label": first[0], "skip": True}) and overrides["skipped"]["labels"] == [first[0]], "twice is still once")
ok(dashboard.skip_step(overrides, {"task": "make", "label": first[0], "skip": False}) and overrides["skipped"]["labels"] == [], "unskip puts it back")
ok(not dashboard.skip_step(overrides, {"task": "make", "label": "no such recipe", "skip": True}), "only recipes in the book")
ok(not dashboard.skip_step(overrides, {"task": "goal", "label": first[0], "skip": True}), "only for what's being worked on (no quest picked)")
ok(not dashboard.skip_step(overrides, {"task": "make", "label": first[0], "skip": "yes"}), "skip is true or false")
ok.done()
