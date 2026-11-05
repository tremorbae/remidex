"""The recipe book, the odds learned from the log, the Planner for Auto makes, and spotting crafts made on RemiliaNET."""

import re
import time
from collections import Counter, defaultdict
from itertools import permutations
from pathlib import Path

import quests
from remidex.core import (
    APP_DIR,
    ASSEMBLE,
    JUNK_CUBE_CRAFT,
    KEY,
    LOG_FILE,
    OVERRIDES_FILE,
    PURPLE,
    SACRIFICE,
    SMASH,
    TIN_HAMMER_CRAFT,
    editing,
    fits_match,
    log,
    read_json,
    write_json,
)
from remidex.settings import alert, valid_make


CRAFT_STATS_FILE = APP_DIR / "craft_stats.json"  # how each recipe has gone for you, for the planner to learn from
REMOVED_FILE = APP_DIR / "removed_recipes.json"  # recipes the game says don't exist, left out of the recipe book for good
ADAMANTINE = 5
CATEGORY_BEETLE, CATEGORY_HAMMER = 2, 4
TASK_LOG_KEEP = 600  # crafts kept for the dashboard's craft console and plan, for each Auto make or quest being worked on
TASK_LOGS_KEPT = 8  # how many Auto makes and quests keep a record at once (the least recently worked on go first)
PLAN_DEPTH = 12  # how many steps back the quest planner looks for something it can craft now
LOW_ODDS_TRIES = 5  # the planner counts a low-odds recipe as taking about this many tries
LEARN_WEIGHT = 3  # ... until it has seen for itself: the recipe book counts like this many of its own tries
PROVE_TRIES = 3  # a smash tried this many times that failed more often than it worked is proven low odds (Purple Beetles go to it)
LONG_SHOT = 1 / 3  # a smash this likely to work or less (it fails at least twice as often) is a long shot: done after the sure
#   steps, so the Green Beetles it burns don't hold up the rest of the plan; with hammer timing on, only with a fresh hammer
RISKY_COST = 6  # ... and an Adamantine-and-up or low-odds smash as this many crafts more: they're hard on hammers
HUNTED_COST = 4  # ... and each thing only hunts and claims bring as costing about this many crafts
STUCK_COST = 1000  # for a plan made for what a waiting step needs, when the plan can't move: anything else beats waiting
# What hunts and claims often bring, and about how many crafts' worth of waiting each is (recipe-book groups as "@...",
# an exact item costs double its group's). The planner waits for these when crafting one would cost more.
DROPS = {"@junk": 2, SACRIFICE: 1, "@beetle_2": 1, "@beetle_3": 4, "@flower_2": 6, "@flower_3": 8}
# The recipe book and item list the dashboard uses too (copied next to this script by install_browser.sh).
RECIPES_FILE = Path(__file__).resolve().parent.parent / "recipes.json"
CATALOG_FILE = Path(__file__).resolve().parent.parent / "catalog.json"


class Bag:
    """Your BeetleBoy inventory, read through the game's own cards: what each item is and which tier it's in."""

    def __init__(self, game, cards, smash_off=()):
        # (anything not shaped like the game's own answers, say from a file cut short, is left out rather than trusted)
        self.cards = {k: c for k, c in cards.items() if isinstance(c, dict)} if isinstance(cards, dict) else {}
        self.game, self.smash_off = game, frozenset(smash_off)
        inventory = game.get("inventory")
        self.items = {k: v for k, v in inventory.items() if type(v) is int and v > 0} if isinstance(inventory, dict) else {}

    def count(self, key):
        return self.items.get(key, 0)

    def without(self, locked, smash_off=()):
        """This bag with the items switched off on the dashboard taken out: what a craft may use; smash_off: hammers switched
        off for smashing, which stay in it (to be upgraded or go into a recipe) but aren't smashed with (see smash_hammers).
        (What you really have is still this bag, for checking whether something's done.)"""
        if not locked and not smash_off:
            return self
        locked = set(locked or ())
        game = {**self.game, "inventory": {k: v for k, v in (self.game.get("inventory") or {}).items() if k not in locked},
                "hammers": [h for h in self.game.get("hammers") or [] if not (isinstance(h, dict) and h.get("hammer") in locked)]}
        return Bag(game, self.cards, smash_off)

    def card(self, key):
        return self.cards.get(key) or {}

    def tier(self, key):
        return self.card(key).get("rarity")

    def name(self, key):
        return self.card(key).get("beetle_name") or key.replace("_", " ").title()

    def hammer_stats(self):
        """What the game says about each hammer: {key: {"break_rate", "base_break_rate", "craft_bonus", ...}}."""
        return {h["hammer"]: h for h in self.game.get("hammers") or [] if isinstance(h, dict) and h.get("hammer")}

    def hammers(self):
        """Hammers you own: {key: (tier, break chance in percent)}."""
        stats = self.hammer_stats()
        keys = {k for k in self.items if self.card(k).get("category") == CATEGORY_HAMMER} | set(stats)
        owned = {}
        for key in keys:
            tier = self.tier(key) or (int(key[-1]) + 1 if key[:-1] == "hammer_t" and key[-1].isdigit() else None)
            if tier:
                owned[key] = (tier, (stats.get(key) or {}).get("break_rate") or 0)
        return owned

    def smash_hammers(self):
        """The hammers a smash may use: the ones you own that aren't switched off for smashing ({key: (tier, break chance)})."""
        return {k: v for k, v in self.hammers().items() if k not in self.smash_off}

    def fresh(self, key):
        """Whether a hammer is at its lowest break chance: no smashes with it since the daily reset."""
        stats = self.hammer_stats().get(key) or {}
        return (stats.get("break_rate") or 0) <= (stats.get("base_break_rate") or 0)

    def keys_by_name(self):
        """Every item's key by its name, as the log and the game write it."""
        return {v.get("name"): k for k, v in recipe_book()[1].items()} | {c.get("beetle_name"): k for k, c in self.cards.items() if isinstance(c, dict)}


_BOOK = _CATALOG = None
_REMOVED = {"checked": 0, "at": None}


def usable(bag, settings):
    """What crafts may use: your bag without the items switched off on the dashboard, and without smashing with the hammers
    switched off for it (they can still be upgraded or go into a recipe)."""
    return bag.without(settings.get("locked"), settings.get("hammer_off") or ())


def recipe_book():
    """The community recipe book, without the recipes the game has said don't exist (see remove_recipe), and the full
    item list; or (None, {}) if they aren't installed. Looks again every few seconds for newly removed recipes."""
    global _BOOK, _CATALOG
    if time.time() - _REMOVED["checked"] > 5:
        _REMOVED["checked"] = time.time()
        at = REMOVED_FILE.stat().st_mtime if REMOVED_FILE.exists() else None
        if at != _REMOVED["at"]:
            _REMOVED["at"], _BOOK = at, None
    if _BOOK is None:
        _BOOK = read_json(RECIPES_FILE) or {}
        gone = read_json(REMOVED_FILE).get("recipes") or {}
        if _BOOK.get("recipes"):
            _BOOK["recipes"] = [r for r in _BOOK["recipes"] if recipe_label(r) not in gone]
        _CATALOG = read_json(CATALOG_FILE).get("items", {})
        _LABELS.clear()
        _ROUTES.clear()
    return (_BOOK if _BOOK.get("recipes") else None), _CATALOG


