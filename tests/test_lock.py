"""Items switched off on the dashboard: the bot never uses them in a craft, a sacrifice or a hammer swing, and still
knows what you really have (made-up bags; the real recipe book)."""
import json
import tempfile
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
support.patch(CRAFT_STATS_FILE=tmp / "stats.json", REMOVED_FILE=tmp / "removed.json", OVERRIDES_FILE=tmp / "overrides.json")
book, catalog = pb.recipe_book()
key = lambda name: next(k for k, v in catalog.items() if v.get("name") == name)  # noqa: E731
H = lambda k, r: {"hammer": k, "craft_count": 0, "break_rate": r, "base_break_rate": 0}  # noqa: E731
game = lambda inv, hammers=None: {"inventory": dict(inv), "hammers": hammers or [H("hammer_t1", 0)]}  # noqa: E731
nectar, ladybug, purple, green, pebble, tin = key("Nectar"), key("Ladybug"), key("Purple Beetle"), "green", key("Smiley Pebble"), "hammer_t1"
make = {"keys": [key("Monarch")], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}
settings = lambda **kw: {"junk_cubes": False, "goal": None, "goal_again": False, "goal_paused": False, "goal_set_at": None, "make": make,  # noqa: E731
                         "hammer_order": [tin], "hammer_limits": {tin: 95}, "max_break_chance": 25, "hammer_timing": False, "locked": [], **kw}
cards = support.cards(catalog)  # what the game's cards say
bag = lambda inv, **kw: pb.Bag(game(inv, **kw), cards)  # noqa: E731
off = {}

# the setting
(tmp / "overrides.json").write_text(json.dumps({"locked": ["a_item", 3, "b_item"]}))
ok(pb.load_settings()["locked"] == ["a_item", "b_item"], "switched-off items come from what the dashboard saved")
(tmp / "overrides.json").write_text("{}")
ok(pb.load_settings()["locked"] == [], "none by default")

# the bag
b = bag({nectar: 2, green: 5})
spare = b.without([nectar, tin])
ok(spare.count(nectar) == 0 and spare.count(green) == 5 and not spare.hammers(), "without() takes the items and the hammer out")
ok(b.count(nectar) == 2 and b.hammers(), "and leaves what you really have alone")
ok(b.without([]) is b, "nothing switched off: the same bag")

# a smash, with and without an item switched off
inv = {nectar: 1, ladybug: 1, purple: 1, green: 14, tin: 1}
step = pb.next_craft(bag(inv), True, settings(), off)
ok(step and step[1] == pb.SMASH and nectar in step[2], f"to start with it smashes ({step and step[2]})")
ok(step and (ladybug in step[2] or purple in step[2]), "using a Ladybug or Purple Beetle")
for locked in ([ladybug], [purple], [nectar], [ladybug, purple]):
    used = [k for c in pb.craft_candidates(bag(inv), True, settings(locked=locked)) for k in c[2]]
    ok(not set(used) & set(locked), f"switch off {[catalog[k]['name'] for k in locked]}: no craft uses it ({len(used)} items used)")
ok(pb.next_craft(bag({nectar: 1, ladybug: 1, green: 14, tin: 1}), True, settings(locked=[ladybug]), off) is None, "with nothing else that works, it waits rather than use it")

# the case that started this: a recipe that takes "any" of a kind, and you have more than one that would do
rose, wort, thistle, pollen = key("Gallic Rose"), key("Spotted St. John's Wort"), key("Milk Thistle"), key("Bronze Pollen")
flowers = {rose: 2, wort: 1, thistle: 1, green: 14, tin: 1}
pollen_make = {**make, "keys": [pollen]}
used = lambda locked, inv=flowers: {k for c in pb.craft_candidates(bag(inv), True, settings(make=pollen_make, locked=locked)) if c[1] == pb.ASSEMBLE for k in c[2]}  # noqa: E731
ok(rose in used([]), "with nothing off, it may use the Gallic Roses")
ok(used([rose]) and rose not in used([rose]), f"with the Gallic Rose off, it makes the Pollen from other flowers ({sorted(catalog[k]['name'] for k in used([rose]))})")
step = pb.next_craft(bag(flowers), True, settings(make=pollen_make, locked=[rose]), off)
ok(step and step[1] == pb.ASSEMBLE and rose not in step[2], "and that's the craft it does next")
ok(not used([rose, wort]), "with all but one off, it waits rather than use them")

# sacrifices and hammers
ok(pb.next_craft(bag(inv), True, settings(locked=[green]), off) is None and pb.craft_wait(bag(inv), True, settings(locked=[green]), off) == "beetles", "Green Beetles off: no smash, and it says it's waiting for one")
ok(pb.next_craft(bag(inv), True, settings(locked=[tin]), off) is None and pb.craft_wait(bag(inv), True, settings(locked=[tin]), off) == "hammer", "the hammer off: no smash")

# Auto Junk Cubes
junk = {pebble: 4}
ok(any(c[0] == pb.JUNK_CUBE_CRAFT for c in pb.craft_candidates(bag(junk), True, settings(junk_cubes=True, make=None))), "loose junk goes into cubes")
ok(not list(pb.craft_candidates(bag(junk), True, settings(junk_cubes=True, make=None, locked=[pebble]))), "unless that junk is switched off")

# what you really have still counts
monarch = key("Monarch")
ok(pb.make_finished(bag({monarch: 1}), {**make, "start": 0}, book), "a finished make is still finished when the item made is switched off")
ok(pb.craft_focus(bag({monarch: 1}), True, settings(locked=[monarch]), book)[0] != "make", "so it doesn't go on making it")

# why it's waiting is about the step it does next (the first one it holds everything for), not just any step
flower = {**make, "keys": ["@flower_5"]}
ok(pb.craft_wait(bag({"bombardier": 1, "junk_cube_t1": 1, tin: 1}), True, settings(make=flower), off) == "beetles", "its next step is a smash it has the items for: waiting for a Green Beetle")
ok(pb.craft_wait(bag({"junk_cube_t1": 1, green: 3, tin: 1}), True, settings(make=flower), off) == "items", "no step it has everything for (no Adamantine Beetle yet): waiting for items, not a beetle")
bare = pb.Bag({"inventory": {"bombardier": 1, "junk_cube_t1": 1}, "hammers": []}, cards)  # (no hammer at all)
ok(pb.craft_wait(bare, True, settings(make=flower), off) == "items", "no hammer: it makes one first, so it's the loose junk for that it waits for")
ok.done()
