"""Mac alerts: which fire, which stay quiet, and that one isn't repeated (notifications captured, temp files)."""
import json
import tempfile
import time
import types
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
sent = []
support.patch(OVERRIDES_FILE=tmp / "o.json", STATE_FILE=tmp / "s.json", log=lambda m: None, notify=lambda msg, sub="": sent.append((sub, msg)))


def fresh(overrides=None):
    pb.OVERRIDES_FILE.write_text(json.dumps(overrides or {}))
    pb._SETTINGS.update(key=None)
    pb._ALERTED.clear()
    sent.clear()


fresh()
pb.alert("level_up", "Level 26!", "You're now level 26 in BeetleBoy.")
pb.alert("level_up", "Level 26!", "You're now level 26 in BeetleBoy.")
ok(sent == [("Level 26!", "You're now level 26 in BeetleBoy.")], "an alert goes out once, not twice")
pb.alert("hammer_broke", "A hammer broke", "x")
ok(len(sent) == 1, "a hammer breaking isn't one")


def at(h, m):
    return types.SimpleNamespace(time=time.time, gmtime=lambda *a: time.struct_time((2026, 10, 18, h, m, 0, 6, 291, 0)), strftime=time.strftime, sleep=time.sleep)


fresh()
import store  # noqa: E402
store.streak = lambda: 5  # (the streak comes from the history)
support.patch(time=at(10, 0))
pb.streak_watch({"cooldowns": {}})
ok(sent == [], "unclaimed at 10:00 UTC: plenty of time, quiet")
support.patch(time=at(20, 30))
pb.streak_watch({"cooldowns": {"claimUBC": 5000}})
ok(sent == [], "already claimed today: quiet")
pb.streak_watch({"cooldowns": {}})
ok(len(sent) == 1 and "3h 30m" in sent[0][1] and "5-day streak" in sent[0][1], f"unclaimed at 20:30 UTC: {sent}")
pb.streak_watch({"cooldowns": {}})
ok(len(sent) == 1, "not repeated for the same stretch of the evening")
support.patch(time=at(23, 20))
pb.streak_watch({"cooldowns": {}})
ok(len(sent) == 2 and "0h 40m" in sent[1][1], f"and once more in the last hour: {sent[1]}")
support.patch(time=time)
ok(pb.quest_title("mars_rhino") == "Red Planet" and pb.quest_title("nope") == "nope", "quest titles")
ok.done()