def remove_recipe(label, tried=None):
    """Takes a recipe the game said doesn't exist ("NOTHING HAPPENED") out of the recipe book for good: it stays out
    after reinstalls too. Auto makes picked by recipe keep pointing at the same recipes (they're numbered by place in
    the book), and one picked by this recipe goes on by any recipe. tried: what was crafted, for the dashboard."""
    book = recipe_book()[0]
    index = next((n for n, r in enumerate(book["recipes"]) if recipe_label(r) == label), None) if book else None
    removed = read_json(REMOVED_FILE)
    removed.setdefault("recipes", {})[label] = {"at": time.time(), "tried": tried}
    write_json(REMOVED_FILE, removed)
    if index is not None:
        with editing(OVERRIDES_FILE):
            overrides = read_json(OVERRIDES_FILE)
            for make in [overrides.get("make"), *(overrides.get("make_queue") or [])]:
                if isinstance(make, dict) and type(make.get("recipe")) is int and make["recipe"] >= index:
                    make["recipe"] = None if make["recipe"] == index else make["recipe"] - 1
            write_json(OVERRIDES_FILE, overrides)
    _REMOVED["checked"] = 0  # the book without it, straight away


def item_tier(key, bag, catalog):
    if key.startswith("@"):
        return (recipe_book()[0] or {}).get("groups", {}).get(key[1:], {}).get("tier", 1)
    return bag.tier(key) or (catalog.get(key) or {}).get("tier", 1)


_SEASONAL = None


def seasonal(key):
    """Holiday items (Halloween and December beetles): catch-only for a couple of weeks a year, so never spent."""
    global _SEASONAL
    if _SEASONAL is None:
        _SEASONAL = frozenset(k for k, v in recipe_book()[1].items() if v.get("season"))
    return key in _SEASONAL


def group_members(gid, bag, book):
    """The items you've discovered that a recipe-book group (like "@flower_3", any Bronze flower) stands for.
    Seasonal items never count."""
    g = book["groups"][gid]
    if "keys" in g:
        return list(g["keys"])
    return [k for k, c in bag.cards.items() if fits_match(g["match"], k, c.get("category"), c.get("rarity")) and not seasonal(k)]


def in_group(key, gid, book, catalog):
    """Whether a recipe-book group could give this item, by the full item list (so undiscovered items count).
    Seasonal items are in none: they can't be crafted, and the bot never spends them."""
    if seasonal(key):
        return False
    g = book["groups"][gid]
    if "keys" in g:
        return key in g["keys"]
    item = catalog.get(key) or {}
    return fits_match(g["match"], key, item.get("category"), item.get("tier"))


def burns_sacrifices(r, book, catalog):
    """A low-odds recipe that takes Green Beetles: the game mostly hands one Green Beetle back, so each try costs two,
    plus the one sacrificed. The planners only pick these for an Auto make of something nothing else makes (and you can,
    by its recipe)."""
    return low_odds(r) and any(x == SACRIFICE or x.startswith("@") and in_group(SACRIFICE, x[1:], book, catalog) for x in r["in"])


_ROUTES = {}  # recipes_for's answers: forgotten when the recipe book or the craft stats change (see recipe_book, craft_stats)


def recipes_for(target, book, catalog):
    """The recipes that make target, and whether they make it only by chance (a random one of its group). Remembered,
    since planning asks again and again; each caller gets its own copy of the list."""
    craft_stats()  # (if the stats have changed, that forgets what's remembered: which recipes burn sacrifices can change)
    key = (target, id(book), id(catalog))
    if key not in _ROUTES:
        _ROUTES[key] = _recipes_for(target, book, catalog)
    direct, random_route = _ROUTES[key]
    return list(direct), random_route


def _recipes_for(target, book, catalog):
    direct = [r for r in book["recipes"] if target in r["out"] and not r.get("gamble") and not burns_sacrifices(r, book, catalog)]
    if target.startswith("@"):  # "any Mithril beetle": recipes that make one particular member count too
        direct += [r for r in book["recipes"] if not r.get("gamble") and r not in direct and not burns_sacrifices(r, book, catalog)
                   and any(not o.startswith("@") and in_group(o, target[1:], book, catalog) for o in r["out"])]
    if direct or target.startswith("@"):
        return direct, False
    # One particular flower, say: aim for "a random flower of its tier".
    return [r for r in book["recipes"] if not r.get("gamble") and not (r.get("note") or "").startswith("Low odds")
            and any(o.startswith("@") and in_group(target, o[1:], book, catalog) for o in r["out"])], True


def craft_cost(target, bag, book, catalog, slot3, need=1, seen=frozenset(), memo=None, basic=None):
    """Roughly how many crafts it takes to have `need` of target, from what you have, by the cheapest recipe at each
    step (one try per possible result when the result is random, three for low-odds recipes). basic: what to count
    for each thing only hunts and claims bring; None means it can't be had now, and then so can't target.
    A guide for the dashboard: it doesn't notice two steps wanting the same item."""
    memo = {} if memo is None else memo
    have = sum(bag.count(k) for k in group_members(target[1:], bag, book)) if target.startswith("@") else bag.count(target)
    if have >= need:
        return 0
    if (target, need) in memo:
        return memo[target, need]
    if target in seen or len(seen) > PLAN_DEPTH:
        return None
    seen = seen | {target}
    direct, random_route = recipes_for(target, book, catalog)
    if not direct:
        return None if basic is None else basic * (need - have)
    best = None
    for r in direct:
        cost = 1
        if len(r["in"]) == 3 and not slot3:
            key = craft_cost(KEY, bag, book, catalog, slot3, 1, seen, memo, basic)
            if key is None:
                continue
            cost += key
        left = dict(bag.items)
        for x in sorted(r["in"], key=lambda x: x.startswith("@")):
            options = [x] if not x.startswith("@") else group_members(x[1:], bag, book)
            pick = next((k for k in options if left.get(k, 0) > 0 and k != target), None)
            if pick:
                left[pick] -= 1
                continue
            held = sum(bag.count(k) for k in group_members(x[1:], bag, book)) if x.startswith("@") else bag.count(x)
            more = craft_cost(x, bag, book, catalog, slot3, held + 1, seen, memo, basic)  # one more than you hold
            if more is None:
                cost = None
                break
            cost += more
        if cost is None:
            continue
        if random_route:
            cost *= max(1, max(sum(in_group(k, o[1:], book, catalog) for k in catalog) for o in r["out"] if o.startswith("@")))
        if (r.get("note") or "").startswith("Low odds"):
            cost *= 3
        best = cost if best is None else min(best, cost)
    cost = None if best is None else best * (need - have)
    if cost is not None:
        memo[target, need] = cost
    return cost


