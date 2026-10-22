"""Recipes the game says don't exist are removed for good, and Auto makes keep pointing at the right recipes (temp files)."""
import tempfile
import time
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
logs = []
support.patch(OVERRIDES_FILE=tmp / "overrides.json", REMOVED_FILE=tmp / "removed_recipes.json", STATE_FILE=tmp / "state.json", CRAFT_STATS_FILE=tmp / "craft_stats.json",
              GAME_FILE=tmp / "game.json", LEVELS_FILE=tmp / "levels.json", LOG_FILE=tmp / "log.txt", log=logs.append)
pb._REMOVED["checked"] = 0
book = pb.recipe_book()[0]
total = len(book["recipes"])
label = "@beetle_6 from @beetle_5 + @beetle_5"
target = next(n for n, r in enumerate(book["recipes"]) if pb.recipe_label(r) == label)
after_label = pb.recipe_label(book["recipes"][target + 1])
pb.write_json(pb.OVERRIDES_FILE, {"make": {"keys": ["x"], "recipe": target, "set_at": 1.0, "start": 0},
                                  "make_queue": [{"keys": ["y"], "recipe": target + 1}, {"keys": ["z"], "recipe": target - 1}, {"keys": ["w"], "recipe": None}]})
pb.remove_recipe(label, "Smash Goliath Beetle + Bombardier Beetle")
book2 = pb.recipe_book()[0]
ok(len(book2["recipes"]) == total - 1 and all(pb.recipe_label(r) != label for r in book2["recipes"]), f"book has {total} -> {len(book2['recipes'])} recipes, without it")
ok(pb.recipe_named(label) is None, "recipe_named no longer finds it")
o = pb.read_json(pb.OVERRIDES_FILE)
ok(o["make"]["recipe"] is None, "an Auto make picked by that recipe now goes by any recipe")
ok(pb.recipe_label(book2["recipes"][o["make_queue"][0]["recipe"]]) == after_label, "a later recipe number still points at the same recipe")
ok(o["make_queue"][1]["recipe"] == target - 1 and o["make_queue"][2]["recipe"] is None, "earlier numbers and 'any' are untouched")
ok(pb.read_json(pb.REMOVED_FILE)["recipes"][label]["tried"] == "Smash Goliath Beetle + Bombardier Beetle", "it remembers what was tried")
pb.remove_recipe(label)
ok(len(pb.recipe_book()[0]["recipes"]) == total - 1, "removing it twice changes nothing")
pb._BOOK = None
ok(len(pb.recipe_book()[0]["recipes"]) == total - 1, "still out after reloading the book")
pb.REMOVED_FILE.unlink()
pb._REMOVED["checked"] = 0
pb.recipe_book()
first = pb.recipe_label(pb.recipe_book()[0]["recipes"][0])


class Site:
    def api(self, method, path, body=None):
        return (200, {"inventory": {}, "hammers": []}) if path == "/api/beetle/user" else (200, {})
    signed_api = api

    def wait(self, s):
        pass


state = {"craft_off": {first: time.time() + pb.FOR_GOOD}, "handle": "me"}
settings = {"goal": None, "make": None, "hammer_order": pb.HAMMERS, "hammer_limits": {h: 25 for h in pb.HAMMERS}}
pb.beetlecraft(Site(), state, False, settings)
ok(first in (pb.read_json(pb.REMOVED_FILE).get("recipes") or {}), "an old switched-off-for-good recipe is removed at the next check")
ok(any("removed a recipe" in l for l in logs), "and the log says so")
ok.done()
