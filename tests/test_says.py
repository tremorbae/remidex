"""What RemiliaNET's own screens say about a catch, hunt, Cheeseman handout or craft: the page's wording (copied from its code),
worked out from the log lines the bot has always written, written exactly by the bot for new crafts, and folded into the feed."""
import tempfile
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
import dashboard  # noqa: E402
import store  # noqa: E402
from remidex import says  # noqa: E402

# ---- the page's own sentences
ok(says.catch_says(["Green Beetle", "Empty Noodle Cup", "SIM Card"], 55) == ["YOU NOTICE A STRAY BEETLE IN YOUR PATH...", "YOU FOUND A GREEN BEETLE, AN EMPTY NOODLE CUP, AND A SIM CARD! +55 XP"], "a free beetle: the page's two lines")
ok(says.hunt_says(["Monarch", "Chip Bag"], 66)[1] == "SUCCESS! YOU JUST CAUGHT A MONARCH AND A CHIP BAG! +66 XP", "a hunt: two things, joined with AND")
ok(says.hunt_says(["Ant"], 5)[1] == "SUCCESS! YOU JUST CAUGHT AN ANT! +5 XP", "AN before a vowel")
ok(says.hunt_says(["Green Beetle", "Glue Stick", "Glue Stick", "Pebble"], 9)[1] == "SUCCESS! YOU JUST CAUGHT A GREEN BEETLE, A GLUE STICK (2), AND A PEBBLE! +9 XP", "the same drop twice is counted, like the page does")
ok(says.hunt_empty_says() == ["YOU LEAVE OUT SOME CHEESE HOPING BEETLES WILL BITE... -20 CHZ", "SORRY... YOU COULDN'T FIND ANY BEETLES THIS TIME..."], "a hunt that found nothing")
ok(says.cheeseman_says(["Glue Stick", "Pebble"]) == ['"HERE, TAKE THIS Glue Stick!"'], "the Cheeseman names the first thing")
ok(says.cheeseman_says([]) == ['"NOTHING HAPPENED..."'], "or says nothing happened")
ok(says.item_list(["Nectar", "Ladybug"]) == "A Nectar AND A Ladybug" and says.item_list(["Junk Cube", "Junk Cube"]) == "2 Junk CubeS", "what went into a craft, as the craft screen lists it")
ok(says.item_list(["A", "B", "C"]) == "A A, A B, AND A C", "three things")
ok(says.craft_says("SUCCESS", True, ["Glue Stick", "Empty Noodle Cup"], "Green Beetle", "Junk Cube") == ["YOU ASSEMBLED A Junk Cube FROM A Glue Stick AND A Empty Noodle Cup!"], "an assemble")
ok(says.craft_says("SUCCESS", False, ["Nectar", "Ladybug"], "Green Beetle", "Monarch", False, 25) == ["YOU SACRIFICED A Green Beetle AND SMASHED A Nectar AND A Ladybug INTO A Monarch!", "HAMMER BREAK CHANCE: 25%"], "a smash, with the hammer's new break chance")
ok(says.craft_says("SUCCESS", False, ["Nectar", "Ladybug"], "Green Beetle", "Monarch", True, 30)[0].endswith("INTO A Monarch! YOUR HAMMER SHATTERED!") and len(says.craft_says("SUCCESS", False, ["N"], "G", "M", True, 30)) == 1, "a smash that broke the hammer: says so, and no break chance after")
ok(says.craft_says("UNLUCKY_ROLL", False, ["A", "B"], "Green Beetle", "", False, 55) == ["SMASH FAILED. YOUR Green Beetle IS GONE.", "HAMMER BREAK CHANCE: 55% - HIGH RISK"], "a failed smash, at a high risk")
ok(says.craft_says("UNLUCKY_ROLL", False, [], "Green Beetle", "", False, 80)[1] == "HAMMER BREAK CHANCE: 80% - CRITICAL!", "and a critical one")
ok(says.craft_says("INVALID_RECIPE", True, [], "", "") == ["NOTHING HAPPENED."] and says.craft_says("INVALID_RECIPE", False, [], "Green Beetle", "", True) == ["NOTHING HAPPENED. YOUR Green Beetle IS GONE. YOUR HAMMER SHATTERED!"], "nothing happened")
ok(says.craft_says("ALREADY_OWNED", True, [], "", "") == ["YOU ALREADY HAVE THIS ITEM."] and says.craft_says("SOMETHING_NEW", True, [], "", "") == [], "already owned; and an answer it doesn't know says nothing")