def low_odds(r):
    """A recipe that fails more often than it works: by what the bot has seen (see odds)."""
    return odds(r) < 0.5


def recipe_label(r):
    """A recipe as "out from in + in", the name the crafting code and the craft stats know it by."""
    if "_label" not in r:
        r["_label"] = f"{' or '.join(r['out'])} from {' + '.join(r['in'])}"
    return r["_label"]


_STATS = {"checked": 0, "at": None, "data": {}, "odds": {}}
_LABELS = {}


def recipe_named(label):
    """The recipe a craft label stands for, or None."""
    book = recipe_book()[0]
    if book and not _LABELS:
        _LABELS.update((recipe_label(r), r) for r in book["recipes"])
    return _LABELS.get(label)


def craft_stats():
    """How each recipe has gone for you, by label: {"n": tries, "outs": {item: times it came out}, "low": proven low odds
    (see proven_low_odds)}; a failed smash is a try with nothing out. Looked at again every few seconds, so a running
    dashboard sees new crafts."""
    if time.time() - _STATS["checked"] > 5:
        _STATS["checked"] = time.time()
        try:
            at = CRAFT_STATS_FILE.stat().st_mtime
        except OSError:
            at = None
        if at != _STATS["at"]:
            _STATS.update(at=at, data=read_json(CRAFT_STATS_FILE) if at else {}, odds={})
            _ROUTES.clear()
    return _STATS["data"]


def makes(r, key, book, catalog):
    """Whether key is one of what recipe r makes (a member of one of its "@groups" counts, and so does any pinned beetle,
    trophy_<beetle>, for the book's stand-in for one)."""
    return any(o == key or o.startswith("@") and not key.startswith("@") and in_group(key, o[1:], book, catalog)
               or o == "trophy_green" and key.startswith("trophy_") and (catalog.get(key[7:]) or {}).get("category") == 2 for o in r["out"])


def times_worked(r, seen):
    """How many of the tries seen made something r makes."""
    book, catalog = recipe_book()
    return sum(n for k, n in seen["outs"].items() if makes(r, k, book, catalog))


def odds(r):
    """The chance one try of a recipe works. The recipe book's (low odds: about one in LOW_ODDS_TRIES, else it works),
    weighed against what the bot has seen once it has tried it. Getting something the recipe doesn't make (the game
    handing a Green Beetle back, say) counts as not working."""
    label = recipe_label(r)
    stats = craft_stats()
    if label not in _STATS["odds"]:
        chance = 1 / LOW_ODDS_TRIES if (r.get("note") or "").startswith("Low odds") else 1
        if seen := stats.get(label):
            chance = (times_worked(r, seen) + chance * LEARN_WEIGHT) / (seen["n"] + LEARN_WEIGHT)
        _STATS["odds"][label] = chance
    return _STATS["odds"][label]


def hit_rate(r, wanted, chance):
    """The chance a try of r that works gives what's wanted (wanted(key) says whether an item counts): chance, weighed
    against what has come out before."""
    seen = craft_stats().get(recipe_label(r))
    if not seen:
        return chance
    hits = sum(n for k, n in seen["outs"].items() if wanted(k))
    return (hits + chance * LEARN_WEIGHT) / (times_worked(r, seen) + LEARN_WEIGHT)


def long_shot(r):
    """Whether a recipe is a long shot: a smash that fails at least twice as often as it works (see LONG_SHOT and odds)."""
    return r["mode"] == "smash" and odds(r) <= LONG_SHOT


def proven_low_odds(label, seen=None):
    """Whether a recipe has proven itself low odds by what the bot has seen: tried PROVE_TRIES times or more, and it failed
    (an unlucky roll) more often than something came out. Once proven, it stays so (noted as "low" in the craft stats), so a
    Purple Beetle's luck doesn't make it look easy again. seen: its craft stats, if already read."""
    seen = (craft_stats().get(label) if seen is None else seen) or {}
    came = sum((seen.get("outs") or {}).values())
    return bool(seen.get("low")) or seen.get("n", 0) >= PROVE_TRIES and seen["n"] - came > came


def count_craft(stats, label, made):
    """Adds one try of a recipe to craft stats (made: the item that came out, None if the smash failed)."""
    seen = stats.setdefault(label, {"n": 0, "outs": {}})
    seen["n"] += 1
    if made:
        seen["outs"][made] = seen["outs"].get(made, 0) + 1


CRAFT_LINE = re.compile(r"BeetleCraft: (Assemble|Smash) (.+?)(?: -> (.+?)(?: \(sacrificing.*\))?\.(?: The hammer shattered!)?|: smash failed.*)$")


def craft_specs(mode, items, made, book, catalog):
    """Which recipe a craft of these items (keys) was: the one that makes what came out (made, or None), else the most
    particular one. Returns (recipe, what each item stood for in it: "@flower_3", or the key itself), or None."""
    best = None
    for r in book["recipes"]:
        if r["mode"] != mode or len(r["in"]) != len(items) or r.get("gamble"):
            continue
        for p in permutations(range(len(items))):
            if all(x == items[i] or x.startswith("@") and in_group(items[i], x[1:], book, catalog) for x, i in zip(r["in"], p)):
                rank = (bool(made) and makes(r, made, book, catalog), sum(not x.startswith("@") for x in r["in"]))
                if not best or rank > best[0]:
                    best = rank, r, [r["in"][p.index(i)] for i in range(len(items))]
                break
    return best and best[1:]


def learn_from_log(bag):
    """The first time: fills the craft stats from the crafts already in the log, each matched to the recipe it fits
    (the one that makes what came out, else the most particular one)."""
    book, catalog = recipe_book()
    if not book or CRAFT_STATS_FILE.exists():
        return
    keys, stats = bag.keys_by_name(), {}
    try:
        lines = LOG_FILE.read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        match = CRAFT_LINE.search(line)
        if not match:
            continue
        items, made = [keys.get(n) for n in match[2].split(" + ")], keys.get(match[3]) if match[3] else None
        if None in items or match[3] and not made:
            continue
        if fit := craft_specs(match[1].lower(), items, made, book, catalog):
            count_craft(stats, recipe_label(fit[0]), made)
    write_json(CRAFT_STATS_FILE, stats)
    if stats:
        log(f"BeetleCraft: learned from {sum(s['n'] for s in stats.values())} crafts in the log ({len(stats)} recipes).")


def step_cost(r, bag, catalog):
    """What one try of a recipe costs the planner, in crafts: a smash spends a Green Beetle and wears a hammer, and an
    Adamantine-and-up or low-odds one wears it the most."""
    if r["mode"] != "smash":
        return 1
    tier = max(item_tier(k, bag, catalog) for k in r["in"] + r["out"])
    return 1.5 + RISKY_COST * (tier >= ADAMANTINE or low_odds(r))


