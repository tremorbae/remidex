"""Quests: every one is well formed (its icon, guide and crafting goal point at real things), and progress comes from what
you hold, your game, your friends and the history's all-time counts (the catalog and recipe book; no network)."""
import json
import support
pb = support.load()
ok = support.Checks()
import quests  # noqa: E402

book, catalog = pb.recipe_book()
ids = [q[1] for q in quests.QUESTS]
ok(len(ids) == len(set(ids)) and len(ids) >= 130, f"{len(ids)} quests, none twice")
ok(all(len(q[4]) == 1 or q[4] in catalog for q in quests.QUESTS), "every icon is an item (or a character)")
ok(set(quests.GUIDES) <= set(ids) and set(quests.GOALS) <= set(ids) and all(i in quests.GUIDES for i in ids), "every quest has a guide; no guide or goal is left over")


def real(target):
    keys = target if isinstance(target, list) else [target]
    return all(k is None or k in catalog or k.startswith("@") and k[1:] in book["groups"] or k in ("slot3", "pinned") or k.startswith("own:") and k[4:] in catalog
               for k in keys)


ok(all(real(t) for steps in quests.GUIDES.values() for _, t in steps), "every guide step points at something real")
ok(all(real(n) for needs in quests.GOALS.values() for n in needs), "every quest the bot can do points at something real")
mokia = [t for t, _ in quests.GUIDES["trophy_mokia"]]
ok(mokia[-3:] == ["Assemble the Roman Dodecahedron (its recipe is below).", "Assemble the Titanium Cube (its recipe is below).", "Assemble the Mokia (its recipe is below)."],
   f"a trophy's guide makes what it's made from first, then it ({mokia[-3:]})")

# progress
items = [{"key": k, "category": v.get("category"), "tier": v.get("tier"), "discovered": k in ("pond", "pollen_common"), "count": 12 if k == "junk_cube_t1" else 0}
         for k, v in catalog.items()]
game = {"level": 34, "beetle_count": 760}
state = {"friends": [f"f{n}" for n in range(379)]}
totals = {"crafts": 450, "smashes": 244, "hunts": 238, "pokes": 2730, "pokes_received": 595, "viewed_you": 950, "junk_cubes": 196, "hammers_broken": 39, "streak": 9}
done = {}
shown = {q["id"]: q for q in quests.check(items, game, state, 0, done, totals)[0]}
have = lambda qid: (shown[qid]["have"], shown[qid]["need"])  # noqa: E731
ok(have("pokes_1000") == (1000, 1000) and have("pokes_5000") == (2730, 5000), f"pokes from the history ({have('pokes_5000')})")
ok(have("hammer_broke") == (1, 1) and have("hammers_broke_25") == (25, 25), "broken hammers too")
ok(have("streak_7") == (7, 7) and have("streak_30") == (9, 30), f"the cheese streak ({have('streak_30')})")
ok(have("friends_250") == (250, 250) and have("friends_500") == (379, 500), f"friends ({have('friends_500')})")
ok(have("beetles_500") == (500, 500) and have("beetles_1000") == (760, 1000), "beetles caught")
ok(have("level_30") == (30, 30) and have("level_40") == (34, 40), "levels")
ok(have("cubes_10") == (10, 10) and have("pond") == (1, 1) and have("tin_pollen") == (1, 1) and have("moss") == (0, 1), "what you hold or have found")
ok("pokes_1000" in done and "pokes_5000" not in done, "the ones reached are noted as done")
nothing = {q["id"]: q for q in quests.check(items, game, state, 0, {}, None)[0]}
ok(nothing["pokes_100"]["have"] == 0 and nothing["streak_7"]["have"] == 0, "no history yet: nothing counted, no error")
ok(json.dumps(shown), "it all goes to the page as JSON")
ok.done()
