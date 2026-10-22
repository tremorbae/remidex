"""What RemiliaNET's own BeetleBoy and BeetleCraft screens say, word for word: the catch and hunt screen, the Cheeseman and the
craft screen. The website writes these itself in the page (the game sends only the facts), so this is its wording, copied.
The dashboard shows them under each line of the activity feed; nothing here talks to the site."""
import re

CATCH_START = "YOU NOTICE A STRAY BEETLE IN YOUR PATH..."
HUNT_START = "YOU LEAVE OUT SOME CHEESE HOPING BEETLES WILL BITE... -20 CHZ"
HUNT_EMPTY = "SORRY... YOU COULDN'T FIND ANY BEETLES THIS TIME..."
SCREEN = "BeetleCraft screen: "  # the log line the bot writes for each craft's exact words (see craft.py)


def an(word):
    return "AN" if word[:1].upper() in "AEIOU" and word else "A"


def found_phrase(name, extras=()):
    """"A GREEN BEETLE, A CHIP BAG, AND 2 GLUE STICKS": the beetle, then what else came with it, as the page joins them."""
    parts = [f"{an(name.upper())} {name.upper()}"]
    counts = {}
    for extra in extras:
        counts[extra] = counts.get(extra, 0) + 1
    for extra, n in counts.items():
        label = extra.upper() + (f" ({n})" if n > 1 else "")
        parts.append(f"{an(label)} {label}")
    return joined(parts)


def joined(parts):
    """"A AND B", or "A, B, AND C", as the screens list things."""
    return " AND ".join(parts) if len(parts) <= 2 else f"{', '.join(parts[:-1])}, AND {parts[-1]}"


def catch_says(names, xp):
    """The free hourly beetle: what the screen says as it starts and what it says when you've found something."""
    return [CATCH_START, f"YOU FOUND {found_phrase(names[0], names[1:])}! +{xp} XP"]


def hunt_says(names, xp):
    """A hunt that found something."""
    return [HUNT_START, f"SUCCESS! YOU JUST CAUGHT {found_phrase(names[0], names[1:])}! +{xp} XP"]


def hunt_empty_says():
    return [HUNT_START, HUNT_EMPTY]


def cheeseman_says(names):
    """The Cheeseman, handing out junk: he names the first thing."""
    return [f'"HERE, TAKE THIS {names[0]}!"' if names else '"NOTHING HAPPENED..."']


def item_list(names):
    """What went into a craft, as the craft screen lists it: "A NECTAR AND A LADYBUG", "2 JUNK CUBES"."""
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return joined([f"{n} {name}S" if n > 1 else f"A {name}" for name, n in counts.items()])


def break_chance_says(rate):
    return f"HAMMER BREAK CHANCE: {rate}%" + (" - CRITICAL!" if rate >= 75 else " - HIGH RISK" if rate >= 50 else "")


def craft_says(outcome, assemble, items, sacrifice, result, shattered=False, rate=None):
    """What the craft screen says about a craft: its sentence, then (for a smash where the hammer held, and the new break
    chance is known) the hammer's break chance. outcome is what the game answered: SUCCESS, UNLUCKY_ROLL, INVALID_RECIPE or
    ALREADY_OWNED. items: names of what went in. sacrifice: the name of the beetle sacrificed (a smash). result: what came out."""
    broke = " YOUR HAMMER SHATTERED!" if shattered else ""
    if outcome == "SUCCESS":
        line = f"YOU ASSEMBLED A {result} FROM {item_list(items)}!" if assemble else f"YOU SACRIFICED A {sacrifice} AND SMASHED {item_list(items)} INTO A {result}!{broke}"
    elif outcome == "INVALID_RECIPE":
        line = "NOTHING HAPPENED." if assemble else f"NOTHING HAPPENED. YOUR {sacrifice} IS GONE.{broke}"
    elif outcome == "UNLUCKY_ROLL":
        line = f"SMASH FAILED. YOUR {sacrifice} IS GONE.{broke}"
    elif outcome == "ALREADY_OWNED":
        line = "YOU ALREADY HAVE THIS ITEM."
    else:
        return []
    return [line] + ([break_chance_says(rate)] if rate is not None and not assemble and not shattered else [])


# ---- from the log: the lines the bot has always written, so older entries get their comments too
HUNT = re.compile(r"^Hunt: (.+?) \(\+(\d+) XP\)\.(?: [\d,]+ cheese left\.)?$")
CATCH = re.compile(r"^Free beetle: (.+?) \(\+(\d+) XP\)\.$")
CHEESEMAN = re.compile(r"^Cheeseman: (.+?)(?: \(\+\d+ XP\))?\.$")
CRAFTED = re.compile(r"^BeetleCraft: (Assemble|Smash) (.+?) -> (.+?)(?: \((.*?)\))?\.( The hammer shattered!)?$")
UNLUCKY = re.compile(r"^BeetleCraft: Smash (.+?): smash failed")
LOST = re.compile(r"^BeetleCraft: Smash failed: (?:a|\d+) .+? lost")
NOTHING = re.compile(r"^BeetleCraft: (Assemble|Smash) (.+?): NOTHING HAPPENED")
OWNED = re.compile(r"^BeetleCraft: (?:Assemble|Smash) .+?: you can only own one of that")
SACRIFICED = re.compile(r"sacrificing a (.+?);")


def from_log(text):
    """The screen's words for a line the bot wrote to the log, or None if the screen says nothing for it. A craft's new
    hammer break chance isn't in the log, so it's left out here (new crafts have it: see craft.py)."""
    if m := HUNT.match(text):
        return hunt_says(m[1].split(" + "), int(m[2]))
    if text.startswith("Hunt: found nothing"):
        return hunt_empty_says()
    if m := CATCH.match(text):
        return None if m[1] == "nothing" else catch_says(m[1].split(" + "), int(m[2]))
    if m := CHEESEMAN.match(text):
        return cheeseman_says([] if m[1] == "nothing" else m[1].split(" + "))
    if not text.startswith("BeetleCraft: "):
        return None
    shattered = text.endswith("The hammer shattered!")
    sacrifice = (SACRIFICED.search(text) or [None, "Green Beetle"])[1]
    if m := CRAFTED.match(text):
        assemble = m[1] == "Assemble"
        return craft_says("SUCCESS", assemble, m[2].split(" + "), sacrifice, m[3], shattered)
    if m := UNLUCKY.match(text):
        return craft_says("UNLUCKY_ROLL", False, [], sacrifice, "", shattered)
    if LOST.match(text):
        return craft_says("UNLUCKY_ROLL", False, [], "Green Beetle", "", shattered)
    if m := NOTHING.match(text):
        return craft_says("INVALID_RECIPE", m[1] == "Assemble", [], sacrifice, "", shattered)
    if OWNED.match(text):
        return craft_says("ALREADY_OWNED", True, [], "", "")
    return None
