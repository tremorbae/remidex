"""Working out crafts made on RemiliaNET itself from how the bag changed (real recipe book, fake bags, temp files)."""
import json
import tempfile
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
logs = []
support.patch(CRAFT_STATS_FILE=tmp / "stats.json", REMOVED_FILE=tmp / "removed.json", log=logs.append, alert=lambda *a, **k: None)
pb._REMOVED["checked"] = 0
book, catalog = pb.recipe_book()
key = lambda name: next(k for k, v in catalog.items() if v.get("name") == name)
game = lambda inv, hammers=None: {"inventory": dict(inv), "hammers": hammers or []}
H = lambda k, n, r: {"hammer": k, "craft_count": n, "break_rate": r}


def run(before, after):
    logs.clear()
    pb.note_outside_crafts(before, after, {})
    return [l for l in logs if "verified a recipe" not in l]


cube, pebble, nectar, ladybug, green, monarch, tin = key("Junk Cube"), key("Smiley Pebble"), key("Nectar"), key("Ladybug"), "green", key("Monarch"), "hammer_t1"
out = run(game({pebble: 2, "cheese": 100}), game({cube: 1, "cheese": 100}))
ok(len(out) == 1 and "Assemble Smiley Pebble + Smiley Pebble -> Junk Cube (by you, on RemiliaNET)" in out[0], f"assemble: {out}")
out = run(game({nectar: 1, ladybug: 1, green: 14, tin: 1}, [H(tin, 3, 20)]), game({monarch: 1, green: 13, tin: 1}, [H(tin, 4, 25)]))
ok(len(out) >= 1 and "Smash Nectar + Ladybug -> Monarch (by you, on RemiliaNET; Tin Hammer at 20% break chance)" in out[0], f"smash: {out}")
out = run(game({green: 14, tin: 1}, [H(tin, 3, 20)]), game({green: 13, tin: 1}, [H(tin, 4, 25)]))
ok(len(out) == 1 and "Smash failed: a Green Beetle lost (by you, on RemiliaNET; Tin Hammer at 20% break chance)" in out[0], f"failed smash: {out}")
out = run(game({nectar: 1, ladybug: 1, green: 14, tin: 1}, [H(tin, 3, 35)]), game({monarch: 1, green: 13}, []))
ok(any("The hammer shattered!" in l and "Tin Hammer at 35% break chance" in l for l in out), f"a hammer that broke on a good smash: {out}")
out = run(game({green: 14, tin: 1}, [H(tin, 3, 35)]), game({green: 13}, []))
ok(len(out) == 1 and "Smash failed" in out[0] and "shattered" in out[0], f"a hammer that broke on a failed smash: {out}")
ok(run(game({pebble: 2, "cheese": 100}), game({pebble: 2, ladybug: 1, "cheese": 80})) == [], "a hunt (cheese spent, items gained) isn't a craft")
ok(run(game({pebble: 1}), game({})) == [], "an item that just left, with nothing it could make, is left alone")
out = run(game({pebble: 4}), game({cube: 2}))
ok(len(out) == 2 and all("Junk Cube" in l for l in out), f"two crafts at once: {out}")
ok(run(game({pebble: 2}), game({pebble: 2})) == [], "no change, no craft")
stats = json.load(open(pb.CRAFT_STATS_FILE))
ok(stats.get("junk_cube_t1 from @junk + @junk", {}).get("outs", {}).get(cube) == 3, "they count in the recipe stats")
logs.clear()
pb.note_outside_crafts(game({nectar: 1, ladybug: 1, green: 14, tin: 1}, [H(tin, 3, 20)]), game({monarch: 1, green: 13, tin: 1}, [H(tin, 4, 25)]), {})
ok(not any("verified a recipe" in l for l in logs), "the verified line comes only the first time a recipe works")
ok.done()
