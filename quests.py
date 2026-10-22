"""Quests for the dashboard, and what you can craft right now.

Quests are checked against what the bot has saved: items you've discovered or own, hammers, crafting slot 3, cheese,
level, friends, today's pokes and profile views, and the all-time counts in the history (crafts, smashes, hunts, pokes,
broken hammers, views of your profile, the cheese streak), which start from when RemiDex began counting. Once a quest is done it stays done, even if you later spend
what it took (the dashboard remembers that in its own file).
"""

from remidex.core import HAMMERS

POLLENS = ["pollen_common", "pollen_uncommon", "pollen_rare", "pollen_super_rare"]
ARTIFACTS = ["nectar", "cattail", "pinecone", "moss", "gunpowder"]
MITHRIL_SPECIALS = ["giraffe_weevil", "pillbug", "imperial_tortoise", "golden_tiger", "blue_longicorn"]
ADAMANTINE_SPECIALS = ["sabertooth_longhorn", "sunset_moth", "death_feigning", "pondhawk"]
HOLIDAY = ["skull", "widow", "christmas", "candycane_tiger_moth"]
TIN_FLOWERS = ["daisy", "sunflower", "poppy", "petunia", "snapdragon", "carnation"]
BRONZE_FLOWERS = ["gallic_rose", "marigold", "milk_thistle", "magnolia", "st_johns_wort"]
MITHRIL_FLOWERS = ["royal_poinciana", "camellia", "morning_glory", "larkspur", "fringed_iris"]
ADAMANTINE_FLOWERS = ["pincushion", "gazania", "passionflower", "hellebore"]


class Context:
    """What the quests look at."""

    def __init__(self, items, game, state, viewers_today, totals=None):
        self.items = {i["key"]: i for i in items}
        self.found = {k for k, i in self.items.items() if i["discovered"] or i["count"] > 0}
        self.game, self.state, self.viewers_today, self.totals = game or {}, state or {}, viewers_today, totals or {}

    def count(self, key):
        return (self.items.get(key) or {}).get("count", 0)

    def found_of(self, keys):
        return sum(k in self.found for k in keys)

    def where(self, category, tier=None, exclude=()):
        return [k for k, i in self.items.items() if i["category"] == category and (tier is None or i["tier"] == tier) and k not in exclude]

    def found_where(self, category, tier=None, exclude=()):
        return [k for k in self.where(category, tier, exclude) if k in self.found]


def _beetles(c):
    return c.where(2)


def _flowers(c):
    return [k for k in c.where(5) if not k.startswith("pollen")]


def _junk(c):
    return c.where(3, exclude=("junk_cube_t1", "junk_cube_t2"))


def _trophies(c):
    """Trinket trophies (trophy_<trinket>); pinned beetles count for the Pinned quest instead."""
    trinkets = set(c.where(8))
    return [k for k in c.where(9) if k[7:] in trinkets]


def _pinned(c):
    beetles = set(_beetles(c))
    return any(k.startswith("trophy_") and k[7:] in beetles for k in c.found)


def found(key):
    return lambda c: (int(key in c.found), 1)


def found_all(keys):
    return lambda c: (c.found_of(keys), len(keys))


def at_least(value, need):
    def progress(c):
        have = value(c)
        return (min(have, need) if isinstance(have, (int, float)) and not isinstance(have, bool) else 0), need
    return progress


def total(name, need):
    """One of the history's all-time counts (crafts, smashes, hunts, pokes...: see store.COUNTERS), or the cheese streak."""
    return at_least(lambda c: c.totals.get(name) or 0, need)


def caught(need):
    return at_least(lambda c: c.game.get("beetle_count") or 0, need)


def level(need):
    return at_least(lambda c: c.game.get("level") or 0, need)


def friends(need):
    return at_least(lambda c: len(c.state.get("friends") or []), need)


# One quest for each trinket trophy (besides the ones with their own above): (trinket, title, how).
TROPHY_QUESTS = [
    ("prism", "Refraction", "Win the Prism trophy: two Junk Tesseracts + Tin Pollen."),
    ("roman_dodeca", "Ancient Mystery", "Win the Roman Dodecahedron trophy: two Junk Tesseracts + Bronze Pollen."),
    ("arrowhead", "Sharp Shooter", "Win the Arrowhead trophy: two Junk Tesseracts + Mithril Pollen."),
    ("titanium_cube", "Heavy Metal", "Win the Titanium Cube trophy: two Junk Tesseracts + Adamantine Pollen."),
    ("oriental_fan", "Cool Breeze", "Win the Oriental Fan trophy: a Chinese Coin + any pollen."),
    ("jade_cabbage", "Jade Garden", "Win the Jade Cabbage trophy: a Chinese Coin + a Roman Dodecahedron."),
    ("thumb_drive", "Plug and Play", "Win the Thumb Drive trophy: a Roman Dodecahedron + a Prism."),
    ("mokia", "Mokia", "Win the Mokia trophy: a Roman Dodecahedron + a Titanium Cube."),
    ("compass", "True North", "Win the Compass trophy: a Roman Dodecahedron + an Arrowhead."),
    ("juex_card", "Wild Card", "Win the Juex Card trophy: a Prism + a Titanium Cube."),
    ("police_badge", "Badge of Honor", "Win the Police Badge trophy: an Arrowhead + a Titanium Cube."),
    ("cult_medallion", "Cult Classic", "Win the CULT Medallion trophy: a Chinese Coin + a Juex Card."),
    ("goya_miniature", "Old Master", "Win the Goya Miniature trophy: an Arrowhead + a Stradivarius."),
    ("milady_fumoku", "Milady", "Win the Milady Fumoku trophy: an Arrowhead + a CULT Medallion."),
    ("joystick", "Player One", "Win the Joystick trophy: a D20 or a Deck of Cards + a Thumb Drive."),
]


