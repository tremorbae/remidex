"""The history database: reads a log once, counts right, and never twice (temp database)."""
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
import support
support.load()
import store
ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
store.DB_FILE = tmp / "t.db"
log = tmp / "log.txt"
day = time.strftime("%Y-%m-%d")
count = lambda name, on=None: store.day_counts([name], on)[name]  # noqa: E731
log.write_text(f"""{day} 10:00:00 ~a (friend): poked. Today: 1 pokes, 0 friend requests.
{day} 10:01:00 ~b (other): poked; friend request sent. Today: 2 pokes, 1 friend requests.
{day} 10:02:00 Hunt: Green Beetle + Pebble (+94 XP). 2,226 cheese left.
{day} 10:03:00 Hunt: found nothing. 2,206 cheese left.
{day} 10:04:00 Free beetle: Green Beetle + Paperclip (+56 XP).
{day} 10:05:00 Daily cheese: +350, streak 5.
{day} 10:06:00 BeetleCraft: Assemble Pebble + Pebble -> Junk Cube.
{day} 10:07:00 BeetleCraft: Smash Nectar + Ladybug -> Monarch (sacrificing a Green Beetle; Tin Hammer at 5% break chance).
{day} 10:08:00 BeetleCraft: Smash Goliath Beetle + Junk Cube: smash failed (sacrificing a Green Beetle; Tin Hammer at 10% break chance); the Green Beetle is gone, the rest came back.
{day} 10:09:00 BeetleCraft: Assemble Phone + Cube -> Junk Cube (by you, on RemiliaNET).
{day} 10:10:00 BeetleCraft: Smash failed: 2 Green Beetles lost (by you, on RemiliaNET).
{day} 10:11:00 Level up! You're now level 26.
{day} 10:12:00 Profile views from the activity feed: 7.
{day} 10:13:00 ~c (other): accepted their friend request.
""")
n = store.ingest_log(log)
t = store.totals()
ok(n == 14, f"reads every line: {n}")
ok(t["pokes"] == 2 and t["requests"] == 1 and t["hunts"] == 2, f"pokes, friend requests and hunts: {t}")
ok(t["crafts"] == 3 and t["junk_cubes"] == 2, f"crafts (the bot's and yours) and junk cubes: {t}")
ok(sorted(t) == sorted(store.COUNTERS) and count("crafts", day) == 3, "only what the dashboard shows is counted, by day")
ok(store.streak() == 5, "the streak")
ok(store.ingest_log(log) == 0 and store.totals() == t, "reading again changes nothing")
with open(log, "a") as f:
    f.write(f"{day} 10:20:00 Hunt: Cube (+10 XP). 2,186 cheese left.\n{day} 10:21:00 half a li")
ok(store.ingest_log(log) == 1 and store.totals()["hunts"] == 3, "a new line is counted, a half-written one waits")

# the bot and the dashboard both read the log in: one that looked at the log just before the other read a new line in
# doesn't take the log for cleared and count it all again
main_db, store.DB_FILE = store.DB_FILE, tmp / "race.db"
race = tmp / "race.log"
race.write_text("".join(f"{day} 08:{n:02d}:00 Hunt: found nothing.\n" for n in range(20)))
store.ingest_log(race)
other = sqlite3.connect(str(store.DB_FILE), isolation_level=None)  # the bot, reading in a line it just wrote
other.execute("BEGIN IMMEDIATE")
waiting = threading.Thread(target=store.ingest_log, args=(race,))  # the dashboard, waiting its turn
waiting.start()
time.sleep(0.5)
with open(race, "a") as f:
    f.write(f"{day} 08:30:00 Hunt: found nothing.\n")
other.execute("UPDATE daily SET hunts = hunts + 1 WHERE day = ?", (day,))
store.set_meta(other, "log_offset", race.stat().st_size)
other.execute("COMMIT")
waiting.join(20)
ok(store.totals()["hunts"] == 21, f"read in by one while the other waits: counted once ({store.totals()['hunts']} hunts, 21 lines)")

# smashes and broken hammers, for the overall break rate (the bot's and yours; the craft screen's own lines aren't counted again)
store.DB_FILE = tmp / "hammers.db"
smashes = tmp / "smashes.log"
smashes.write_text(f"""{day} 09:00:00 BeetleCraft: Smash Poppy + Poppy -> Large White Petunia (sacrificing a Green Beetle; Tin Hammer at 30% break chance). The hammer shattered!
{day} 09:00:00 BeetleCraft screen: YOU SACRIFICED A Green Beetle AND SMASHED 2 PoppyS INTO A Large White Petunia! YOUR HAMMER SHATTERED!
{day} 09:02:00 BeetleCraft: Smash Pond Beetle + Mithril Pollen: smash failed (sacrificing a Green Beetle; Tin Hammer at 20% break chance); the Green Beetle is gone, the rest came back.
{day} 09:03:00 BeetleCraft: Smash failed: 2 Green Beetles lost (by you, on RemiliaNET; Tin Hammer at 20% break chance). The hammer shattered!
{day} 09:04:00 BeetleCraft: Assemble Pebble + Pebble -> Junk Cube.
""")
store.ingest_log(smashes)
t = store.totals()
ok(t["smashes"] == 4 and t["hammers_broken"] == 2, f"smashes made and failed, two failed on RemiliaNET as two, and the hammers they broke ({t['smashes']}, {t['hammers_broken']})")
db = store.connect()  # a database that read this log before smashes were counted
db.execute("UPDATE daily SET smashes = 0, hammers_broken = 0")
db.execute("DELETE FROM meta WHERE key = 'hammers_counted'")
db.close()
with open(smashes, "a") as f:
    f.write(f"{day} 09:05:00 BeetleCraft: Smash Daisy + Daisy -> Sunflower (sacrificing a Green Beetle; Bronze Hammer at 0% break chance).\n")