class Planner:
    """Works out every craft it takes to make something from what you have, the whole chain down to what only hunts
    and claims bring, taking the cheapest way at each step. It plays the plan out on a copy of your inventory, so two
    steps never count on the same item, and what you already hold for a step is kept for that step.

    Random results (a random flower of a tier, Nectar or Cattail) are planned as if they come out right, and cost as
    many tries as they usually take; the bot plans again after every craft, so a wrong one just means trying again
    (like rerolling a flower with a Tesseract). How many tries that is, and how often a recipe fails, it learns from
    its own crafts (see odds and hit_rate).

    steps: in order, each {"recipe", "mode", "label", "items", "from", "makes", "out", "tries", "low"}, where "from"
    says, for each item, None (you hold it), a step number (an earlier step makes it) or "wait" (hunts and claims
    bring it)."""

    def __init__(self, bag, book, catalog, slot3, protect=(), hunted=HUNTED_COST, reserve=None, sacrifice=SACRIFICE, forge=True):
        self.bag, self.book, self.catalog, self.hunted = bag, book, catalog, hunted
        self.held = Counter(bag.items)  # what you have and no step has spoken for yet
        self.made = Counter()  # what planned steps make and no later step has spoken for yet
        self.made_by = defaultdict(list)
        self.sacrifice = sacrifice  # the beetle each smash sacrifices (see sacrifice_kept): one is kept for each, never spent in a recipe
        self.spare_sacrifices = bag.count(sacrifice)
        self.forge = forge  # with no hammer, a smash forges a Tin Hammer first (False: Auto Tin Hammer is off, so it waits for one)
        self.slot3, self.hammer = slot3, bool(bag.smash_hammers())
        self.found = set(bag.cards) | set(bag.items)
        self.protect = list(protect)  # what's being made, so never spent on the way
        self.reserve = Counter(reserve or {})  # items a step asks for by name: "any ..." slots leave these alone while others will do
        self.named = Counter()  # every item a step asks for by name, however it's had (what a second pass reserves, of what you hold)
        self.steps, self.memo = [], {}

    def fits(self, key, spec):
        return key == spec or (spec.startswith("@") and not key.startswith("@") and in_group(key, spec[1:], self.book, self.catalog))

    def usable(self, pool, key, planned):
        if seasonal(key) or any(self.fits(key, t) for t in self.protect):
            return False
        return planned or key != self.sacrifice or self.spare_sacrifices > 0

    def free(self, pool, key, spec):
        """How many of key a slot for spec may take: all of them, except that an "any ..." slot leaves alone what's reserved
        for steps that name it (even when nothing else fits: those steps need it more than an "any" slot does, and
        something else is easier to make for an "any" slot than one particular item for them)."""
        return pool[key] - (self.reserve[key] if pool is self.held and key != spec else 0)

    def available(self, spec):
        return sum(max(0, self.free(pool, k, spec)) for pool, planned in ((self.held, False), (self.made, True)) for k, n in pool.items()
                   if n > 0 and self.fits(k, spec) and self.usable(pool, k, planned))

    def take(self, spec, pin=False):
        """One of spec, from what you hold, else from what an earlier step makes: (key, None or that step's number)."""
        for pool, planned in ((self.held, False), (self.made, True)):
            keys = [k for k, n in pool.items() if n > 0 and self.fits(k, spec) and self.usable(pool, k, planned) and self.free(pool, k, spec) > 0]
            if not keys:
                continue
            if pin:  # pin the rarest beetle you have a spare of
                key = max(keys, key=lambda k: (pool[k] >= 2, item_tier(k, self.bag, self.catalog)))
            else:  # the item itself, else the one you have most of (once what's reserved is set aside)
                key = max(keys, key=lambda k: (k == spec, self.free(pool, k, spec), k))
            if pool is self.held and key == spec:
                if self.reserve[key] > 0:
                    self.reserve[key] -= 1  # that step has its one now: the rest are free for "any" slots
            pool[key] -= 1
            if key == self.sacrifice and not planned:
                self.spare_sacrifices -= 1
            return key, (self.made_by[key].pop() if planned else None)
        return None

    def routes(self, spec, anyway=False):
        """The recipes that could make spec, with how many tries each usually takes. Low-odds recipes that give a
        random one of its tier are included too, as a last resort. anyway: for what an Auto make makes, so where nothing
        else makes it, the ones that burn Green Beetles do (see burns_sacrifices)."""
        direct, _ = recipes_for(spec, self.book, self.catalog)
        if not spec.startswith("@"):
            direct += [r for r in self.book["recipes"] if r not in direct and not r.get("gamble") and not burns_sacrifices(r, self.book, self.catalog)
                       and any(o.startswith("@") and in_group(spec, o[1:], self.book, self.catalog) for o in r["out"])]
        if spec.startswith("trophy_") and spec in self.found:
            return []  # a trophy is won once
        # A recipe that uses up one of spec to make one (rerolling "any Mithril flower") gets you no more of it.
        found = [(r, self.tries(r, spec)) for r in direct if spec not in r["in"]]
        if anyway and not found:
            found = [(r, self.tries(r, spec)) for r in self.book["recipes"] if not r.get("gamble") and spec not in r["in"] and burns_sacrifices(r, self.book, self.catalog)
                     and any(o == spec or o.startswith("@") and in_group(spec, o[1:], self.book, self.catalog) for o in r["out"])]
        return found

    def tries(self, r, spec):
        """About how many tries of r it takes to get spec: by the recipe book and what the bot has seen."""
        outs = [o for o in r["out"]] if not r.get("first_trophy") else r["out"][:1]
        if spec in outs or any(self.fits(o, spec) for o in outs):
            hits = sum(o == spec or self.fits(o, spec) for o in outs)
            chance = hits / len(outs) if r.get("pick") else 1
        else:  # a random one of the group: about one try per member
            chance = 1 / max(1, sum(sum(in_group(k, o[1:], self.book, self.catalog) for k in self.catalog) for o in outs if o.startswith("@")))
        return 1 / (odds(r) * hit_rate(r, lambda k: k == spec or self.fits(k, spec), chance))

    def wait_cost(self, spec):
        """About how many crafts' worth waiting for hunts and claims to bring one of spec is, or None if they don't."""
        if spec in DROPS:
            cost = DROPS[spec]
        else:
            cost = min((2 * c for g, c in DROPS.items() if g.startswith("@") and not spec.startswith("@")
                        and in_group(spec, g[1:], self.book, self.catalog)), default=None)
        return None if cost is None else cost * self.hunted / HUNTED_COST

    def worth(self, spec):
        """About how many crafts one more of spec costs, from what you hold (0 if one's free to use)."""
        if self.available(spec):
            return 0
        held = sum(self.bag.count(k) for k in group_members(spec[1:], self.bag, self.book)) if spec.startswith("@") else self.bag.count(spec)
        costs = [craft_cost(spec, self.bag, self.book, self.catalog, self.slot3, held + 1, memo=self.memo, basic=self.hunted), self.wait_cost(spec)]
        return min((c for c in costs if c is not None), default=self.hunted)

    def estimate(self, r, tries):
        cost = step_cost(r, self.bag, self.catalog)
        need = Counter(r["in"])
        for x, n in need.items():
            short = max(0, n - self.available(x))
            cost += short * self.worth(x)
        if len(r["in"]) == 3 and not self.slot3:
            cost += self.worth(KEY)
        if r["mode"] == "smash" and not self.hammer:
            cost += self.worth("hammer_t1")
        return cost * tries

    def obtain(self, spec, depth=0, path=frozenset(), recipe=None, new=False, pin=False, anyway=False):
        """Plans getting one of spec (new: one more than you hold; recipe: by that recipe; anyway: see routes). Returns
        (key, source)."""
        if not new and not spec.startswith("@"):
            self.named[spec] += 1
        got = None if new or recipe is not None else self.take(spec, pin)
        if got:
            return got
        options = [(recipe, self.tries(recipe, spec))] if recipe is not None else self.routes(spec, anyway)
        best = min(options, key=lambda o: self.estimate(*o)) if options else None
        wait = None if recipe is not None or new else self.wait_cost(spec)
        if depth > PLAN_DEPTH or spec in path or not best or (wait is not None and wait < self.estimate(*best)):
            return spec, "wait"  # hunts and claims bring it, or nothing else does
        return self.craft(best[0], spec, best[1], depth, path | {spec})

    def craft(self, r, spec, tries, depth, path):
        trophy_first = r.get("first_trophy") and spec == r["out"][1] and r["out"][0] not in self.found
        for run in range(2 if trophy_first else 1):  # the first success wins the trophy, the next makes the trinket
            if len(r["in"]) == 3 and not self.slot3:
                self.obtain(KEY, depth + 1, path)  # the bot unlocks slot 3 as soon as it has the key
                self.slot3 = True
            if r["mode"] == "smash" and not self.hammer:
                if self.forge and "hammer_t1" not in self.bag.hammers() and "hammer_t1" not in self.bag.smash_off:  # (you can only own one of each)
                    self.obtain("hammer_t1", depth + 1, path)
                self.hammer = True
            got = [None] * len(r["in"])
            # What you already hold goes first, so it's kept for this step; exact items before "any ..." slots.
            for n in sorted(range(len(r["in"])), key=lambda n: (not self.available(r["in"][n]), r["in"][n].startswith("@"))):
                got[n] = self.obtain(r["in"][n], depth + 1, path, pin="specimen_pin" in r["in"] and r["in"][n].startswith("@"))
            if r["mode"] == "smash" and self.spare_sacrifices > 0:
                self.spare_sacrifices -= 1  # the beetle it sacrifices
            outs = r["out"][:1] if trophy_first and run == 0 else r["out"]
            out = (r["out"][0] if trophy_first and run == 0 else spec if spec in outs or not spec.startswith("@")
                   else next((o for o in outs if self.fits(o, spec)), spec))
            self.steps.append({"recipe": self.book["recipes"].index(r), "mode": ASSEMBLE if r["mode"] == "assemble" else SMASH,
                               "label": f"{' or '.join(r['out'])} from {' + '.join(r['in'])}", "items": [k for k, _ in got],
                               "from": [src for _, src in got], "makes": spec, "out": out, "tries": tries, "low": low_odds(r)})
            if trophy_first and run == 0:
                self.found.add(r["out"][0])
        number = len(self.steps) - 1
        return out, number

    def ready(self):
        """The planned crafts you could do right now (everything they use, you hold), in plan order, as
        (label, Assemble or Smash, items, what the plan wants from it)."""
        for step in self.steps:
            if not step.get("unlock") and all(src is None for src in step["from"]):  # (unlocking slot 3 the bot does on its own)
                yield step["label"], step["mode"], step["items"], step["out"]


