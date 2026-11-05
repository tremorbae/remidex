"""Recipes added from beetle.wiki: the Striped Cucumber Beetle is tried for Nectar only (never turned into a flower), and
if the game refuses it, only that recipe goes (made-up bags; the real recipe book; temp files)."""
import tempfile
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
from remidex.recipes import LOW_ODDS_TRIES, makes, odds  # noqa: E402
tmp = Path(tempfile.mkdtemp())
support.patch(CRAFT_STATS_FILE=tmp / "stats.json", REMOVED_FILE=tmp / "removed.json", OVERRIDES_FILE=tmp / "overrides.json", log=lambda m: None)
pb._REMOVED["checked"] = 0
book, catalog = pb.recipe_book()
cards = support.cards(catalog)
tin = "hammer_t1"
bag = lambda inv: pb.Bag({"inventory": {**inv, "green": 3, tin: 1}, "hammers": [{"hammer": tin, "craft_count": 0, "break_rate": 0, "base_break_rate": 0}]}, cards)  # noqa: E731
settings = lambda make: {"junk_cubes": False, "goal": None, "goal_again": False, "goal_paused": False, "goal_set_at": None, "locked": [],  # noqa: E731
                         "make": {"keys": [make], "recipe": None, "set_at": 1.0, "start": 0, "paused": False},
                         "hammer_order": [tin], "hammer_limits": {tin: 95}, "max_break_chance": 25, "hammer_timing": False}
cucumber = "nectar or cattail from cucumber + pollen_uncommon"

step = pb.next_craft(bag({"cucumber": 1, "pollen_uncommon": 1}), True, settings("nectar"), {})
ok(step and step[2] == ["cucumber", "pollen_uncommon"], f"for Nectar it tries the Cucumber Beetle ({step})")
ok(not pb.in_group("cucumber", "beetle_3", book, catalog), "it isn't one of Any Bronze Beetle, so nothing else uses it")
step = pb.next_craft(bag({"cucumber": 1, "junk_cube_t1": 1}), True, settings("@flower_3"), {})
ok(not step or "cucumber" not in step[2], f"and it's never smashed into a flower ({step})")
step = pb.next_craft(bag({"nectar": 1, "purple": 1}), True, settings("monarch"), {})
ok(step and sorted(step[2]) == ["nectar", "purple"], f"a Purple Beetle makes a Monarch ({step})")

pin = pb.recipe_named("trophy_green from specimen_pin + @beetle")
ok(makes(pin, "trophy_harlequin", book, catalog) and makes(pin, "trophy_green", book, catalog), "pinning any beetle is what the Specimen Pin makes")
ok(not makes(pin, "trophy_chinese_coin", book, catalog), "and a trinket trophy isn't")
for label in ("nectar or cattail from @beetle_3 + pollen_uncommon", cucumber, "pinecone or moss or gunpowder from @beetle_4 + pollen_rare"):
    ok(odds(pb.recipe_named(label)) == 1 / LOW_ODDS_TRIES, f"the artifact smashes are planned as low odds until tried ({label})")
ok(catalog["bumblebee"]["name"] == "Bumblebee" and catalog["whipscorpion"]["tier"] == 6, "the catalog has the game's names, and the Tailless Whipscorpion")

total = len(book["recipes"])
pb.remove_recipe(cucumber, "Smash Striped Cucumber Beetle + Bronze Pollen")
book = pb.recipe_book()[0]
ok(len(book["recipes"]) == total - 1 and not pb.recipe_named(cucumber), "refused: that recipe goes")
ok(pb.recipe_named("nectar or cattail from @beetle_3 + pollen_uncommon"), "and the Bronze Beetle one stays")
ok.done()