# (group, id, title, how to do it, icon: an item key or a character, progress(context) -> (have, need))
QUESTS = [
    ("合成 Crafting", "junk_cube", "Junk Crusher", "Assemble two pieces of junk into a Junk Cube.", "junk_cube_t1", found("junk_cube_t1")),
    ("合成 Crafting", "tesseract", "Fourth Dimension", "Assemble three Junk Cubes into a Junk Tesseract.", "junk_cube_t2", found("junk_cube_t2")),
    ("合成 Crafting", "slot3", "Open Sesame", "Unlock crafting slot 3 with a BeetleBoy Key (Bronze Flower + Junk Cube).", "beetleboy_key",
     lambda c: (int(bool(c.game.get("crafting_slot3_unlocked"))), 1)),
    ("合成 Crafting", "pollens", "Pollinator", "Assemble all four pollens: Tin, Bronze, Mithril and Adamantine.", "pollen_rare", found_all(POLLENS)),
    ("合成 Crafting", "artifacts", "Artifact Hunter", "Smash all five artifacts: Nectar, Cattail, Pinecone, Moss and Gunpowder.", "nectar", found_all(ARTIFACTS)),
    ("合成 Crafting", "tin_pollen", "Tin Pollinator", "Assemble two Tin Flowers into Tin Pollen.", "pollen_common", found("pollen_common")),
    ("合成 Crafting", "bronze_pollen", "Bronze Pollinator", "Assemble two Bronze Flowers into Bronze Pollen.", "pollen_uncommon", found("pollen_uncommon")),
    ("合成 Crafting", "mithril_pollen", "Mithril Pollinator", "Assemble two Mithril Flowers into Mithril Pollen.", "pollen_rare", found("pollen_rare")),
    ("合成 Crafting", "adamantine_pollen", "Adamantine Pollinator", "Assemble two Adamantine Flowers into Adamantine Pollen.", "pollen_super_rare", found("pollen_super_rare")),
    ("合成 Crafting", "nectar", "Sweet Tooth", "Smash a Ladybug with Bronze Pollen until Nectar comes out.", "nectar", found("nectar")),
    ("合成 Crafting", "cattail", "Reed All About It", "Smash a Ladybug with Bronze Pollen until a Cattail comes out.", "cattail", found("cattail")),
    ("合成 Crafting", "pinecone", "Pine Fresh", "Smash a Pond Beetle with Mithril Pollen until a Pinecone comes out.", "pinecone", found("pinecone")),
    ("合成 Crafting", "moss", "Rolling Stone", "Smash a Pond Beetle with Mithril Pollen until Moss comes out.", "moss", found("moss")),
    ("合成 Crafting", "gunpowder", "Powder Keg", "Smash a Pond Beetle with Mithril Pollen until Gunpowder comes out.", "gunpowder", found("gunpowder")),
    ("合成 Crafting", "cubes_10", "Cube Stack", "Hold 10 Junk Cubes at once.", "junk_cube_t1", at_least(lambda c: c.count("junk_cube_t1"), 10)),
    ("合成 Crafting", "tesseracts_3", "Hypercube", "Hold 3 Junk Tesseracts at once.", "junk_cube_t2", at_least(lambda c: c.count("junk_cube_t2"), 3)),
    ("合成 Crafting", "crafts_100", "Busy Hands", "Craft 100 things (a smash that fails doesn't count).", "合", total("crafts", 100)),
    ("合成 Crafting", "crafts_500", "Workshop", "Craft 500 things.", "合", total("crafts", 500)),
    ("合成 Crafting", "crafts_1000", "Factory Floor", "Craft 1,000 things.", "合", total("crafts", 1000)),
    ("合成 Crafting", "cubes_made_100", "Compactor", "Make 100 Junk Cubes.", "junk_cube_t1", total("junk_cubes", 100)),
    ("合成 Crafting", "smashes_100", "Smash Hit", "Smash 100 times, whether it works or not.", "hammer_t1", total("smashes", 100)),
    ("合成 Crafting", "smashes_500", "Wrecking Crew", "Smash 500 times.", "hammer_t3", total("smashes", 500)),

    ("锤子 Hammers", "hammer_1", "Tin Smith", "Forge a Tin Hammer from two Junk Cubes.", "hammer_t1", found("hammer_t1")),
    ("锤子 Hammers", "hammer_2", "Bronze Smith", "Upgrade to a Bronze Hammer: Tin Hammer + Junk Tesseract + Tin Pollen.", "hammer_t2", found("hammer_t2")),
    ("锤子 Hammers", "hammer_3", "Mithril Smith", "Upgrade to a Mithril Hammer: Bronze Hammer + Junk Tesseract + Bronze Pollen.", "hammer_t3", found("hammer_t3")),
    ("锤子 Hammers", "hammer_4", "Adamantine Smith", "Upgrade to an Adamantine Hammer: Mithril Hammer + Junk Tesseract + Mithril Pollen.", "hammer_t4", found("hammer_t4")),
    ("锤子 Hammers", "hammer_5", "Diamond Smith", "Upgrade to the Diamond Hammer: Adamantine Hammer + Junk Tesseract + Adamantine Pollen.", "hammer_t5", found("hammer_t5")),
    ("锤子 Hammers", "full_rack", "Full Rack", "Own all five hammers at the same time (one of each tier).", "hammer_t5",
     lambda c: (sum(c.count(h) > 0 for h in HAMMERS), 5)),
    ("锤子 Hammers", "hammer_broke", "Shattered", "Break a hammer in a smash. Every smash has a chance to, and it goes up each time.", "hammer_t1", total("hammers_broken", 1)),
    ("锤子 Hammers", "hammers_broke_25", "Hammer Graveyard", "Break 25 hammers.", "hammer_t2", total("hammers_broken", 25)),

    ("甲虫 Beetles", "beetles_100", "Beetle Hoarder", "Catch 100 beetles.", "green", at_least(lambda c: c.game.get("beetle_count") or 0, 100)),
    ("甲虫 Beetles", "beetles_250", "The Swarm", "Catch 250 beetles.", "ladybug", at_least(lambda c: c.game.get("beetle_count") or 0, 250)),
    ("甲虫 Beetles", "beetles_500", "Infestation", "Catch 500 beetles.", "purple", caught(500)),
    ("甲虫 Beetles", "beetles_1000", "Plague", "Catch 1,000 beetles.", "pond", caught(1000)),
    ("甲虫 Beetles", "beetles_2500", "Beetlemania", "Catch 2,500 beetles.", "golden", caught(2500)),
    ("甲虫 Beetles", "species_10", "Collector", "Discover 10 beetle species.", "purple", lambda c: (min(len(c.found_where(2)), 10), 10)),
    ("甲虫 Beetles", "species_20", "Entomologist", "Discover 20 beetle species.", "pillbug", lambda c: (min(len(c.found_where(2)), 20), 20)),
    ("甲虫 Beetles", "species_all", "Complete Collection", "Discover every beetle species, the holiday ones included.", "mars_rhino",
     lambda c: (len(c.found_where(2)), len(c.where(2)))),
    ("甲虫 Beetles", "ladybug", "Lucky Ladybug", "Catch a Ladybug in a hunt or a free claim.", "ladybug", found("ladybug")),
    ("甲虫 Beetles", "purple", "Purple Reign", "Catch a Purple Beetle in a hunt or a free claim.", "purple", found("purple")),
    ("甲虫 Beetles", "cucumber", "Cool as a Cucumber", "Catch a Striped Cucumber Beetle in a hunt or a free claim.", "cucumber", found("cucumber")),
    ("甲虫 Beetles", "bumblebee", "Buzz", "Catch a Bumble Bee: a rare catch from hunts and free claims.", "bumblebee", found("bumblebee")),
    ("甲虫 Beetles", "harlequin", "Jester", "Catch a Harlequin Beetle: a rare catch from hunts and free claims.", "harlequin", found("harlequin")),
    ("甲虫 Beetles", "monarch", "Monarchy", "Smash Nectar with a Ladybug for a Monarch.", "monarch", found("monarch")),
    ("甲虫 Beetles", "pond", "Pond Life", "Smash Cattail with a Ladybug for a Pond Beetle.", "pond", found("pond")),
    ("甲虫 Beetles", "giraffe_weevil", "Long Neck", "Smash a Royal Poinciana with a Mithril Beetle for a Giraffe Weevil.", "giraffe_weevil", found("giraffe_weevil")),
    ("甲虫 Beetles", "pillbug", "Roly-Poly", "Smash a Camellia with a Mithril Beetle for a Pillbug.", "pillbug", found("pillbug")),
    ("甲虫 Beetles", "imperial_tortoise", "Shell Game", "Smash a Morning Glory with a Mithril Beetle for an Imperial Tortoise Beetle.", "imperial_tortoise", found("imperial_tortoise")),
    ("甲虫 Beetles", "golden_tiger", "Eye of the Tiger", "Smash a Two-spike Larkspur with a Mithril Beetle for a Golden-Spotted Tiger Beetle.", "golden_tiger", found("golden_tiger")),
    ("甲虫 Beetles", "blue_longicorn", "Feeling Blue", "Smash a Fringed Iris with a Mithril Beetle for a Blue Longicorn.", "blue_longicorn", found("blue_longicorn")),
    ("甲虫 Beetles", "sabertooth_longhorn", "Saber Tooth", "Smash a Pincushion with an Adamantine Beetle for a Sabertooth Longhorn.", "sabertooth_longhorn", found("sabertooth_longhorn")),
    ("甲虫 Beetles", "sunset_moth", "Golden Hour", "Smash a Gazania with an Adamantine Beetle for a Sunset Moth.", "sunset_moth", found("sunset_moth")),
    ("甲虫 Beetles", "death_feigning", "Playing Dead", "Smash a Purple Passionflower with an Adamantine Beetle for a Blue Death Feigning Beetle.", "death_feigning", found("death_feigning")),
    ("甲虫 Beetles", "pondhawk", "Pond Patrol", "Smash a Green Hellebore with an Adamantine Beetle for an Eastern Pondhawk.", "pondhawk", found("pondhawk")),
    ("甲虫 Beetles", "mithril", "Mithril Club", "Get a Mithril Beetle, like a Monarch or Pond Beetle.", "monarch", lambda c: (int(bool(c.found_where(2, 4))), 1)),
    ("甲虫 Beetles", "adamantine", "Adamantine Club", "Get an Adamantine Beetle: Pinecone, Moss or Gunpowder smashed with a Pond Beetle.", "stag",
     lambda c: (int(bool(c.found_where(2, 5))), 1)),
    ("甲虫 Beetles", "diamond", "Diamond Club", "Get a Diamond Beetle.", "golden", lambda c: (int(bool(c.found_where(2, 6))), 1)),
    ("甲虫 Beetles", "mithril_specials", "Flower Children", "Smash all five Mithril species from their flowers (Giraffe Weevil, Pillbug, Imperial Tortoise, Golden Tiger, Blue Longicorn).",
     "giraffe_weevil", found_all(MITHRIL_SPECIALS)),
    ("甲虫 Beetles", "adamantine_specials", "Heavy Hitters", "Smash all four Adamantine species from their flowers (Sabertooth Longhorn, Sunset Moth, Death Feigning, Pondhawk).",
     "sabertooth_longhorn", found_all(ADAMANTINE_SPECIALS)),
    ("甲虫 Beetles", "goliath", "Gentle Giant", "Get a Goliath Beetle: smash Pinecone with a Pond Beetle.", "goliath", found("goliath")),
    ("甲虫 Beetles", "stag", "Stag Party", "Get a Stag Beetle: smash Moss with a Pond Beetle.", "stag", found("stag")),
    ("甲虫 Beetles", "bombardier", "Boom", "Get a Bombardier Beetle: smash Gunpowder with a Pond Beetle.", "bombardier", found("bombardier")),
    ("甲虫 Beetles", "golden", "Golden Child", "Find or smash the Golden Scarab.", "golden", found("golden")),
    ("甲虫 Beetles", "mars_rhino", "Red Planet", "Smash a Black Lotus, Sabertooth Longhorn and Sunset Moth into the Mars Rhino Beetle.", "mars_rhino", found("mars_rhino")),
    ("甲虫 Beetles", "holiday", "Holiday Spirit", "Catch all four holiday beetles. They only appear during the Halloween and December events.", "christmas", found_all(HOLIDAY)),
    ("甲虫 Beetles", "skull", "Bone Collector", "Catch a Skull Bug during the Halloween event.", "skull", found("skull")),
    ("甲虫 Beetles", "widow", "Black Widow", "Catch a Black Widow during the Halloween event.", "widow", found("widow")),
    ("甲虫 Beetles", "christmas", "Silent Night", "Catch a Christmas Beetle during the December event.", "christmas", found("christmas")),
    ("甲虫 Beetles", "candycane", "Sweet Stripes", "Catch a Candycane Tiger Moth during the December event.", "candycane_tiger_moth", found("candycane_tiger_moth")),

    ("花 Flowers", "flowers_10", "Green Thumb", "Discover 10 kinds of flower.", "sunflower", lambda c: (min(len([k for k in _flowers(c) if k in c.found]), 10), 10)),
    ("花 Flowers", "flowers_all", "Botanist", "Discover every flower.", "camellia", lambda c: (len([k for k in _flowers(c) if k in c.found]), len(_flowers(c)))),
    ("花 Flowers", "tin_flowers", "Wildflowers", "Discover all six Tin Flowers: Daisy, Sunflower, Poppy, Petunia, Snapdragon and Carnation.", "daisy", found_all(TIN_FLOWERS)),
    ("花 Flowers", "bronze_flowers", "Garden Party", "Discover all five Bronze Flowers: Gallic Rose, Marigold, Milk Thistle, Southern Magnolia and St. John's Wort.",
     "gallic_rose", found_all(BRONZE_FLOWERS)),
    ("花 Flowers", "mithril_flowers", "Rare Blooms", "Discover all five Mithril Flowers: Royal Poinciana, Camellia, Morning Glory, Two-spike Larkspur and Fringed Iris.",
     "royal_poinciana", found_all(MITHRIL_FLOWERS)),
    ("花 Flowers", "adamantine_flowers", "Exotic Garden", "Discover all four Adamantine Flowers: Pincushion, Gazania, Purple Passionflower and Green Hellebore.",
     "gazania", found_all(ADAMANTINE_FLOWERS)),
    ("花 Flowers", "pincushion", "Pins and Needles", "Get a Pincushion: smash an Adamantine Beetle with a Junk Cube (reroll others with a Junk Tesseract).", "pincushion", found("pincushion")),
    ("花 Flowers", "gazania", "Treasure Flower", "Get a Gazania: smash an Adamantine Beetle with a Junk Cube (reroll others with a Junk Tesseract).", "gazania", found("gazania")),
    ("花 Flowers", "passionflower", "Passion Project", "Get a Purple Passionflower: smash an Adamantine Beetle with a Junk Cube (reroll others with a Junk Tesseract).",
     "passionflower", found("passionflower")),
    ("花 Flowers", "hellebore", "Christmas Rose", "Get a Green Hellebore: smash an Adamantine Beetle with a Junk Cube (reroll others with a Junk Tesseract).", "hellebore", found("hellebore")),
    ("花 Flowers", "black_lotus", "Black Lotus", "Smash Gunpowder, Moss and Pinecone into the Black Lotus, the only Diamond Flower.", "black_lotus", found("black_lotus")),

    ("奖杯 Trophies", "trophy_1", "Shelf Starter", "Win your first trophy, like the Chinese Coin (2 Junk Cubes + Tin Pollen).", "trophy_chinese_coin",
     lambda c: (min(len([k for k in _trophies(c) if k in c.found]), 1), 1)),
    ("奖杯 Trophies", "trophy_5", "Trophy Case", "Win 5 trophies.", "trophy_prism", lambda c: (min(len([k for k in _trophies(c) if k in c.found]), 5), 5)),
    ("奖杯 Trophies", "trophy_10", "Trophy Hunter", "Win 10 trophies.", "trophy_compass", lambda c: (min(len([k for k in _trophies(c) if k in c.found]), 10), 10)),
    ("奖杯 Trophies", "trophy_all", "Hall of Fame", "Win every trinket trophy.", "trophy_stradivarius",
     lambda c: (len([k for k in _trophies(c) if k in c.found]), len(_trophies(c)))),
    *[("奖杯 Trophies", f"trophy_{name}", title, how, f"trophy_{name}", found(f"trophy_{name}")) for name, title, how in TROPHY_QUESTS],
    ("奖杯 Trophies", "stradivarius", "Virtuoso", "Win the Stradivarius trophy: Oriental Fan + Jade Cabbage.", "trophy_stradivarius", found("trophy_stradivarius")),
    ("奖杯 Trophies", "remilianet_id", "Master Craftsman", "Climb the whole trinket tree to the Reminet ID trophy.", "trophy_remilianet_id", found("trophy_remilianet_id")),
    ("奖杯 Trophies", "dice", "Lucky Roll", "Find a D20 or a Deck of Cards in a hunt.", "d20", lambda c: (int("d20" in c.found or "deck_of_cards" in c.found), 1)),
    ("奖杯 Trophies", "pinned", "Pinned", "Pin a beetle to your shelf with a Specimen Pin.", "specimen_pin", lambda c: (int(_pinned(c)), 1)),

    ("垃圾 Junk", "junk_10", "Scavenger", "Discover 10 kinds of junk.", "bottle_cap", lambda c: (min(len([k for k in _junk(c) if k in c.found]), 10), 10)),
    ("垃圾 Junk", "junk_20", "Dumpster Diver", "Discover 20 kinds of junk.", "pokkiri_box", lambda c: (min(len([k for k in _junk(c) if k in c.found]), 20), 20)),
    ("垃圾 Junk", "junk_30", "Pack Rat", "Discover 30 kinds of junk.", "coffee_can", lambda c: (min(len([k for k in _junk(c) if k in c.found]), 30), 30)),
    ("垃圾 Junk", "junk_40", "Hoarder", "Discover 40 kinds of junk.", "chip_bag", lambda c: (min(len([k for k in _junk(c) if k in c.found]), 40), 40)),
    ("垃圾 Junk", "junk_all", "Junk Drawer", "Discover every kind of junk.", "headphones_90s", lambda c: (len([k for k in _junk(c) if k in c.found]), len(_junk(c)))),

    ("奶酪 Cheese & Levels", "cheese_1k", "Cheese Wheel", "Hold 1,000 cheese at once.", "cheese", at_least(lambda c: c.count("cheese"), 1000)),
    ("奶酪 Cheese & Levels", "cheese_5k", "Big Cheese", "Hold 5,000 cheese at once.", "cheese", at_least(lambda c: c.count("cheese"), 5000)),
    ("奶酪 Cheese & Levels", "cheese_10k", "Cheese Baron", "Hold 10,000 cheese at once.", "cheese", at_least(lambda c: c.count("cheese"), 10000)),
    ("奶酪 Cheese & Levels", "cheese_25k", "Cheese Mogul", "Hold 25,000 cheese at once.", "cheese", at_least(lambda c: c.count("cheese"), 25000)),
    ("奶酪 Cheese & Levels", "streak_7", "Week Streak", "Claim your daily cheese 7 days in a row.", "cheese", total("streak", 7)),
    ("奶酪 Cheese & Levels", "streak_30", "Regular", "Claim your daily cheese 30 days in a row.", "cheese", total("streak", 30)),
    ("奶酪 Cheese & Levels", "streak_100", "Committed", "Claim your daily cheese 100 days in a row.", "cheese", total("streak", 100)),
    ("奶酪 Cheese & Levels", "streak_365", "Sharp Aged", "Claim your daily cheese 365 days in a row (RemiliaNET has an achievement for it too).", "cheese", total("streak", 365)),
    ("奶酪 Cheese & Levels", "hunts_100", "Hunter", "Go on 100 hunts.", "green", total("hunts", 100)),
    ("奶酪 Cheese & Levels", "hunts_500", "Big Game Hunter", "Go on 500 hunts.", "stag", total("hunts", 500)),
    ("奶酪 Cheese & Levels", "hunts_1000", "Apex Predator", "Go on 1,000 hunts.", "goliath", total("hunts", 1000)),
    ("奶酪 Cheese & Levels", "level_25", "Level 25", "Reach BeetleBoy level 25.", "级", at_least(lambda c: c.game.get("level") or 0, 25)),
    ("奶酪 Cheese & Levels", "level_30", "Level 30", "Reach BeetleBoy level 30.", "级", level(30)),
    ("奶酪 Cheese & Levels", "level_40", "Level 40", "Reach BeetleBoy level 40.", "级", level(40)),
    ("奶酪 Cheese & Levels", "level_50", "Level 50", "Reach BeetleBoy level 50.", "级", at_least(lambda c: c.game.get("level") or 0, 50)),
    ("奶酪 Cheese & Levels", "level_75", "Level 75", "Reach BeetleBoy level 75.", "级", level(75)),
    ("奶酪 Cheese & Levels", "level_100", "Level 100", "Reach BeetleBoy level 100.", "级", level(100)),

    ("社交 Social", "friends_50", "Friendly", "Have 50 friends on RemiliaNET.", "友", friends(50)),
    ("社交 Social", "friends_100", "Popular", "Have 100 friends.", "友", friends(100)),
    ("社交 Social", "friends_250", "Networker", "Have 250 friends.", "友", friends(250)),
    ("社交 Social", "friends_500", "Influencer", "Have 500 friends.", "友", friends(500)),
    ("社交 Social", "pokes_100", "Poke Starter", "Poke 100 times.", "戳", total("pokes", 100)),
    ("社交 Social", "pokes_1000", "Poke Machine", "Poke 1,000 times.", "戳", total("pokes", 1000)),
    ("社交 Social", "pokes_5000", "Poke Legend", "Poke 5,000 times.", "戳", total("pokes", 5000)),
    ("社交 Social", "poked_100", "Poked Back", "Get poked 100 times.", "戳", total("pokes_received", 100)),
    ("社交 Social", "poked_1000", "Fan Club", "Get poked 1,000 times.", "戳", total("pokes_received", 1000)),
    ("社交 Social", "viewed_100", "Noticed", "Have your profile viewed 100 times.", "浏", total("viewed_you", 100)),
    ("社交 Social", "viewed_1000", "Famous", "Have your profile viewed 1,000 times.", "浏", total("viewed_you", 1000)),
    ("社交 Social", "viewed_5000", "Celebrity", "Have your profile viewed 5,000 times.", "浏", total("viewed_you", 5000)),

]