def two_pass(build):
    """A plan made twice when it matters: build(reserve) makes a Planner with those items reserved. The first pass finds
    which items you hold that steps ask for by name; the second leaves them alone in "any ..." slots (a Ladybug a later step
    needs isn't spent as an "Any Bronze Beetle" earlier), so the plan doesn't wait on, or craft, what you already hold."""
    plan = build(None)
    reserve = Counter({k: min(n, plan.bag.count(k)) for k, n in plan.named.items() if plan.bag.count(k)})
    return build(reserve) if reserve else plan


def plan_make(bag, slot3, make, book, catalog, hunted=HUNTED_COST, target=None, beside=None, sacrifice=SACRIFICE, forge=True):
    """The plan for an Auto make: one more of its item, by its recipe if one was picked. With target, a plan for just that
    (something a step of the plan beside waits for, see ready_crafts). sacrifice: see sacrifice_kept; forge: see Planner."""
    recipe = book["recipes"][make["recipe"]] if make.get("recipe") is not None else None

    def build(reserve):
        plan = Planner(bag, book, catalog, slot3, protect=make["keys"][:1] + [target] * bool(target), hunted=hunted, reserve=kept_for(beside, reserve),
                       sacrifice=sacrifice, forge=forge)
        plan.obtain(target, new=True) if target else plan.obtain(make["keys"][0], recipe=recipe, new=True, anyway=True)  # (a new one: the plan beside has the ones you hold)
        return plan
    return two_pass(build)


def kept_for(beside, reserve):
    """reserve (see two_pass), and what you hold that the plan beside asks for by name, if there's one."""
    return reserve if beside is None else Counter(reserve or {}) + Counter({k: min(n, beside.bag.count(k)) for k, n in beside.named.items() if beside.bag.count(k)})


def need_targets(need):
    """The items that meet one of a quest's needs (any one of them will do)."""
    if need == "slot3":
        return [KEY]
    if need == "pinned":
        return ["trophy_green"]  # the recipe book's stand-in for "a pinned beetle"
    return need if isinstance(need, list) else [need.removeprefix("own:")]


def plan_goal(bag, slot3, settings, book, catalog, hunted=HUNTED_COST, target=None, beside=None):
    """The plan for the quest picked on the dashboard (see Planner): everything it still needs, the cheapest of the items
    that would do for each, planned like an Auto make, so items a later step needs by name are kept for it (see two_pass).
    What's still needed is by what you really have; the plan only uses what isn't switched off. Unlocking slot 3 shows as
    a step of its own ({"unlock": True}). target and beside: see plan_make."""
    needs, spare = goal_needs_left(bag, slot3, settings), usable(bag, settings)

    def build(reserve):
        plan = Planner(spare, book, catalog, slot3, protect=[n[4:] for n in needs if isinstance(n, str) and n.startswith("own:")], hunted=hunted, reserve=kept_for(beside, reserve),
                       sacrifice=sacrifice_kept(settings), forge=settings.get("tin_hammer", True))
        if target:
            plan.protect.append(target)
            plan.obtain(target, new=True)  # (a new one: the plan beside has the ones you hold)
            return plan
        for need in needs:
            plan.obtain(min(need_targets(need), key=plan.worth))
            if need == "slot3":
                plan.steps.append({"unlock": True})
        return plan
    return two_pass(build)


