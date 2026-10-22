"""Keeping the game fresh for the dashboard while it's open (fake site, temp files)."""
import tempfile
import time
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
support.patch(LOOKING_FILE=tmp / "looking.json", GAME_FILE=tmp / "game.json", LEVELS_FILE=tmp / "levels.json", log=lambda m: None)


class Site:
    def __init__(self):
        self.calls = []

    def signed_api(self, method, path, body=None):
        self.calls.append(path)
        if path == "/api/beetle/user":
            return 200, {"level": 5, "xp": 10, "inventory": {"green": 3, "new_thing": 1}}
        if path == "/api/beetle/cards":
            return 200, {"green": {"beetle_name": "Green Beetle"}, "new_thing": {"beetle_name": "New Thing"}}
        return 404, None


site, state = Site(), {"craft_check_at": 9e9}
ok(not pb.someone_looking(), "nobody looking at first")
pb.sync_game(site, state)
ok(site.calls == [], "no request when nobody's looking")
pb.write_json(pb.LOOKING_FILE, {"at": time.time()})
ok(pb.someone_looking(), "looking after the dashboard says so")
pb.write_json(pb.GAME_FILE, {"game": {"inventory": {"green": 3}}, "cards": {"green": {"beetle_name": "Green Beetle"}}, "fetched_at": 1})
pb.sync_game(site, state)
saved = pb.read_json(pb.GAME_FILE)
ok(site.calls == ["/api/beetle/user", "/api/beetle/cards"], f"game and the new item's card read: {site.calls}")
ok(saved["game"]["inventory"].get("new_thing") == 1 and "new_thing" in saved["cards"] and saved["fetched_at"] > 1, "saved for the dashboard")
ok(state["craft_check_at"] == 0, "crafting looks again because the bag changed")
n = len(site.calls)
pb.sync_game(site, state)
ok(len(site.calls) == n, "not again within the minimum gap")
pb._SYNCED["at"] = 0
state["craft_check_at"] = 9e9
pb.sync_game(site, state)
ok(site.calls[n:] == ["/api/beetle/user"] and state["craft_check_at"] == 9e9, "same bag: just the game, crafting left alone")
before = pb.read_json(pb.GAME_FILE)["fetched_at"]
pb._SYNCED["at"] = 0
pb.sync_game(site, state)
ok(pb.read_json(pb.GAME_FILE)["fetched_at"] == before, "nothing changed: nothing rewritten (so the dashboard doesn't redraw)")
pb.write_json(pb.LOOKING_FILE, {"at": time.time() - pb.LOOKING_FOR - 1})
ok(not pb.someone_looking(), "stops when the page goes quiet")
ok.done()