# Step-by-step guides. Each step is (what to do, what it makes). "What it makes" is an item key, an "@group" from the
# recipe book, a list of either (any one counts), "slot3", or None for a step with nothing to check. The dashboard
# ticks a step once you've discovered what it makes, and shows the recipes that make it.
CUBES = ("Crunch loose junk into Junk Cubes: any two pieces make one. Keep a stack of them.", "junk_cube_t1")
KEY = [("Get a Bronze Flower: hunt for one, or smash a Ladybug or Purple Beetle with a Junk Cube.", "@flower_3"),
       ("Assemble the Bronze Flower with a Junk Cube into the BeetleBoy Key, then drop it on the padlocked slot.", "slot3")]
TESSERACT = ("Assemble three Junk Cubes into a Junk Tesseract.", "junk_cube_t2")
TIN_POLLEN = ("Assemble two Tin Flowers (Daisy, Sunflower, Poppy, Petunia, Snapdragon, Carnation) into Tin Pollen.", "pollen_common")
BRONZE_POLLEN = ("Assemble two Bronze Flowers into Bronze Pollen.", "pollen_uncommon")
HAMMER = ("Forge a Tin Hammer from two Junk Cubes, so you can smash.", "hammer_t1")
TO_MITHRIL = [("Smash a Ladybug with Bronze Pollen: you get Nectar or Cattail.", ["nectar", "cattail"]),
              ("Smash Nectar with a Ladybug for a Monarch, or Cattail with a Ladybug for a Pond Beetle.", ["monarch", "pond"])]
