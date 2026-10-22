"""The EXP bar: how far into a level you are, close when the bot watched that level begin, worked out from the levels
before it when it began in one big jump."""
import json
import tempfile
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
import dashboard  # noqa: E402  (after the bot is loaded, so Playwright is stubbed)
tmp = Path(tempfile.mkdtemp())
file = tmp / "levels.json"
support.patch(LEVELS_FILE=file)
dashboard.bot.LEVELS_FILE = file
save = lambda seen: file.write_text(json.dumps({"seen": {str(k): v for k, v in seen.items()}}))

save({})
ok(dashboard.level_progress(5, 900) == {"level": 5, "xp": 900, "known": False}, "nothing seen yet: no guess")

# what the bot really saw: it watched 23, 24 and 26 begin, but 27 began during one big jump in XP
real = {22: [24340, 24392], 23: [24606, 26271], 24: [26533, 26631], 25: [28145, 29972], 26: [30059, 31889], 27: [34069, 34069]}
save(real)
now = dashboard.level_progress(27, 34069)
began = now["xp"] - now["into"]
ok(now["known"] and not now["watched"] and 31889 < began <= 34069 and now["into"] < now["size"],
   f"a level it didn't see begin: worked out from the ones before, within what it knows, and not past the end ({now})")
ok(dashboard.level_progress(26, 31000)["watched"] is True, "a level it did see begin: from there")
mid = dashboard.level_progress(26, 31000)
ok(abs(mid["into"] - (31000 - 30059)) <= mid["slack"] and mid["slack"] == 44, f"how far in is close: XP minus about where the level began ({mid})")
ok(1500 <= mid["size"] <= 2400, f"the size is a sensible estimate ({mid['size']})")
ok(mid["into"] <= mid["size"], "never past the end")

# what it saw up to level 34, which began during a daily claim (+2,736 XP): levels don't grow quite evenly
later = {**real, 28: [34324, 36564], 29: [36603, 37496], 30: [39921, 41473], 31: [41583, 43735], 32: [46543, 47144], 33: [47198, 49446], 34: [52182, 53268]}
save(later)
per_level, _, off = dashboard.level_sizes(dashboard.watched_starts(later))
ok(100 <= per_level <= 200 and off < 200, f"levels grow steadily, by about {per_level:.0f} XP each ({off:.0f} XP off, typically)")
now = dashboard.level_progress(34, 53268)
ok(now["known"] and not now["watched"] and 49446 < now["xp"] - now["into"] <= 52182 and 0 < now["into"] < now["size"],
   f"level 34: a bar, worked out from level 33's start, within what's known, not past the end ({now})")
save({1: [0, 10], 2: [20, 25]})
ok(dashboard.level_sizes(dashboard.watched_starts({1: [0, 10], 2: [20, 25]})) is None, "one level seen begin: no trend yet")
ok(dashboard.level_progress(2, 22) == {"level": 2, "xp": 22, "into": 7, "size": dashboard.level_size_guess(2), "slack": 5, "known": True, "watched": True},
   "with no trend, the guess is only used for the size")

# the bot notes the XP after every action, so a level-up is pinned down to the action that caused it
class Site:
    def __init__(self, *users):
        self.users = list(users)

    def signed_api(self, method, path, body=None):
        return 200, {"success": True, "result": {}, "user": self.users.pop(0)}


save({25: [28145, 29972], 26: [30059, 31889]})
site = Site({"level": 26, "xp": 31940}, {"level": 27, "xp": 31990}, {"level": 27, "xp": 32050})
for _ in range(3):
    pb.beetle_action(site, "beetleHunt")
seen = json.loads(file.read_text())["seen"]
ok(seen["26"] == [30059, 31940] and seen["27"] == [31990, 32050], f"the XP either side of the level-up is kept ({seen})")
now = dashboard.level_progress(27, 32050)
ok(now["known"] is True and now["into"] == 32050 - 31965 and now["slack"] == 25, f"so the bar knows where the level began, to within one action's XP: {now}")
ok(pb.beetle_action(Site({"level": 27}), "beetleHunt")[2] is None, "an answer without XP is no trouble")
ok.done()
