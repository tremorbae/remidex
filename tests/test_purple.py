"""Purple Beetles are rarer than Green ones: a smash sacrifices one only once its recipe has proven low odds (tried 3 times,
failed more than it worked), never an easy or sure one, and a refusal from the game can't stall crafting (made-up bags and
site, temp files, the real recipe book)."""
import tempfile
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
logs = []
support.patch(CRAFT_STATS_FILE=tmp / "stats.json", GAME_FILE=tmp / "game.json", LEVELS_FILE=tmp / "levels.json", REMOVED_FILE=tmp / "removed.json",
              STATE_FILE=tmp / "state.json", LOG_FILE=tmp / "log.txt", OVERRIDES_FILE=tmp / "overrides.json", log=logs.append, alert=lambda *a, **k: None)
book, catalog = pb.recipe_book()
cards = support.cards(catalog)
moss = "pinecone or moss or gunpowder from @beetle_4 + pollen_rare"
stats = lambda n, worked: {moss: {"n": n, "outs": {"moss": worked} if worked else {}}}  # noqa: E731


def proven(n, worked):
    pb.write_json(pb.CRAFT_STATS_FILE, stats(n, worked)); pb._STATS["checked"] = 0
    return pb.proven_low_odds(moss)


# ---- proving a recipe low odds
ok(not proven(0, 0) and not proven(2, 0), "not before it's been tried 3 times")
ok(proven(3, 0) and proven(3, 1), "3 tries that failed more than they worked: low odds")
ok(not proven(3, 2) and not proven(10, 5), "worked as often as it failed, or more: not low odds")
pb.write_json(pb.CRAFT_STATS_FILE, stats(2, 0)); pb._STATS["checked"] = 0
pb.note_craft({}, None, moss, "Smash Pond Beetle + Mithril Pollen", None)
ok(pb.read_json(pb.CRAFT_STATS_FILE)[moss].get("low") is True, "the third failed try proves it, and that's noted")
for _ in range(5):
    pb.note_craft({}, None, moss, "Smash Pond Beetle + Mithril Pollen", {"beetle": "moss", "beetle_name": "Moss"})
pb._STATS["checked"] = 0
ok(pb.proven_low_odds(moss), "and it stays proven, even after a Purple Beetle's lucky streak (5 of 8 worked)")

# ---- which beetle a smash sacrifices
bag = lambda inv: pb.Bag({"inventory": inv, "hammers": []}, cards)  # noqa: E731
easy = "monarch from nectar + ladybug"
proven(3, 0)
ok(pb.pick_sacrifice(bag({"green": 2, "purple": 2}), moss, pb.SMASH, ["pond", "pollen_rare"]) == "purple", "a proven low-odds smash: a Purple Beetle")
ok(pb.pick_sacrifice(bag({"green": 2}), moss, pb.SMASH, ["pond", "pollen_rare"]) == "green", "no Purple one: a Green one")
ok(pb.pick_sacrifice(bag({"green": 2, "purple": 2}), easy, pb.SMASH, ["nectar", "ladybug"]) == "green", "an easy or sure smash: a Green one, even with Purple ones there")
ok(pb.pick_sacrifice(bag({"purple": 5}), easy, pb.SMASH, ["nectar", "ladybug"]) is None, "and with only Purple ones it waits rather than spend one")
ok(pb.pick_sacrifice(bag({"purple": 1, "green": 1}), moss, pb.SMASH, ["purple", "pollen_rare"]) == "green", "a Purple Beetle the craft itself uses isn't also sacrificed")
ok(pb.pick_sacrifice(bag({"green": 2, "purple": 2}), moss, pb.SMASH, ["pond", "pollen_rare"], purple=False) == "green", "switched off after a refusal: Green")
ok(pb.pick_sacrifice(bag({"purple": 2}), moss, pb.ASSEMBLE, ["a", "b"]) == "", "an assemble sacrifices nothing")


# ---- the bot sends it, says it, and a refusal can't stall crafting
class Site:
    def __init__(self, answer, status=200, greens=2):
        self.answer, self.status, self.sent, self.greens, self.purples = answer, status, [], greens, 2

    def game(self):
        return {"inventory": {"pond": 1, "pollen_rare": 1, "green": self.greens, "purple": self.purples, "hammer_t1": 1}, "crafting_slot3_unlocked": True,
                "hammers": [{"hammer": "hammer_t1", "break_rate": 0, "base_break_rate": 0, "craft_count": 0}]}

    def api(self, method, path, body=None):
        return (200, self.game()) if path == "/api/beetle/user" else (200, cards) if path == "/api/beetle/cards" else (200, {})

    def signed_api(self, method, path, body=None):
        self.sent.append(body["sacrifice"])
        if self.status != 200:
            return self.status, {"error": "Invalid sacrifice"}
        self.purples -= body["sacrifice"] == "purple"; self.greens -= body["sacrifice"] == "green"
        return 200, {"message": self.answer, "result": {}, "user": self.game()}

    def wait(self, s):
        pass


settings = {"junk_cubes": False, "goal": None, "goal_again": False, "goal_paused": False, "goal_set_at": None, "locked": [],
            "make": {"keys": ["moss"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False}, "skipped": None,
            "hammer_order": ["hammer_t1"], "hammer_limits": {"hammer_t1": 95}, "max_break_chance": 25, "hammer_timing": False}
proven(3, 0)
site, state = Site("UNLUCKY_ROLL"), {"handle": "me"}
pb.beetlecraft(site, state, False, settings)
ok(site.sent[:2] == ["purple", "purple"], f"a proven low-odds smash sacrifices the Purple Beetles first ({site.sent})")
ok(site.sent[2:] == ["green", "green"], "then Green ones")
ok(any("sacrificing a Purple Beetle" in l and "the Purple Beetle is gone" in l for l in logs), "and the log says which went")
proven(0, 0)
site, state = Site("UNLUCKY_ROLL", greens=2), {"handle": "me"}
pb.beetlecraft(site, state, False, settings)
ok(site.sent == ["green", "green"], f"a new recipe isn't proven by 2 tries: out of Green ones, it waits rather than spend a Purple one ({site.sent})")
proven(0, 0)
site, state = Site("UNLUCKY_ROLL", greens=3), {"handle": "me"}
pb.beetlecraft(site, state, False, settings)
ok(site.sent[:3] == ["green", "green", "green"], f"a new recipe: its first 3 tries are Green ({site.sent})")
ok(site.sent[3:] == ["purple", "purple"], "3 failures prove it low odds: then Purple ones")
proven(3, 0)
site, state, logs[:] = Site("UNLUCKY_ROLL", status=400), {"handle": "me"}, []
pb.beetlecraft(site, state, False, settings)
ok(site.sent == ["purple"] and state["craft_off"].get(pb.PURPLE_OFF), f"the game refuses a Purple Beetle: switched off for a day ({site.sent})")
site.status = 200
state["craft_check_at"] = 0
pb.beetlecraft(site, state, False, settings)
ok(site.sent[1:3] == ["green", "green"], f"and crafting carries on with Green ones ({site.sent})")
ok.done()