MITHRIL_POLLEN = [("Smash a Pond Beetle or Monarch with a Junk Cube for a Mithril Flower. Do it twice.", "@flower_4"),
                  ("Assemble two Mithril Flowers into Mithril Pollen.", "pollen_rare")]
TO_ADAMANTINE = [("Smash a Pond Beetle with Mithril Pollen: you get Pinecone, Moss or Gunpowder.", ["pinecone", "moss", "gunpowder"]),
                 ("Smash that artifact with a Pond Beetle: Pinecone makes a Goliath, Moss a Stag, Gunpowder a Bombardier.", ["goliath", "stag", "bombardier"])]
ADAMANTINE_POLLEN = [("Smash an Adamantine Beetle with a Junk Cube for an Adamantine Flower. Do it twice.", "@flower_5"),
                     ("Assemble two Adamantine Flowers into Adamantine Pollen.", "pollen_super_rare")]
BASICS = [CUBES, HAMMER, *KEY]
MITHRIL_ROUTE = [*BASICS, BRONZE_POLLEN, *TO_MITHRIL]
ADAMANTINE_ROUTE = [*MITHRIL_ROUTE, *MITHRIL_POLLEN, *TO_ADAMANTINE]
HUNTS = ("Hunt (20 cheese) and claim your free beetle every 2 hours; the bot does both on AUTO.", None)