# ---- from the lines the bot has always logged
f = says.from_log
ok(f("Hunt: Green Beetle + Chip Bag + Pokkiri Box (+66 XP). 1,486 cheese left.")[1] == "SUCCESS! YOU JUST CAUGHT A GREEN BEETLE, A CHIP BAG, AND A POKKIRI BOX! +66 XP", "a hunt line")
ok(f("Hunt: found nothing. 1,446 cheese left.") == says.hunt_empty_says(), "a hunt that found nothing")
ok(f("Free beetle: Green Beetle + Red Whistle (+55 XP).")[1] == "YOU FOUND A GREEN BEETLE AND A RED WHISTLE! +55 XP", "a free beetle line")
ok(f("Cheeseman: Glue Stick.") == ['"HERE, TAKE THIS Glue Stick!"'] and f("Cheeseman: Glue Stick (+40 XP).") == ['"HERE, TAKE THIS Glue Stick!"'], "a Cheeseman line, with or without XP")
ok(f("Daily cheese: +333, streak 6.") is None and f("Level up! You're now level 27.") is None and f("~bob (friend): poked.") is None, "lines the screens don't comment on")
ok(f("BeetleCraft: Assemble Glue Stick + Empty Noodle Cup -> Junk Cube.") == ["YOU ASSEMBLED A Junk Cube FROM A Glue Stick AND A Empty Noodle Cup!"], "an assemble line")
ok(f("BeetleCraft: Smash Nectar + Ladybug -> Monarch (sacrificing a Green Beetle; Tin Hammer at 20% break chance).") == ["YOU SACRIFICED A Green Beetle AND SMASHED A Nectar AND A Ladybug INTO A Monarch!"], "a smash line (the new break chance isn't in the log)")
ok(f("BeetleCraft: Smash Green Beetle + Junk Cube -> Daisy (sacrificing a Green Beetle; Tin Hammer at 35% break chance). The hammer shattered!")[0].endswith("INTO A Daisy! YOUR HAMMER SHATTERED!"), "a smash that broke the hammer")
ok(f("BeetleCraft: Smash Purple Beetle + Bronze Pollen: smash failed (sacrificing a Green Beetle; Tin Hammer at 25% break chance); the Green Beetle is gone, the rest came back.") == ["SMASH FAILED. YOUR Green Beetle IS GONE."], "a failed smash line")
ok(f("BeetleCraft: Smash Pond + Junk Cube: NOTHING HAPPENED, so this recipe doesn't exist; it's been removed from the recipe book.") == ["NOTHING HAPPENED. YOUR Green Beetle IS GONE."], "a recipe the game says doesn't exist")
ok(f("BeetleCraft: Assemble A + B: NOTHING HAPPENED, so this recipe doesn't exist; it's been removed from the recipe book.") == ["NOTHING HAPPENED."], "(an assemble: nothing is lost)")
ok(f("BeetleCraft: Assemble Smiley Pebble + Smiley Pebble -> Junk Cube (by you, on RemiliaNET).") == ["YOU ASSEMBLED A Junk Cube FROM 2 Smiley PebbleS!"], "a craft you made on RemiliaNET")
ok(f("BeetleCraft: Smash failed: a Green Beetle lost (by you, on RemiliaNET; Tin Hammer at 20% break chance).") == ["SMASH FAILED. YOUR Green Beetle IS GONE."] and f("BeetleCraft: Smash failed: 2 Green Beetles lost (by you, on RemiliaNET). The hammer shattered!") == ["SMASH FAILED. YOUR Green Beetle IS GONE. YOUR HAMMER SHATTERED!"], "smashes you lost on RemiliaNET")
ok(f("BeetleCraft: Assemble X: you can only own one of that; skipping it for a day.") == ["YOU ALREADY HAVE THIS ITEM."], "one you already own")
ok(f("BeetleCraft: verified a recipe: Smash A -> B.") is None and f("BeetleCraft: unlocked crafting slot 3 with the BeetleBoy Key.") is None and f("BeetleCraft screen: X") is None, "other BeetleCraft lines say nothing")