def goal_crafts(bag, slot3, settings, book, catalog, skipped=frozenset(), fallback=True, first=None):
    """Crafts toward the quest picked on the dashboard that can be done now (see plan_goal and ready_crafts). Empty if
    there's no goal or it's done."""
    yield from ready_crafts(lambda hunted, *more: plan_goal(bag, slot3, settings, book, catalog, hunted, *more), skipped, fallback, first)


def ready_crafts(build, skipped=frozenset(), fallback=True, first=None):
    """The crafts that can be done now, in plan order, as (label, Assemble or Smash, items, what the plan wants from it,
    where it's from: 0 the plan, 2 a plan for a waiting step): from the plan (build(HUNTED_COST)), then, in case none of
    those can go (say they all wait on junk from hunts), from plans for just what the plan's steps wait for
    (build(STUCK_COST, what, the plan)), made from what you hold even where the plan had set it aside for a later step,
    so the bot doesn't sit idle holding it (what a step asks for by name stays kept for it, and Green Beetles are left
    for the smashes: its own, and the waiting step's). (1 was a backup plan, in older records.) Steps skipped on the dashboard (by recipe label) wait while the plan
    has any other step left that doesn't need them, even one that can't go yet, so they're done last of all; the steps
    that need what they make don't hold them up. Within each plan the sure steps go first and the long shots (see
    long_shot) after them, so Green Beetles go where they make progress. first: the recipe of a step picked on the dashboard
    to work on first: it and the steps that make what it needs go before all the rest, long shots too. fallback: False for
    the plan's own steps only (none for waiting steps)."""
    main = build(HUNTED_COST)
    held_up = set()  # skipped steps, and the steps that need what they make (steps come after the ones they need)
    for i, s in enumerate(main.steps):
        if not s.get("unlock") and (s["label"] in skipped or any(isinstance(f, int) and f in held_up for f in s["from"])):
            held_up.add(i)
    wait = any(not s.get("unlock") and i not in held_up for i, s in enumerate(main.steps))
    def plans():  # (each made only when the ones before it have nothing that can go)
        yield 0, main
        if fallback:
            for spec, smash in waited_for(main.steps, held_up):
                plan = build(STUCK_COST, spec, main)
                if plan.bag.count(plan.sacrifice) >= sacrifices_used(plan) + smash:
                    yield 2, plan
    for tier, plan in plans():
        ahead, sure, later = needed_for(plan.steps, first), [], []
        for craft in plan.ready():
            craft = (*craft, tier)
            if wait and craft[0] in skipped:
                continue
            if craft[0] in ahead:
                yield craft
            elif (r := recipe_named(craft[0])) is not None and long_shot(r):
                later.append(craft)
            else:
                sure.append(craft)
        yield from sure
        yield from later


def waited_for(steps, held_up=(), most=3):
    """What a plan's steps wait on hunts and claims for, in plan order, each once and only the first few, as (an item, or
    an "any ..." like "@flower_2"; whether the step waiting for it is a smash); held_up: the steps not to count."""
    specs = {}
    for i, s in enumerate(steps):
        if not s.get("unlock") and i not in held_up:
            for spec, f in zip(s["items"], s["from"]):
                if f == "wait":
                    specs.setdefault(spec, s["mode"] == SMASH)
    return list(specs.items())[:most]


def sacrifices_used(plan):
    """How many of the beetle its smashes sacrifice a plan's crafts use: in recipes, and one for each smash."""
    return sum(s["items"].count(plan.sacrifice) + (s["mode"] == SMASH) for s in plan.steps if not s.get("unlock"))


def sacrifice_kept(settings):
    """The beetle the planners keep one of for each smash to sacrifice: a Purple one while Crafting Beetles (in Analytics)
    is set to Purple, else a Green one (Auto sacrifices Purple ones only for smashes proven low odds: see pick_sacrifice)."""
    return PURPLE if settings.get("sacrifice") == PURPLE else SACRIFICE


def needed_for(steps, label):
    """The recipes of a plan's steps called label and of the steps that make what they need (further back too)."""
    need = set()
    for i in range(len(steps) - 1, -1, -1):  # (steps come after the ones they need)
        s = steps[i]
        if not s.get("unlock") and (s["label"] == label or i in need):
            need.add(i)
            need.update(f for f in s["from"] if isinstance(f, int))
    return {steps[i]["label"] for i in need if not steps[i].get("unlock")}


def label_of(key, bag, book):
    """An item's name (from the full item list if you haven't found it yet), or a recipe-book group's ("A Mithril flower")."""
    if key.startswith("@"):
        return book["groups"].get(key[1:], {}).get("label") or key
    return bag.card(key).get("beetle_name") or (recipe_book()[1].get(key) or {}).get("name") or bag.name(key)


def make_title(keys, bag, book):
    """What an Auto make makes, in words: "Daisy", or "Daisy or Sunflower"."""
    return " or ".join(label_of(k, bag, book) for k in keys)


def new_make(keys, recipe, bag, book):
    """An Auto make starting now: it's made once you hold more of keys than now."""
    return {"keys": keys, "recipe": recipe, "set_at": time.time(), "start": make_count(keys, bag, book)}


def make_count(keys, bag, book):
    """How many of an Auto make's items you hold (a "@group" key counts every member)."""
    return sum(sum(bag.count(m) for m in group_members(k[1:], bag, book)) if k.startswith("@") else bag.count(k) for k in keys)


def make_finished(bag, make, book):
    """Whether an Auto make is made: you hold more of it than when it was picked."""
    return make_count(make["keys"], bag, book) > make["start"]


def active_make(bag, settings, book):
    """The Auto make picked on the dashboard, until it's made."""
    make = settings.get("make")
    if not make or not book or (make.get("recipe") is not None and not 0 <= make["recipe"] < len(book["recipes"])):
        return None
    return None if make_finished(bag, make, book) else make


def advance_queues(bag, slot3, book):
    """When the Auto make or quest is done (or there's none), starts the next one queued on the dashboard. Returns
    whether one started."""
    with editing(OVERRIDES_FILE):  # (a change made on the dashboard meanwhile stays)
        return _advance_queues(bag, slot3, book)