TIN_FLOWER = ("Smash a Green Beetle with a Junk Cube for a Tin Flower. Do it twice.", "@flower_2")
BRONZE_FLOWER = ("Get two Bronze Flowers: hunt for them, or smash a Ladybug or Purple Beetle with a Junk Cube.", "@flower_3")
REROLL = ("Not the flower you want? Assemble it with a Junk Tesseract to reroll it.", None)
TRINKET_NAMES = {"roman_dodeca": "Roman Dodecahedron", "cult_medallion": "CULT Medallion", "remilianet_id": "Reminet ID", "goya_miniature": "Goya Miniature Painting",
                 "d20": "D20"}
# What each trinket is assembled from (the trophy comes first, then trinkets you can use), and what the first ones need.
TRINKET_NEEDS = {"oriental_fan": ["chinese_coin"], "jade_cabbage": ["chinese_coin", "roman_dodeca"], "thumb_drive": ["roman_dodeca", "prism"],
                 "mokia": ["roman_dodeca", "titanium_cube"], "compass": ["roman_dodeca", "arrowhead"], "juex_card": ["prism", "titanium_cube"],
                 "police_badge": ["arrowhead", "titanium_cube"], "cult_medallion": ["chinese_coin", "juex_card"], "stradivarius": ["oriental_fan", "jade_cabbage"],
                 "goya_miniature": ["arrowhead", "stradivarius"], "milady_fumoku": ["arrowhead", "cult_medallion"], "remilianet_id": ["juex_card", "milady_fumoku"],
                 "joystick": ["thumb_drive"]}
TRINKET_BASICS = {"chinese_coin": [CUBES, HAMMER, TIN_FLOWER, TIN_POLLEN], "prism": [CUBES, HAMMER, TESSERACT, TIN_FLOWER, TIN_POLLEN],
                  "roman_dodeca": [CUBES, TESSERACT, BRONZE_FLOWER, BRONZE_POLLEN], "arrowhead": [*MITHRIL_ROUTE, TESSERACT, *MITHRIL_POLLEN],
                  "titanium_cube": [*ADAMANTINE_ROUTE, TESSERACT, *ADAMANTINE_POLLEN],
                  "joystick": [("Find a D20 or a Deck of Cards in a hunt.", ["d20", "deck_of_cards"])]}


def _trinket_steps(*names):
    return [(f"Assemble the {TRINKET_NAMES.get(n, n.replace('_', ' ').title())} (its recipe is below).", ["trophy_" + n, n]) for n in names]


def _trophy_guide(name):
    """The steps to a trinket trophy: what it's made from, in the order to make them, with what those need first."""
    order = []

    def visit(n):
        for m in TRINKET_NEEDS.get(n, []):
            visit(m)
        if n not in order:
            order.append(n)
    visit(name)
    basics = []
    for n in order:
        basics += [step for step in TRINKET_BASICS.get(n, []) if step not in basics]
    return [*basics, *([("Each trophy recipe gives the trophy the first time, then a trinket you can use: make the ones it's made from twice.", None)]
                       if len(order) > 1 else []), *_trinket_steps(*order)]


# Each Mithril and Adamantine species, from its flower.
SPECIES_FLOWERS = {"giraffe_weevil": ("royal_poinciana", "Royal Poinciana", "Mithril"), "pillbug": ("camellia", "Camellia", "Mithril"),
                   "imperial_tortoise": ("morning_glory", "Morning Glory", "Mithril"), "golden_tiger": ("larkspur", "Two-spike Larkspur", "Mithril"),
                   "blue_longicorn": ("fringed_iris", "Fringed Iris", "Mithril"), "sabertooth_longhorn": ("pincushion", "Pincushion", "Adamantine"),
                   "sunset_moth": ("gazania", "Gazania", "Adamantine"), "death_feigning": ("passionflower", "Purple Passionflower", "Adamantine"),
                   "pondhawk": ("hellebore", "Green Hellebore", "Adamantine")}


def _species_guide(key):
    flower, name, tier = SPECIES_FLOWERS[key]
    return [*(MITHRIL_ROUTE if tier == "Mithril" else ADAMANTINE_ROUTE),
            (f"Get a {name}: smash {'a' if tier == 'Mithril' else 'an'} {tier} Beetle with a Junk Cube for a {tier} Flower, and reroll others with a Junk Tesseract.", flower),
            (f"Smash the {name} with {'a' if tier == 'Mithril' else 'an'} {tier} Beetle.", key)]


def _flower_guide(key):
    return [*ADAMANTINE_ROUTE, ADAMANTINE_POLLEN[0], ("Not the one you want? Assemble it with a Junk Tesseract to reroll it.", key)]


CRAFTING = [("Pick an Auto make or a quest, and the bot crafts toward it: every craft counts.", None)]
STREAK = [("Claim your daily cheese every day before midnight UTC: the bot does it on AUTO.", None)]
LEVELS = [("Every catch, hunt and craft gives XP. Keep claims and hunts on AUTO.", None)]


TRINKET_ORDER = ["chinese_coin", "prism", "roman_dodeca", "arrowhead", "titanium_cube", "oriental_fan", "jade_cabbage", "thumb_drive",
                 "mokia", "compass", "juex_card", "police_badge", "cult_medallion", "stradivarius", "goya_miniature", "milady_fumoku",
                 "remilianet_id", "joystick"]