# ---- the feed's entries
ok(dashboard.kind_of("Cheeseman: Glue Stick.") == "cheeseman" and dashboard.kind_of("BeetleBoy: junkFaucet refused (x); trying again in 15 minutes.") == "problem" and dashboard.kind_of("Hunt: found nothing.") == "beetle", "the Cheeseman has his own kind; a refusal is still a problem")
D = "2026-10-18 20:00:%02d"
lines = [(D % 5, "Hunt: Green Beetle (+5 XP). 100 cheese left."), (D % 7, "Cheeseman: Glue Stick."),
         (D % 10, "BeetleCraft: Smash Nectar + Ladybug -> Monarch (sacrificing a Green Beetle; Tin Hammer at 20% break chance)."),
         (D % 10, "feed: something from the feed watcher"),
         (D % 10, says.SCREEN + "YOU SACRIFICED A Green Beetle AND SMASHED A Nectar AND A Ladybug INTO A Monarch!"), (D % 10, says.SCREEN + "HAMMER BREAK CHANCE: 30%"),
         (D % 20, "BeetleCraft: Assemble A + B -> C."), (D % 40, says.SCREEN + "from nowhere")]
entries = dashboard.log_entries(lines, set())
ok([e["text"].split(":")[0] for e in entries] == ["Hunt", "Cheeseman", "BeetleCraft", "feed", "BeetleCraft"], f"the screen's lines don't make entries of their own ({len(entries)})")
smash = entries[2]
ok(smash["say"] == ["YOU SACRIFICED A Green Beetle AND SMASHED A Nectar AND A Ladybug INTO A Monarch!", "HAMMER BREAK CHANCE: 30%"], "a craft's own lines replace the worked-out ones, and include the break chance")
ok(entries[0]["say"][0] == says.HUNT_START and entries[0]["kind"] == "beetle" and entries[1]["kind"] == "cheeseman" and entries[1]["say"] == ['"HERE, TAKE THIS Glue Stick!"'], "hunts and the Cheeseman have theirs")
ok(entries[4]["say"] == ["YOU ASSEMBLED A C FROM A A AND A B!"], "an older craft keeps the worked-out words (a screen line from long after isn't its)")
ok("say" not in entries[3], "lines with nothing to say have no comment")
dashboard.LOG_FILE = Path(tempfile.mkdtemp()) / "none.log"  # (never the real log)
older = dashboard.older_cheeseman([("2026-10-18 20:00:00", "x")], set())
ok(older == [], "his older lines come from the history database (none in a new one)")
ok(dashboard.log_entries([(D % 1, "~bob (friend): poked.")], {"bob"})[0] == {"at": D % 1, "ts": store.log_time(D % 1), "text": "~bob (friend): poked.", "kind": "poke", "who": "bob", "pic": True, "friend": None}, "pokes still say who")

# ---- the bot writes the craft screen's exact words, and the same shape of log line is what the dashboard reads
tmp = Path(tempfile.mkdtemp())
logs = []
support.patch(log=logs.append)
cards = {"nectar": {"beetle_name": "Nectar", "category": 5, "rarity": 2}, "ladybug": {"beetle_name": "Ladybug", "category": 2, "rarity": 2}, "green": {"beetle_name": "Green Beetle"},
         "monarch": {"beetle_name": "Monarch", "category": 2, "rarity": 4}, "hammer_t1": {"beetle_name": "Tin Hammer", "category": 4, "rarity": 2}}
game = {"inventory": {"nectar": 1, "ladybug": 1, "green": 14, "hammer_t1": 1}, "hammers": [{"hammer": "hammer_t1", "break_rate": 20, "base_break_rate": 0}]}
bag = pb.Bag(game, cards)
after = {"inventory": {"monarch": 1, "green": 13, "hammer_t1": 1}, "hammers": [{"hammer": "hammer_t1", "break_rate": 25, "base_break_rate": 0}]}
pb.log_screen(bag, cards, pb.SMASH, ["nectar", "ladybug"], "hammer_t1", {"message": "SUCCESS", "result": {"beetle": "monarch", "beetle_name": "Monarch"}, "user": after})
ok(logs == [says.SCREEN + "YOU SACRIFICED A Green Beetle AND SMASHED A Nectar AND A Ladybug INTO A Monarch!", says.SCREEN + "HAMMER BREAK CHANCE: 25%"], f"a smash: its sentence and the hammer's break chance from the game's answer ({logs})")
logs.clear()
pb.log_screen(bag, cards, pb.SMASH, ["nectar", "ladybug"], "hammer_t1", {"message": "UNLUCKY_ROLL", "hammer_broke": True, "user": {**after, "hammers": []}})
ok(logs == [says.SCREEN + "SMASH FAILED. YOUR Green Beetle IS GONE. YOUR HAMMER SHATTERED!"], f"a failed smash that broke the hammer: no break chance after ({logs})")
logs.clear()
pb.log_screen(bag, cards, pb.ASSEMBLE, ["nectar", "ladybug"], "", {"message": "SUCCESS", "result": {"beetle": "monarch", "beetle_name": "Monarch"}, "user": after})
ok(logs == [says.SCREEN + "YOU ASSEMBLED A Monarch FROM A Nectar AND A Ladybug!"], f"an assemble ({logs})")
logs.clear()
pb.log_screen(bag, cards, pb.ASSEMBLE, ["nectar"], "", {"error": "boom"})
ok(logs == [], "an answer the screen has no words for adds nothing")