def _advance_queues(bag, slot3, book):
    overrides, started = read_json(OVERRIDES_FILE), False
    make, queue = overrides.get("make"), [m for m in overrides.get("make_queue") or [] if isinstance(m, dict) and m.get("keys")]
    if queue and (not valid_make(make) or make_finished(bag, make, book)):
        nxt = queue.pop(0)
        overrides.update(make=new_make(nxt["keys"], nxt.get("recipe"), bag, book), make_queue=queue)
        if overrides.get("goal"):
            overrides["goal_paused"] = True  # only one plays at a time, and this one just started
        log(f"BeetleCraft: next Auto make from the queue: {make_title(nxt['keys'], bag, book)}.")
        started = True
    goal, goals = overrides.get("goal"), [g for g in overrides.get("goal_queue") or [] if isinstance(g, dict) and g.get("quest")]
    if goals and (not goal or not goal_needs_left(bag, slot3, {"goal": goal, "goal_again": overrides.get("goal_again") is True})):
        nxt = goals.pop(0)
        overrides.update(goal=nxt["quest"], goal_again=nxt.get("again") is True, goal_set_at=time.time(), goal_paused=False, goal_queue=goals)
        if valid_make(overrides.get("make")) and not make_finished(bag, overrides["make"], book):
            overrides["make"]["paused"] = True
        log(f"BeetleCraft: next quest from the queue: {nxt['quest']}.")
        started = True
    if started:
        write_json(OVERRIDES_FILE, overrides)
    return started


def note_make_done(state, settings, bag, book):
    """Logs once that the Auto make is made, and what the bot goes back to."""
    make = settings.get("make")
    if make and book and state.get("make_done") != make["set_at"] and make_finished(bag, make, book):
        log(f"BeetleCraft: Auto make is done ({make_title(make['keys'], bag, book)}). "
            + ("Next one from the queue." if read_json(OVERRIDES_FILE).get("make_queue") else
               "Back to the quest you picked." if settings.get("goal") else "Nothing more to craft until you pick another."))
        state["make_done"] = make["set_at"]
        alert("make_done", "Auto make done", f"{make_title(make['keys'], bag, book)} is made.")
        task, logs = task_id("make", settings), state.get("task_logs") or {}
        if task in logs:  # its record is put away: another of the same item later (a hammer made, broken, made again) starts afresh
            logs[finished_key(task, make)] = logs.pop(task)


def finished_key(task, make):
    """Where a finished Auto make's record is kept (see note_make_done), so the dashboard can still show it."""
    return f"{task} done {make['set_at']}"


def make_crafts(bag, slot3, make, book, catalog, skipped=frozenset(), fallback=True, first=None, sacrifice=SACRIFICE, forge=True):
    """Crafts toward the Auto make that can be done now (see plan_make and ready_crafts)."""
    yield from ready_crafts(lambda hunted, *more: plan_make(bag, slot3, make, book, catalog, hunted, *more, sacrifice=sacrifice, forge=forge), skipped, fallback, first)


def craft_focus(bag, slot3, settings, book):
    """What crafting works toward now: ("make", the Auto make), ("goal", the quest), ("paused", None): nothing, since
    what's picked is paused, or ("idle", None): nothing picked, so nothing to craft. An Auto make goes before the quest;
    while it's paused, the quest goes on if that's playing. A finished quest counts as none."""
    make = active_make(bag, settings, book)
    goal = settings.get("goal") if book and goal_needs_left(bag, slot3, settings) else None
    if make and not make.get("paused"):
        return "make", make
    if goal and not settings.get("goal_paused"):
        return "goal", goal
    return ("paused", None) if make or goal else ("idle", None)


def junk_cube_crafts(bag, book):
    """Loose junk into a Junk Cube, two pieces at a time, the ones you have most of first (for Auto Junk Cubes)."""
    junk = sorted(group_members("junk", bag, book), key=lambda k: -bag.count(k))
    pieces = [k for k in junk for _ in range(bag.count(k))]
    if len(pieces) >= 2:
        yield JUNK_CUBE_CRAFT, ASSEMBLE, pieces[:2], "junk_cube_t1", 0


def tin_hammer_crafts(bag, spare, book):
    """Two Junk Cubes into a Tin Hammer while you own none (for Auto Tin Hammer). bag: what you have (you can only own one
    hammer of each tier); spare: what crafts may use (see usable)."""
    if "hammer_t1" not in bag.hammers() and spare.count("junk_cube_t1") >= 2 and any(recipe_label(r) == TIN_HAMMER_CRAFT for r in book["recipes"]):
        yield TIN_HAMMER_CRAFT, ASSEMBLE, ["junk_cube_t1", "junk_cube_t1"], "hammer_t1", 0


def task_id(kind, settings):
    """The Auto make ("make") or quest ("goal") as one string, new each time one is picked; None for anything else. An Auto
    make is its item and how many you held when it began, so picking it again (after stopping it, say) carries on where
    it was, and only making one more starts a fresh record."""
    if kind == "make" and settings.get("make"):
        make = settings["make"]
        return f"make {','.join(make['keys'])}@{make.get('start', 0)}"
    if kind == "goal" and settings.get("goal"):
        return f"goal {settings['goal']} {settings.get('goal_set_at')}"
    return None


def note_craft(state, task, label, what, made, aim=None, source=0):
    """Notes a craft that went through: in the stats the planner learns from, and (with task, the Auto make or quest it
    was for) for the dashboard's craft console. made: the game's card for what came out, None if the smash failed. aim:
    the name of what the plan wanted from it; source: 2 if it was made for a waiting step, else 0 (see ready_crafts)."""
    stats, r = read_json(CRAFT_STATS_FILE), recipe_named(label)
    before = times_worked(r, stats[label]) if r and label in stats else 0
    count_craft(stats, label, made and made["beetle"])
    if proven_low_odds(label, stats[label]):
        stats[label]["low"] = True  # (for good: see proven_low_odds)
    write_json(CRAFT_STATS_FILE, stats)
    _STATS["checked"] = 0  # count it straight away
    if r and not before and made and makes(r, made["beetle"], *recipe_book()):  # the first time it's worked: it's verified now
        log(f"BeetleCraft: verified a recipe: {recipe_words(r)}.")
    if not task:
        return
    made = made and made.get("beetle_name", made["beetle"])
    record = task_record(state, task) or {"task": task, "count": 0, "made": 0, "done": []}
    record["made"] = made_count(record) + bool(made)  # (failed smashes count as crafts tried, not as steps done)
    record["count"] += 1
    record["done"] = (record["done"] + [[time.time(), what, made, aim, source]])[-TASK_LOG_KEEP:]
    logs = state.setdefault("task_logs", {})
    logs.pop(task, None)
    logs[task] = record  # (the newest last)
    while len(logs) > TASK_LOGS_KEPT:
        del logs[next(iter(logs))]


def step_picks(kind, settings):
    """What's picked on the dashboard for the steps of this Auto make or quest (kind "make" or "goal"), by recipe (see
    valid_steps): skipped steps wait until they're all that's left of the plan, so the plan still gets done, and the
    "first" one goes first with the steps it needs (see ready_crafts); a smash can have its own beetle and hammer (see
    pick_sacrifice and pick_hammer). Nothing picked, for any other."""
    steps = settings.get("steps") or {}
    if kind in ("make", "goal") and steps.get("task") and steps["task"] == task_id(kind, settings):
        return steps
    return {"task": None, "skipped": [], "first": None, "sacrifice": {}, "hammer": {}}


def task_record(state, task):
    """The crafts recorded for a task (see task_id): {"task", "count", "made", "done": [[when, what, made or None], ...]}, or {}."""
    record = (state.get("task_logs") or {}).get(task)
    return record if isinstance(record, dict) else {}