GUIDES = {
    "junk_cube": [HUNTS, CUBES],
    "tesseract": [CUBES, *KEY, TESSERACT],
    "slot3": [CUBES, HAMMER, *KEY],
    "pollens": [*BASICS, TIN_POLLEN, BRONZE_POLLEN, *TO_MITHRIL, *MITHRIL_POLLEN, *TO_ADAMANTINE, *ADAMANTINE_POLLEN],
    "artifacts": [*MITHRIL_ROUTE, MITHRIL_POLLEN[0], MITHRIL_POLLEN[1],
                  ("Smash Pond Beetles with Mithril Pollen until you've had all three: Pinecone, Moss and Gunpowder.", ["pinecone", "moss", "gunpowder"])],
    "hammer_1": [HUNTS, CUBES, HAMMER],
    "hammer_2": [*BASICS, TESSERACT, TIN_POLLEN, ("Assemble Tin Hammer + Junk Tesseract + Tin Pollen.", "hammer_t2")],
    "hammer_3": [*BASICS, TESSERACT, TIN_POLLEN, ("Assemble Tin Hammer + Junk Tesseract + Tin Pollen.", "hammer_t2"), BRONZE_POLLEN,
                 ("Assemble Bronze Hammer + another Junk Tesseract + Bronze Pollen.", "hammer_t3")],
    "hammer_4": [*MITHRIL_ROUTE, TESSERACT, TIN_POLLEN, ("Upgrade to the Bronze Hammer.", "hammer_t2"), ("Upgrade to the Mithril Hammer.", "hammer_t3"),
                 *MITHRIL_POLLEN, ("Assemble Mithril Hammer + Junk Tesseract + Mithril Pollen.", "hammer_t4")],
    "hammer_5": [*ADAMANTINE_ROUTE, TESSERACT, TIN_POLLEN, ("Upgrade to the Bronze Hammer.", "hammer_t2"), ("Upgrade to the Mithril Hammer.", "hammer_t3"),
                 ("Upgrade to the Adamantine Hammer.", "hammer_t4"), *ADAMANTINE_POLLEN,
                 ("Assemble Adamantine Hammer + Junk Tesseract + Adamantine Pollen into the Diamond Hammer.", "hammer_t5")],
    "full_rack": [("Work your way up to the Diamond Hammer (see Diamond Smith). Each upgrade uses up the hammer below it.", "hammer_t5"),
                  ("Then forge the lower ones again: a new Tin Hammer from two Junk Cubes, and upgrade copies as far as you need.", "hammer_t1"),
                  ("You can hold one of each tier at once, so keep all five.", None)],
    "beetles_100": [HUNTS], "beetles_250": [HUNTS],
    "species_10": [HUNTS, *MITHRIL_ROUTE, ("Every new species you craft counts too: see Flower Children.", None)],
    "species_20": [HUNTS, *ADAMANTINE_ROUTE, ("Craft the flower species (Flower Children and Heavy Hitters) to fill out the list.", None)],
    "mithril": MITHRIL_ROUTE,
    "adamantine": ADAMANTINE_ROUTE,
    "goliath": [*ADAMANTINE_ROUTE[:-1], ("Smash Pinecone with a Pond Beetle.", "goliath")],
    "stag": [*ADAMANTINE_ROUTE[:-1], ("Smash Moss with a Pond Beetle.", "stag")],
    "bombardier": [*ADAMANTINE_ROUTE[:-1], ("Smash Gunpowder with a Pond Beetle (a Monarch also works).", "bombardier")],
    "diamond": [*ADAMANTINE_ROUTE, ("Smash two Adamantine Beetles together for a Diamond Beetle (low odds), or follow Red Planet for the Mars Rhino.", "@beetle_6")],
    "mithril_specials": [*MITHRIL_ROUTE, MITHRIL_POLLEN[0],
                         ("Got the wrong Mithril Flower? Assemble a Junk Tesseract with it to reroll it.", None),
                         ("Royal Poinciana + Mithril Beetle → Giraffe Weevil.", "giraffe_weevil"), ("Camellia + Mithril Beetle → Pillbug.", "pillbug"),
                         ("Morning Glory + Mithril Beetle → Imperial Tortoise.", "imperial_tortoise"), ("Two-spike Larkspur + Mithril Beetle → Golden Tiger.", "golden_tiger"),
                         ("Fringed Iris + Mithril Beetle → Blue Longicorn.", "blue_longicorn")],
    "adamantine_specials": [*ADAMANTINE_ROUTE, ADAMANTINE_POLLEN[0], ("Got the wrong Adamantine Flower? Assemble a Junk Tesseract with it to reroll it.", None),
                            ("Pincushion + Adamantine Beetle → Sabertooth Longhorn.", "sabertooth_longhorn"), ("Gazania + Adamantine Beetle → Sunset Moth.", "sunset_moth"),
                            ("Purple Passionflower + Adamantine Beetle → Death Feigning.", "death_feigning"), ("Green Hellebore + Adamantine Beetle → Pondhawk.", "pondhawk")],
    "golden": [HUNTS, ("It's an ultra-rare drop from claims and hunts, so keep both on AUTO.", None), *ADAMANTINE_ROUTE,
               ("Or smash two Adamantine Beetles together: a Diamond Beetle comes out, with low odds.", "golden")],
    "mars_rhino": [*ADAMANTINE_ROUTE, ADAMANTINE_POLLEN[0],
                   ("Get a Pincushion and a Gazania (reroll other Adamantine Flowers with a Junk Tesseract).", ["pincushion", "gazania"]),
                   ("Smash Pincushion + an Adamantine Beetle for a Sabertooth Longhorn. Use a Purple sacrifice.", "sabertooth_longhorn"),
                   ("Smash Gazania + an Adamantine Beetle for a Sunset Moth.", "sunset_moth"),
                   ("Collect one each of Gunpowder, Moss and Pinecone, and smash them into a Black Lotus.", "black_lotus"),
                   ("Smash Black Lotus + Sabertooth Longhorn + Sunset Moth into the Mars Rhino. Best hammer, Purple sacrifice, first smash after the daily reset.", "mars_rhino")],
    "holiday": [("Skull Bug and Black Widow: catch them during the Halloween event.", ["skull", "widow"]),
                ("Christmas Beetle and Candycane Tiger Moth: catch them during the December event.", ["christmas", "candycane_tiger_moth"]),
                ("They can't be crafted, so keep the free claim and hunts on AUTO while the events run.", None)],
    "flowers_10": [HUNTS, CUBES, HAMMER, ("Smash beetles with Junk Cubes: each gives a random flower of the beetle's tier.", "@flower_3")],
    "flowers_all": [HUNTS, *MITHRIL_ROUTE, MITHRIL_POLLEN[0], *TO_ADAMANTINE, ADAMANTINE_POLLEN[0],
                    ("Reroll flowers with a Junk Tesseract to find the ones you're missing.", None),
                    ("The Black Lotus can only be smashed: see the Black Lotus quest.", "black_lotus")],
    "black_lotus": [*ADAMANTINE_ROUTE[:-1], ("Collect one each of Gunpowder, Moss and Pinecone.", ["gunpowder", "moss", "pinecone"]),
                    ("Smash Gunpowder + Moss + Pinecone into the Black Lotus.", "black_lotus")],
    "trophy_1": [CUBES, *KEY, TIN_POLLEN, ("Assemble two Junk Cubes + Tin Pollen. The first time gives the Chinese Coin trophy.", "trophy_chinese_coin")],
    "trophy_5": [CUBES, *KEY, TIN_POLLEN, BRONZE_POLLEN, TESSERACT, *_trinket_steps("chinese_coin", "prism", "roman_dodeca", "oriental_fan", "thumb_drive")],
    "trophy_all": [("Work through the whole trinket tree: open any trinket in your inventory to see its recipe.", None),
                   *_trinket_steps("chinese_coin", "prism", "roman_dodeca", "arrowhead", "titanium_cube", "oriental_fan", "jade_cabbage",
                                   "thumb_drive", "mokia", "compass", "juex_card", "police_badge", "cult_medallion", "stradivarius",
                                   "goya_miniature", "milady_fumoku", "remilianet_id", "joystick"),
                   ("The D20 and Deck of Cards come from hunts; the Engraved Lighter's recipe is unknown.", ["trophy_d20", "trophy_deck_of_cards"])],
    "stradivarius": [CUBES, *KEY, TIN_POLLEN, BRONZE_POLLEN, TESSERACT,
                     *_trinket_steps("chinese_coin"), ("Make a second Chinese Coin: that one's a trinket you can use.", "chinese_coin"),
                     *_trinket_steps("oriental_fan", "roman_dodeca", "jade_cabbage", "stradivarius")],
    "remilianet_id": [CUBES, HAMMER, *KEY, TIN_POLLEN, TESSERACT, *MITHRIL_ROUTE[len(BASICS):], *MITHRIL_POLLEN, *ADAMANTINE_ROUTE[len(MITHRIL_ROUTE) + 2:],
                      *ADAMANTINE_POLLEN, ("Each trophy recipe gives the trophy first, then trinkets you can use: craft each one twice.", None),
                      *_trinket_steps("chinese_coin", "prism", "titanium_cube", "juex_card", "cult_medallion", "arrowhead", "milady_fumoku", "remilianet_id")],
    "dice": [HUNTS, ("The D20 and Deck of Cards are rare hunt drops. Either one plus a Thumb Drive makes the Joystick.", ["d20", "deck_of_cards"])],
    "pinned": [("Get a Specimen Pin: everyone got one free in v0.7, and more drop rarely from hunts.", "specimen_pin"), CUBES, HAMMER,
               ("Smash the Specimen Pin with the beetle you want to show off.", None)],
    "junk_20": [HUNTS, ("The Cheeseman gives one free piece of junk a day (the bot claims it on AUTO).", None)],
    "junk_all": [HUNTS, ("The Cheeseman gives one free piece of junk a day.", None), ("Some junk is rare: keep hunting.", None)],
    "cheese_5k": [("Claim your daily cheese every day: the streak makes each claim bigger.", None),
                  ("Hunts cost 20 cheese each. Switch hunting to MANUAL on the dashboard to save up.", None)],
    "cheese_10k": [("Claim your daily cheese every day: the streak makes each claim bigger.", None),
                   ("Hunts cost 20 cheese each. Switch hunting to MANUAL on the dashboard to save up.", None)],
    "level_25": LEVELS, "level_50": LEVELS,
    # the ones added later
    "tin_pollen": [HUNTS, CUBES, HAMMER, TIN_FLOWER, TIN_POLLEN], "bronze_pollen": [HUNTS, CUBES, HAMMER, BRONZE_FLOWER, BRONZE_POLLEN],
    "mithril_pollen": [*MITHRIL_ROUTE, *MITHRIL_POLLEN], "adamantine_pollen": [*ADAMANTINE_ROUTE, *ADAMANTINE_POLLEN],
    **{k: [HUNTS, CUBES, HAMMER, BRONZE_FLOWER, BRONZE_POLLEN, (f"Smash a Ladybug with Bronze Pollen until {'Nectar' if k == 'nectar' else 'a Cattail'} comes out.", k)]
       for k in ("nectar", "cattail")},
    **{k: [*MITHRIL_ROUTE, *MITHRIL_POLLEN, (f"Smash a Pond Beetle with Mithril Pollen until {k.title()} comes out.", k)] for k in ("pinecone", "moss", "gunpowder")},
    "cubes_10": [HUNTS, CUBES], "tesseracts_3": [CUBES, TESSERACT],
    "crafts_100": CRAFTING, "crafts_500": CRAFTING, "crafts_1000": CRAFTING, "cubes_made_100": [HUNTS, CUBES], "smashes_100": CRAFTING, "smashes_500": CRAFTING,
    "hammer_broke": [("Every smash raises the hammer's break chance. The limits in Set up hammers decide how far the bot lets it go.", None)],
    "hammers_broke_25": [("Every smash raises the hammer's break chance. The limits in Set up hammers decide how far the bot lets it go.", None)],
    "beetles_500": [HUNTS], "beetles_1000": [HUNTS], "beetles_2500": [HUNTS],
    "species_all": [HUNTS, *ADAMANTINE_ROUTE, ("Craft the flower species (see each one's quest).", None),
                    ("The holiday beetles only come during the Halloween and December events.", None)],
    **{k: [HUNTS, ("It can't be crafted: it only comes from hunts and free claims.", None)] for k in ("ladybug", "purple", "cucumber", "bumblebee", "harlequin")},
    "monarch": [HUNTS, CUBES, HAMMER, BRONZE_FLOWER, BRONZE_POLLEN, ("Smash a Ladybug with Bronze Pollen until Nectar comes out.", "nectar"),
                ("Smash the Nectar with a Ladybug.", "monarch")],
    "pond": [HUNTS, CUBES, HAMMER, BRONZE_FLOWER, BRONZE_POLLEN, ("Smash a Ladybug with Bronze Pollen until a Cattail comes out.", "cattail"),
             ("Smash the Cattail with a Ladybug.", "pond")],
    **{k: _species_guide(k) for k in SPECIES_FLOWERS},
    **{k: [HUNTS, (f"Catch it during the {'Halloween' if k in ('skull', 'widow') else 'December'} event: it can't be crafted.", None)]
       for k in ("skull", "widow", "christmas", "candycane")},
    "tin_flowers": [HUNTS, CUBES, HAMMER, TIN_FLOWER, REROLL], "bronze_flowers": [HUNTS, CUBES, HAMMER, BRONZE_FLOWER, REROLL],
    "mithril_flowers": [*MITHRIL_ROUTE, MITHRIL_POLLEN[0], REROLL], "adamantine_flowers": [*ADAMANTINE_ROUTE, ADAMANTINE_POLLEN[0], REROLL],
    **{k: _flower_guide(k) for k in ADAMANTINE_FLOWERS},
    "trophy_10": [("Work up the trinket tree: open any trinket in your inventory to see its recipe.", None), *_trinket_steps(*TRINKET_ORDER[:10])],
    **{f"trophy_{name}": _trophy_guide(name) for name, _, _ in TROPHY_QUESTS},
    "junk_10": [HUNTS, ("The Cheeseman gives one free piece of junk a day (the bot claims it on AUTO).", None)],
    "junk_30": [HUNTS, ("The Cheeseman gives one free piece of junk a day.", None)], "junk_40": [HUNTS, ("Some junk is rare: keep hunting.", None)],
    "cheese_1k": [("Claim your daily cheese every day: the streak makes each claim bigger.", None)],
    "cheese_25k": [("Claim your daily cheese every day: the streak makes each claim bigger.", None),
                   ("Hunts cost 20 cheese each. Switch hunting to MANUAL on the dashboard to save up.", None)],
    "streak_7": STREAK, "streak_30": STREAK, "streak_100": STREAK, "streak_365": STREAK,
    "hunts_100": [HUNTS], "hunts_500": [HUNTS], "hunts_1000": [HUNTS],
    "level_30": LEVELS, "level_40": LEVELS, "level_75": LEVELS, "level_100": LEVELS,
    **{k: [("Turn on Auto Add Friend: the bot sends friend requests to people it pokes, and accepts the ones you get.", None)]
       for k in ("friends_50", "friends_100", "friends_250", "friends_500")},
    **{k: [("Turn on Auto Poke.", None)] for k in ("pokes_100", "pokes_1000", "pokes_5000")},
    **{k: [("People look back: poking them and viewing their profiles brings them to yours (Auto Poke, and the switch on Viewed You).", None)]
       for k in ("poked_100", "poked_1000", "viewed_100", "viewed_1000", "viewed_5000")},
}