# ---- the whole craft path: the bot crafts, and the log gets the craft's line and then the screen's
support.patch(log=lambda m: logs.append(m), OVERRIDES_FILE=tmp / "o.json", REMOVED_FILE=tmp / "r.json", STATE_FILE=tmp / "s.json", CRAFT_STATS_FILE=tmp / "c.json",
              GAME_FILE=tmp / "g.json", LEVELS_FILE=tmp / "l.json", LOG_FILE=tmp / "log.txt")
pb._REMOVED["checked"] = 0
catalog = pb.recipe_book()[1]
real = {k: {"beetle_name": v.get("name"), "category": v.get("category"), "rarity": v.get("tier")} for k, v in catalog.items()}
pebble = next(k for k, v in catalog.items() if v.get("name") == "Smiley Pebble")
start = {"inventory": {pebble: 2}, "hammers": [], "crafting_slot3_unlocked": False}
end = {"inventory": {"junk_cube_t1": 1}, "hammers": [], "crafting_slot3_unlocked": False}
sent = []


class Site:
    def api(self, method, path, body=None):
        return (200, start if path == "/api/beetle/user" else real)

    def signed_api(self, method, path, body=None):
        sent.append(body)
        return 200, {"message": "SUCCESS", "result": {"beetle": "junk_cube_t1", "beetle_name": "Junk Cube"}, "user": end}

    def wait(self, s):
        pass


logs.clear()
settings = {"junk_cubes": True, "goal": None, "goal_again": False, "goal_paused": False, "goal_set_at": None, "make": None, "locked": [],
            "hammer_order": pb.HAMMERS, "hammer_limits": {h: 25 for h in pb.HAMMERS}, "max_break_chance": 25, "hammer_timing": False}
pb.beetlecraft(Site(), {"handle": "me"}, False, settings)
crafts = [m for m in logs if m.startswith("BeetleCraft")]
ok(len(sent) == 1 and crafts[0].startswith("BeetleCraft: Assemble") and crafts[-1] == says.SCREEN + "YOU ASSEMBLED A Junk Cube FROM 2 Smiley PebbleS!", f"the bot's craft is followed by the screen's words ({crafts})")
shown = dashboard.log_entries([("2026-10-18 21:00:00", m) for m in crafts], set())
ok(len(shown) == 2 and shown[0]["say"] == ["YOU ASSEMBLED A Junk Cube FROM 2 Smiley PebbleS!"] and "say" not in shown[1] and "verified" in shown[1]["text"], "and the feed shows them with the craft, not with the verified-recipe line between")

# ---- the history database reads the same log, and the screen's lines aren't crafts or failed smashes
journal = tmp / "journal.log"
journal.write_text("2026-10-18 20:00:10 BeetleCraft: Smash Nectar + Ladybug -> Monarch (sacrificing a Green Beetle; Tin Hammer at 20% break chance).\n"
                   + f"2026-10-18 20:00:10 {says.SCREEN}YOU SACRIFICED A Green Beetle AND SMASHED A Nectar AND A Ladybug INTO A Monarch!\n"
                   + f"2026-10-18 20:00:10 {says.SCREEN}SMASH FAILED. YOUR Green Beetle IS GONE.\n2026-10-18 20:00:10 {says.SCREEN}HAMMER BREAK CHANCE: 25%\n")
store.ingest_log(journal)
ok(store.totals()["crafts"] == 1, f"the history counts the craft once ({store.totals()['crafts']})")
ok(dashboard.kind_of("BeetleCraft: Smash A + B -> C (sacrificing a Green Beetle; Tin Hammer at 10% break chance). The hammer shattered!") == "craft"
   and dashboard.kind_of("BeetleCraft: Smash A + B: NOTHING HAPPENED, so this recipe doesn't exist; it's been removed from the recipe book.") == "craft", "a shattered hammer is part of the craft")
ok(dashboard.kind_of("Error: couldn't check the sign-in (HTTP 0) (trying again in a minute)") == "problem" and dashboard.kind_of("BeetleCraft: couldn't load the game (HTTP 0/0).") == "problem", "the app's own trouble stays in the Console")
ok.done()