def made_count(record):
    """How many of a task's crafts made something (not an unlucky smash): its steps done."""
    return record["made"] if isinstance(record.get("made"), int) else 0


def recipe_words(r):
    """A recipe as the recipe book writes it, in words: "Smash Any Bronze Beetle + Bronze Pollen -> Nectar or Cattail"."""
    book, catalog = recipe_book()
    name = lambda k: (book["groups"].get(k[1:], {}).get("label") or k) if k.startswith("@") else (catalog.get(k) or {}).get("name") or k  # noqa: E731
    return f"{'Smash' if r['mode'] == 'smash' else 'Assemble'} {' + '.join(map(name, r['in']))} -> {' or '.join(map(name, r['out']))}"


MAX_OUTSIDE_CRAFTS = 4  # crafts told apart in one change of the bag, at most


def infer_crafts(before, after, book, catalog):
    """Works out which crafts were made on RemiliaNET itself (not by the bot) from how your bag changed. Items that
    leave the bag can only have gone into crafts, so they say what was used; each craft must also be a recipe in the
    recipe book that gives something that came into the bag. A smash also takes a Green Beetle to sacrifice.
    Returns (crafts, shattered, failed): crafts as (recipe, the items used, what it made), the hammer that broke
    (key or None), and how many smashes failed (they lose only the Green Beetle). If more than one way fits, or none,
    crafts is empty: it's only a guess, so it never guesses between two."""
    have0, have1 = (b.get("inventory") or {} for b in (before, after))
    gone = Counter({k: n - have1.get(k, 0) for k, n in have0.items() if k != "cheese" and isinstance(n, int) and n > have1.get(k, 0)})
    came = Counter({k: n - have0.get(k, 0) for k, n in have1.items() if k != "cheese" and isinstance(n, int) and n > have0.get(k, 0)})
    if not gone:
        return [], None, 0
    recipes = [(n, r) for n, r in enumerate(book["recipes"]) if not r.get("gamble")]

    def inputs_from(r, pool):  # the ways r's ingredients can be picked from what's left
        found = set()

        def pick(i, left, chosen):
            if i == len(r["in"]):
                found.add(tuple(sorted(chosen)))
                return
            spec = r["in"][i]
            for k in [spec] if not spec.startswith("@") else [k for k in left if left[k] > 0 and in_group(k, spec[1:], book, catalog)]:
                if left[k] > 0:
                    left[k] -= 1
                    pick(i + 1, left, chosen + [k])
                    left[k] += 1
        pick(0, Counter(pool), [])
        return found

    def explain(pool):
        solutions = set()

        def go(left, new, chosen):
            if len(solutions) > 3:
                return
            if not +left:
                solutions.add(tuple(sorted(chosen)))
                return
            if len(chosen) >= MAX_OUTSIDE_CRAFTS:
                return
            for n, r in recipes:
                for used in inputs_from(r, left):
                    rest = left - Counter(used)
                    if r["mode"] == "smash":
                        if rest[SACRIFICE] < 1:
                            continue
                        rest = rest - Counter({SACRIFICE: 1})
                    for out in [k for k in new if new[k] > 0 and makes(r, k, book, catalog)]:
                        go(rest, new - Counter({out: 1}), chosen + [(n, used, out)])
        go(pool, came, [])
        return solutions

    for lost in [None] + sorted(k for k in gone if k.startswith("hammer_t")):  # a smash can also break its hammer
        pool = gone if lost is None else gone - Counter({lost: 1})
        found = explain(pool)
        if len(found) == 1:
            return [(book["recipes"][n], list(used), out) for n, used, out in found.pop()], lost, 0
        if len(found) > 1:
            return [], None, 0  # more than one way fits: not sure
        if set(pool) == {SACRIFICE}:  # only a Green Beetle went: smashes that failed
            return [], lost, pool[SACRIFICE]
    return [], None, 0


def note_outside_crafts(before, after, cards):
    """Logs the crafts you made on RemiliaNET yourself, worked out from how the game changed (see infer_crafts), the way
    the bot logs its own, so they show up in the feed, the Crafts count and the notifications, and count toward verifying
    recipes. Said as made "by you, on RemiliaNET", since it's worked out, not seen."""
    book, catalog = recipe_book()
    if not book or (before.get("inventory") or {}) == (after.get("inventory") or {}):
        return
    crafts, shattered, failed = infer_crafts(before, after, book, catalog)
    if not (crafts or failed or shattered):
        return
    bag = Bag(after, cards)
    name = lambda k: label_of(k, bag, book)  # noqa: E731
    smashes = sum(r["mode"] == "smash" for r, _, _ in crafts) + failed
    # The hammer used: the one whose smash count went up (or the one that broke).
    count = lambda g: {h.get("hammer"): h for h in g.get("hammers") or [] if isinstance(h, dict)}  # noqa: E731
    was, now = count(before), count(after)
    used = next((k for k in was if k in now and (now[k].get("craft_count") or 0) > (was[k].get("craft_count") or 0)), shattered)
    hammer = f"; {name(used)} at {was[used].get('break_rate') or 0}% break chance" if used in was and smashes else ""
    for n, (r, items, out) in enumerate(crafts):
        left, ordered = list(items), []
        for spec in r["in"]:  # in the order the recipe lists them, as the bot's own lines do
            k = next(k for k in left if k == spec or spec.startswith("@") and in_group(k, spec[1:], book, catalog))
            left.remove(k)
            ordered.append(k)
        what = f"{'Smash' if r['mode'] == 'smash' else 'Assemble'} {' + '.join(map(name, ordered))}"
        broke = " The hammer shattered!" if shattered and n == len(crafts) - 1 and r["mode"] == "smash" else ""
        log(f"BeetleCraft: {what} -> {name(out)} (by you, on RemiliaNET{hammer if r['mode'] == 'smash' else ''}).{broke}")
        note_craft({}, None, recipe_label(r), what, {"beetle": out, "beetle_name": name(out)})
        if broke:
            alert("hammer_broke", "A hammer broke", f"Your {name(used)} shattered during a smash." if used else "A hammer shattered during a smash.")
    if failed and shattered:
        alert("hammer_broke", "A hammer broke", f"Your {name(used)} shattered during a smash." if used else "A hammer shattered during a smash.")
    if failed:
        log(f"BeetleCraft: Smash failed: {'a Green Beetle' if failed == 1 else f'{failed} Green Beetles'} lost (by you, on RemiliaNET{hammer}).{' The hammer shattered!' if shattered else ''}")


def goal_needs_left(bag, slot3, settings):
    """What the quest picked on the dashboard still needs (see quests.goal_needs)."""
    beetles = {k for k, c in bag.cards.items() if c.get("category") == CATEGORY_BEETLE}
    return quests.goal_needs(settings.get("goal"), set(bag.cards) | set(bag.items), set(bag.items), slot3, beetles, settings.get("goal_again"))