# What the bot crafts toward when you pick a quest on the dashboard ("work on this"): the items it needs, in order.
# Each entry is an item key, a list (any one counts), "slot3", "pinned" (any pinned beetle) or "own:<key>" (must own it
# now, not just have found it once). Quests missing here come from hunts, claims or levels, so there's nothing to craft.
SPECIES_ORDER = MITHRIL_SPECIALS + ["goliath", "stag", "bombardier"] + ADAMANTINE_SPECIALS + ["mars_rhino"]
GOALS = {
    "junk_cube": ["junk_cube_t1"], "tesseract": ["junk_cube_t2"], "slot3": ["slot3"], "pollens": POLLENS, "artifacts": ARTIFACTS,
    **{f"hammer_{i}": [f"hammer_t{i}"] for i in range(1, 6)}, "full_rack": [f"own:{h}" for h in HAMMERS],
    "species_10": SPECIES_ORDER, "species_20": SPECIES_ORDER,
    "mithril": [["monarch", "pond"]], "adamantine": [["stag", "goliath", "bombardier"]], "diamond": ["mars_rhino"],
    "goliath": ["goliath"], "stag": ["stag"], "bombardier": ["bombardier"],
    "mithril_specials": MITHRIL_SPECIALS, "adamantine_specials": ADAMANTINE_SPECIALS, "mars_rhino": ["mars_rhino"], "black_lotus": ["black_lotus"],
    "trophy_1": ["trophy_chinese_coin"], "trophy_5": ["trophy_" + t for t in TRINKET_ORDER[:6]], "trophy_all": ["trophy_" + t for t in TRINKET_ORDER],
    "stradivarius": ["trophy_stradivarius"], "remilianet_id": ["trophy_remilianet_id"], "pinned": ["pinned"],
    "tin_pollen": ["pollen_common"], "bronze_pollen": ["pollen_uncommon"], "mithril_pollen": ["pollen_rare"], "adamantine_pollen": ["pollen_super_rare"],
    **{k: [k] for k in ("nectar", "cattail", "pinecone", "moss", "gunpowder", "monarch", "pond", *SPECIES_FLOWERS, *ADAMANTINE_FLOWERS)},
    "trophy_10": ["trophy_" + t for t in TRINKET_ORDER[:10]],
    **{f"trophy_{name}": [f"trophy_{name}"] for name, _, _ in TROPHY_QUESTS},
}