store.ingest_log(smashes)
store.ingest_log(smashes)
t = store.totals()
ok(t["smashes"] == 5 and t["hammers_broken"] == 2, f"the earlier ones are found in the log once, the new one counted as it comes ({t['smashes']}, {t['hammers_broken']})")

store.DB_FILE = main_db
ok(store.log_time(f"{day} 10:00:00") > 0 and store.log_time("not a time") == 0, "log times")
store.mark_viewed("someone")
ok("someone" in store.viewed_lately(), "who was viewed")
store.feed_touch({"x", "y"})
ok(set(store.feed_recent()) == {"x", "y"}, "who the feed showed")
ok(store.migrate({"viewed_at": {"old": time.time()}}, {"seen_old": time.time()}) is True and store.migrate({}, {}) is False, "moving the old files in happens once")

# the Cheeseman's lines are kept for good (the feed only holds the last day or so of the log)
two = tmp / "two.log"
two.write_text("2026-10-10 09:00:00 Cheeseman: Glue Stick.\n2026-10-11 09:00:05 Hunt: Cube (+10 XP). 1,000 cheese left.\n2026-10-11 09:00:09 Cheeseman: Pebble + Paperclip (+3 XP).\n")
store.DB_FILE = tmp / "cheese.db"
store.ingest_log(two)
got = store.cheeseman()
ok(got == [("2026-10-10 09:00:00", "Cheeseman: Glue Stick."), ("2026-10-11 09:00:09", "Cheeseman: Pebble + Paperclip (+3 XP).")], f"his lines, oldest first ({got})")
ok(store.cheeseman(before="2026-10-11 00:00:00") == got[:1] and store.cheeseman(limit=1) == got[1:], "only those before a time, or only the newest few")
store.ingest_log(two)
ok(len(store.cheeseman()) == 2, "read again, nothing is doubled")
with open(two, "a") as f:
    f.write("2026-10-12 09:00:00 Cheeseman: Bottle Cap.\n")
store.ingest_log(two)
ok([line for _, line in store.cheeseman()][-1] == "Cheeseman: Bottle Cap." and len(store.cheeseman()) == 3, "a new one is added")
db = store.connect()  # a database from before the table existed: it has read the log, but never kept his lines
db.execute("DELETE FROM cheeseman")
db.execute("DELETE FROM meta WHERE key = 'cheeseman_kept'")
db.close()
store.ingest_log(two)
ok(len(store.cheeseman()) == 3, "his earlier lines are found in the log once, when the table is new")

# views of your profile, counted as each person's count grows
store.DB_FILE = tmp / "views.db"
day0 = time.mktime(time.strptime("2026-10-10 12:00:00", "%Y-%m-%d %H:%M:%S"))
store.record_views([("a", 3, day0), ("b", 1, day0), ("c", 2, time.time())])
t = store.totals()["viewed_you"]
ok(t == 6 and count("viewed_you") == 2 and count("viewed_you", "2026-10-10") == 4, f"the first time: everything seen, each on its own day ({t})")
store.record_views([("a", 3, day0), ("b", 4, day0), ("d", 1, time.time())])
ok(store.totals()["viewed_you"] == 10 and count("viewed_you") == 2 + 3 + 1, "later: only what grew, on the day it was seen")
store.record_views([("a", 3, day0)])
ok(store.totals()["viewed_you"] == 10, "the same again changes nothing")
store.record_views([])
ok(count("viewed_you", "1999-01-01") == 0, "a day with none")
try:
    store.day_counts(["pokes", "pokes; drop table daily"])
    ok(False, "only a counter's name is accepted")
except ValueError:
    ok(True, "only a counter's name is accepted")
# a database from before views were counted
store.DB_FILE = tmp / "old.db"
old_db = sqlite3.connect(str(store.DB_FILE))
old_db.executescript("CREATE TABLE daily (day TEXT PRIMARY KEY, pokes INTEGER NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0, accepted INTEGER NOT NULL DEFAULT 0, hunts INTEGER NOT NULL DEFAULT 0, crafts INTEGER NOT NULL DEFAULT 0, junk_cubes INTEGER NOT NULL DEFAULT 0, failed_smashes INTEGER NOT NULL DEFAULT 0, free_beetles INTEGER NOT NULL DEFAULT 0, cheese_claimed INTEGER NOT NULL DEFAULT 0, xp_gained INTEGER NOT NULL DEFAULT 0, views_made INTEGER NOT NULL DEFAULT 0, level_ups INTEGER NOT NULL DEFAULT 0); INSERT INTO daily(day, pokes) VALUES ('2026-10-01', 7);")
old_db.commit(); old_db.close()
store._ready.discard(str(store.DB_FILE))
t = store.totals()
ok(t["pokes"] == 7 and t["viewed_you"] == 0, "an older database gets the new column and keeps its numbers")
ok.done()