def goal_needs(qid, found, owned, slot3, beetles, again=False):
    """What a quest still needs, for the bot to craft toward. found and owned are item keys; beetles are beetle keys.
    again: the quest was done before and is being done again, so it counts only what you hold now."""
    if again:
        found = owned
    left = []
    for need in GOALS.get(qid, []):
        if need == "slot3":
            done = slot3
        elif need == "pinned":
            done = any(k.startswith("trophy_") and k[7:] in beetles for k in found)
        elif isinstance(need, str) and need.startswith("own:"):
            done = need[4:] in owned
        elif isinstance(need, list):
            done = any(k in found for k in need)
        else:
            done = need in found
        if not done:
            left.append(need)
    return left


def _one_of_a_kind(need):
    """Needs you can only have one of: hammers (one per tier), trophies, a pinned beetle, and unlocking slot 3."""
    keys = need if isinstance(need, list) else [need.removeprefix("own:")]
    return all(k in ("slot3", "pinned") or k.startswith(("hammer_t", "trophy_")) for k in keys)


def completed(qid, found, owned, slot3, beetles):
    """For a quest about one-of-a-kind items: True while you still hold all of them (so it can't be done again),
    False if one is gone (it can). None for quests about things you can have many of."""
    needs = GOALS.get(qid)
    if not needs or not all(_one_of_a_kind(n) for n in needs):
        return None
    held = owned | {k for k in found if k.startswith("trophy_")}  # trophies stay on your shelf once won
    return not goal_needs(qid, held, held, slot3, beetles)


def _step_done(target, c):
    """A step is ticked while you hold what it makes (use it up and it unticks). Unlocking slot 3 isn't an item, so
    that one stays ticked once it's open. None for steps with nothing to check."""
    if target is None:
        return None
    if target == "slot3":
        return bool(c.game.get("crafting_slot3_unlocked"))
    targets = target if isinstance(target, list) else [target]
    return any(_held(t, c) for t in targets)


def _held(t, c):
    if not t.startswith("@"):
        return c.count(t) > 0
    kind, _, tier = t[1:].partition("_")
    category = {"flower": 5, "beetle": 2}.get(kind)
    return bool(category and any(c.count(k) > 0 for k in c.where(category, int(tier)) if not k.startswith("pollen")))


def check(items, game, state, viewers_today, done, totals=None):
    """Every quest with its progress. done maps quest id -> when it was first completed; newly completed ones are
    added to it. totals: the history's all-time counts, and "streak". Returns (quests, whether done changed)."""
    import time
    c = Context(items, game, state, viewers_today, totals)
    out, changed = [], False
    for group, qid, title, how, icon, progress in QUESTS:
        have, need = progress(c)
        if have >= need and qid not in done:
            done[qid] = time.time()
            changed = True
        steps = [{"text": text, "makes": target if isinstance(target, list) or target is None else [target],
                  "done": _step_done(target, c)} for text, target in GUIDES.get(qid, [])]
        out.append({"group": group, "id": qid, "title": title, "how": how, "icon": icon,
                    "have": need if qid in done else have, "need": need, "done_at": done.get(qid), "steps": steps})
    return out, changed


def resolve_group(gid, book, items):
    """The item keys a recipe group (like "@flower_3") stands for."""
    g = book["groups"][gid]
    if "keys" in g:
        return list(g["keys"])
    m = g["match"]
    return [i["key"] for i in items if i["category"] == m["category"] and (m.get("tier") is None or i["tier"] == m["tier"])
            and i["key"] not in m.get("exclude", []) and not (m.get("exclude_prefix") and i["key"].startswith(m["exclude_prefix"])) and not i.get("season")]


def craftable(book, items, game):
    """Recipes you could craft right now with what you own, and the items they'd make.
    Smash also needs a hammer and a Green or Purple Beetle to sacrifice; three-item recipes need slot 3.
    Returns (rows, keys to mark), where rows are {"recipe": index, "out": [...], "uses": [...]}."""
    have = {i["key"]: i["count"] for i in items}
    found = {i["key"] for i in items if i["discovered"] or i["count"] > 0}
    slot3 = bool((game or {}).get("crafting_slot3_unlocked"))
    rows, marks = [], set()
    for index, r in enumerate(book["recipes"]):
        if len(r["in"]) == 3 and not slot3:
            continue
        left, used = dict(have), []
        ok = True
        for x in sorted(r["in"], key=lambda x: x.startswith("@")):  # exact items first, then "any ..." slots
            options = [x] if not x.startswith("@") else sorted(resolve_group(x[1:], book, items), key=lambda k: -left.get(k, 0))
            pick = next((k for k in options if left.get(k, 0) > 0), None)
            if not pick:
                ok = False
                break
            left[pick] -= 1
            used.append(pick)
        if not ok:
            continue
        if r["mode"] == "smash":
            if not any(left.get(h, 0) > 0 for h in HAMMERS) or not (left.get("green", 0) > 0 or left.get("purple", 0) > 0):
                continue
        out = list(r["out"])
        if r.get("first_trophy"):
            out = [out[0]] if out[0] not in found else [out[1]]
        if len(out) == 1 and out[0] in HAMMERS and have.get(out[0], 0) > 0:
            continue  # one hammer of each tier
        if out == ["beetleboy_key"] and slot3:
            continue
        rows.append({"recipe": index, "out": out, "uses": used})
        if not r.get("gamble"):
            marks.update(k for k in out if not k.startswith("@"))
    return rows, sorted(marks)
