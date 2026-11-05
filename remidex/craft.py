"""BeetleCraft: choosing the next craft and the hammer."""

import time

from remidex.core import (
    APP_DIR,
    ASSEMBLE,
    BEETLE_RETRY,
    HAMMERS,
    JUNK_CUBE_CRAFT,
    KEY,
    OVERRIDES_FILE,
    PURPLE,
    SACRIFICE,
    SMASH,
    STATE_FILE,
    TIN_HAMMER_CRAFT,
    log,
    read_json,
    stamp,
    write_json,
)
from remidex.settings import alert
from remidex.recipes import (
    Bag,
    advance_queues,
    craft_focus,
    goal_crafts,
    goal_needs_left,
    junk_cube_crafts,
    label_of,
    learn_from_log,
    make_crafts,
    note_craft,
    note_make_done,
    long_shot,
    proven_low_odds,
    recipe_book,
    recipe_named,
    remove_recipe,
    sacrifice_kept,
    step_picks,
    task_id,
    tin_hammer_crafts,
    usable,
)
from remidex.says import SCREEN, craft_says
from remidex.state import quest_title, save_game
from remidex.beetle import beetle_action


CRAFTS_PER_RUN = 8
CRAFT_GAP = 5  # seconds counted down before each craft, on the craft screen's little status screen (a pause meanwhile stops it)
CRAFT_NOW_FILE = APP_DIR / "craft_now.json"  # what crafting is doing this moment, for that little screen
CRAFT_CHECK = 15 * 60  # with nothing to craft, look again this often (hunts and claims bring new items)
FOR_GOOD = 3650 * 86400  # a recipe the game says doesn't exist ("NOTHING HAPPENED") is switched off this long
PURPLE_OFF = "purple sacrifice"  # in craft_off: until when Purple Beetles aren't offered as a sacrifice (the game refused one)


def craft_candidates(bag, slot3, settings, fallback=True):
    """The crafts toward the Auto make picked on the dashboard (a picked quest waits until it's made), else toward the
    quest, best first. Nothing when neither is picked, or what's picked is paused: the bot only crafts for those.
    With Auto Junk Cubes on, loose junk goes into Junk Cubes first, and with Auto Tin Hammer on, two Junk Cubes into a
    Tin Hammer while you have none, whatever else is going on. fallback: False for the
    plan's own steps only, none for waiting steps (see ready_crafts)."""
    book, catalog = recipe_book()
    spare = usable(bag, settings)  # items switched off on the dashboard are never used
    if settings.get("junk_cubes") and book:
        yield from junk_cube_crafts(spare, book)
    if settings.get("tin_hammer", True) and book:
        yield from tin_hammer_crafts(bag, spare, book)
    focus, make = craft_focus(bag, slot3, settings, book)
    picks = step_picks(focus, settings)  # steps picked on the dashboard: skipped ones done last of all, the "first" one first
    skipped = set(picks["skipped"])
    if focus == "make":
        yield from make_crafts(spare, slot3, make, book, catalog, skipped, fallback, picks["first"], sacrifice_kept(settings), settings.get("tin_hammer", True))
    elif focus == "goal":
        yield from goal_crafts(bag, slot3, settings, book, catalog, skipped, fallback, picks["first"])


def side_craft(settings, label):
    """The switch in Analytics a craft is for ("Auto Junk Cubes" or "Auto Tin Hammer"), or None: the Auto make's or quest's."""
    if settings.get("junk_cubes") and label == JUNK_CUBE_CRAFT:
        return "Auto Junk Cubes"
    return "Auto Tin Hammer" if settings.get("tin_hammer", True) and label == TIN_HAMMER_CRAFT else None


def focus_picks(bag, slot3, settings):
    """What's picked on the dashboard for the steps of what crafting works toward now (see step_picks)."""
    return step_picks(craft_focus(bag, slot3, settings, recipe_book()[0])[0], settings)


def pick_sacrifice(bag, label, mode, items, purple=True, picked=None, beetle="auto"):
    """The beetle a smash sacrifices: a Purple Beetle, the rarer one, only for a smash proven to be low odds (see
    proven_low_odds) and only one the craft itself doesn't use; else a Green Beetle. None when there's no beetle it may
    use, so the smash waits: a Purple Beetle is never spent on an easy or sure smash. "" for an assemble. purple: False
    while Purple Beetles are switched off as a sacrifice (see PURPLE_OFF). picked: the beetle picked on the dashboard for
    this step: that one or none. beetle: Crafting Beetles in Analytics ("green" or "purple": only that one, for every
    step without its own pick; "auto": as above)."""
    if mode != SMASH:
        return ""
    spare = lambda k: bag.count(k) - items.count(k)  # noqa: E731
    picked = picked or (beetle if beetle in (SACRIFICE, PURPLE) else None)
    if picked:
        return picked if spare(picked) > 0 and (picked != PURPLE or purple) else None
    if purple and spare(PURPLE) > 0 and proven_low_odds(label):
        return PURPLE
    return SACRIFICE if spare(SACRIFICE) > 0 else None


def pick_hammer(bag, mode, items, slot3, settings, risky=False, sacrifice=SACRIFICE, picked=None):
    """The hammer for this craft ("" for Assemble), or None if it can't be done now: items missing, slot 3 locked, no
    beetle to sacrifice (sacrifice: which one, see pick_sacrifice), or every hammer at its limit. Hammers are used in the
    order picked on the dashboard (Tin first unless picked), each while its break chance is under its own limit. risky: a
    smash that mostly fails, which (with hammer timing on) waits for a fresh hammer, right after the daily reset. picked:
    the hammer picked on the dashboard for this step: only that one, still only while it's under its limit."""
    if len(items) == 3 and not slot3:
        return None
    need = {k: items.count(k) for k in items}
    if mode == SMASH:
        need[sacrifice] = need.get(sacrifice, 0) + 1
    if any(bag.count(k) < n for k, n in need.items()):
        return None
    if mode == ASSEMBLE:
        return ""
    owned, order = bag.smash_hammers(), settings.get("hammer_order") or HAMMERS
    for key in [picked] if picked else order + sorted(k for k in owned if k not in order):
        if key in owned:
            wear, limit = owned[key][1], (settings.get("hammer_limits") or {}).get(key, settings.get("max_break_chance", 25))
            if wear < limit and not (risky and settings.get("hammer_timing", True) and not bag.fresh(key)):
                return key
    return None


def risky(label, mode):
    """A long shot (see long_shot): with hammer timing on, it waits for a fresh hammer."""
    r = recipe_named(label)
    return mode == SMASH and r is not None and long_shot(r)


def next_craft(bag, slot3, settings, off):
    """The craft to do now: (label, Assemble or Smash, items, hammer, the beetle it sacrifices, what the plan wants from it,
    where it's from: 0 the plan, 2 a plan for a waiting step, see ready_crafts), or None. off holds recipes
    switched off (and PURPLE_OFF)."""
    spare, purple, picks = usable(bag, settings), off.get(PURPLE_OFF, 0) <= time.time(), focus_picks(bag, slot3, settings)
    for label, mode, items, aim, source in craft_candidates(bag, slot3, settings):
        if off.get(label, 0) > time.time() or (sacrifice := pick_sacrifice(spare, label, mode, items, purple, picks["sacrifice"].get(label), settings.get("sacrifice"))) is None:
            continue
        if (hammer := pick_hammer(spare, mode, items, slot3, settings, risky(label, mode), sacrifice, picks["hammer"].get(label))) is not None:
            return label, mode, items, hammer, sacrifice, aim, source
    return None


def craft_wait(bag, slot3, settings, off):
    """Why next_craft found nothing, for the dashboard. It's about the step the bot does next: the plan's first step it
    holds everything for (the one the craft console shows in yellow). "idle" (no Auto make or quest), "paused" (what's
    picked is paused), "items" (no step has everything yet: hunts and claims must bring more), "slot3" (a three-item
    craft, with crafting slot 3 still locked), "hammer" (no hammer yet), "hammers_off" (every hammer you have is switched
    off for smashing), "beetles" (no beetle to sacrifice: no Green one, and no Purple one or the smash isn't proven low
    odds), "fresh" (a risky smash waiting for a fresh hammer), "worn" (every hammer is at its break limit), "picked_beetle"
    (the beetle picked for the step on the dashboard isn't there) or "picked_hammer" (the hammer picked for it can't smash now)."""
    focus = craft_focus(bag, slot3, settings, recipe_book()[0])[0]
    if focus in ("idle", "paused"):
        return focus
    now = time.time()
    first = next((c for c in craft_candidates(bag, slot3, settings, fallback=False) if off.get(c[0], 0) <= now), None)
    if first is not None and len(first[2]) == 3 and not slot3:
        return "slot3"
    if first is None or first[1] != SMASH:
        return "items"  # (an assemble that can't go is one waiting on what it's made from)
    label, mode, items = first[:3]
    spare = usable(bag, settings)
    if not spare.smash_hammers():
        return "hammers_off" if spare.hammers() else "hammer"
    picks = focus_picks(bag, slot3, settings)
    beetle, hammer = picks["sacrifice"].get(label), picks["hammer"].get(label)
    sacrifice = pick_sacrifice(spare, label, mode, items, off.get(PURPLE_OFF, 0) <= now, beetle, settings.get("sacrifice"))
    if sacrifice is None:
        return "picked_beetle" if beetle else "beetles"
    if pick_hammer(spare, mode, items, slot3, settings, sacrifice=sacrifice, picked=hammer) is not None:
        return "fresh"
    return "picked_hammer" if hammer else "worn"


def describe_craft(bag, mode, items, hammer, sacrifice=SACRIFICE, notes=()):
    """What a craft does, in words: ("Smash Pond + Junk Cube", " (sacrificing a Green Beetle; ...)"); a smash's notes go
    in the brackets too."""
    what = f"{'Assemble' if mode == ASSEMBLE else 'Smash'} {' + '.join(bag.name(k) for k in items)}"
    how = f" ({'; '.join([f'sacrificing a {bag.name(sacrifice)}', f'{bag.name(hammer)} at {bag.hammers()[hammer][1]}% break chance', *notes])})" if hammer else ""
    return what, how


def log_screen(bag, cards, mode, items, hammer, body, sacrifice=SACRIFICE):
    """Writes what RemiliaNET's craft screen says about the craft just made, as its own lines under the craft's (the
    dashboard shows them with it; see says.py). The hammer's new break chance is only in the game's answer, so it's here."""
    made, user = body.get("result") if isinstance(body.get("result"), dict) else {}, body.get("user")
    stat = Bag(user, cards).hammer_stats().get(hammer) if isinstance(user, dict) and hammer else None
    lines = craft_says(body.get("message"), mode == ASSEMBLE, [bag.name(k) for k in items], bag.name(sacrifice), made.get("beetle_name") or "",
                       bool(body.get("hammer_broke")), (stat.get("break_rate") or 0) if stat else None)
    for line in lines:
        log(f"{SCREEN}{line}")


def crafting_now(doing, what=None, side=None, until=None):
    """Tells the craft screen's little status screen what crafting is doing this moment: "next" (counting down to what,
    until then), "crafting" (what) or "done" (the run is over). side: the switch it's for, not the task (see side_craft)."""
    write_json(CRAFT_NOW_FILE, {"at": time.time(), "doing": doing, "what": what, "side": side, "until": until})


def count_down(site, what, side, saved):
    """The CRAFT_GAP seconds before a craft, shown counting down. False if anything was saved on the dashboard meanwhile
    (a pause, say; saved: overrides.json as the run began), and then the craft doesn't go."""
    crafting_now("next", what, side, time.time() + CRAFT_GAP)
    for _ in range(CRAFT_GAP):
        if stamp(OVERRIDES_FILE) != saved:
            return False
        site.wait(1)
    return stamp(OVERRIDES_FILE) == saved


def stop_run(state):
    """A run ended by something saved on the dashboard: crafting looks again straight away, with what's picked now."""
    crafting_now("done")
    state["craft_check_at"] = 0
    write_json(STATE_FILE, state)


def beetlecraft(site, state, dry_run, settings):
    """Crafts toward the Auto make or quest picked on the dashboard (see craft_candidates), up to CRAFTS_PER_RUN times in
    a row, each after a CRAFT_GAP countdown (see count_down), and logs each one. With neither picked it only reads the
    game, for the dashboard. Anything saved on the dashboard meanwhile (a pause, say) ends the run before the next craft,
    so it starts again with what's picked now."""
    saved = stamp(OVERRIDES_FILE)  # (the settings were read just before)
    picks = [settings.get("goal"), settings.get("goal_again"), settings.get("goal_set_at"), settings.get("goal_paused"), settings.get("junk_cubes"), settings.get("tin_hammer"),
             (settings.get("make") or {}).get("set_at"), (settings.get("make") or {}).get("paused"), settings.get("sacrifice"),
             settings.get("hammer_order"), settings.get("hammer_limits"), settings.get("hammer_off"), settings.get("steps")]
    picked = state.get("craft_picks") != picks  # a quest, Auto make, pause or hammer setting was picked on the dashboard: look now
    state["craft_picks"] = picks
    if not dry_run and not picked and time.time() < state.get("craft_check_at", 0):
        return
    status, game = site.api("GET", "/api/beetle/user")
    cards_status, cards = site.api("GET", "/api/beetle/cards")
    if status != 200 or cards_status != 200 or not isinstance(game, dict) or not isinstance(cards, dict):
        log(f"BeetleCraft: couldn't load the game (HTTP {status}/{cards_status}).")
        state["craft_check_at"] = time.time() + BEETLE_RETRY
        return
    save_game(game, cards, outside=True)
    book = recipe_book()[0]
    learn_from_log(Bag(game, cards))
    off = state.setdefault("craft_off", {})  # recipe label -> until when it's switched off
    for label in [k for k, until in off.items() if until - time.time() > 365 * 86400 and recipe_named(k)]:
        remove_recipe(label)  # switched off for good before such recipes were removed
        log(f"BeetleCraft: removed a recipe the game said doesn't exist ({label}).")
    crafted, next_check, shown = 0, CRAFT_CHECK, False
    while crafted < CRAFTS_PER_RUN:
        bag, slot3 = Bag(game, cards), bool(game.get("crafting_slot3_unlocked"))
        if not slot3 and usable(bag, settings).count(KEY) and craft_focus(bag, slot3, settings, book)[0] in ("make", "goal"):  # only for a task
            if dry_run:
                print("BeetleCraft: would unlock crafting slot 3 with the BeetleBoy Key.")
                return
            shown = True
            if not count_down(site, "Unlock crafting slot 3", None, saved):
                stop_run(state)
                return
            crafting_now("crafting", "Unlock crafting slot 3")
            _, after, error = beetle_action(site, "unlockCraftingSlot3")
            if error:
                log(f"BeetleCraft: couldn't unlock slot 3 ({error}).")
                next_check = BEETLE_RETRY
                break
            game = after or {**game, "crafting_slot3_unlocked": True}
            log("BeetleCraft: unlocked crafting slot 3 with the BeetleBoy Key.")
            crafted += 1
            continue
        step = next_craft(bag, slot3, settings, off)
        if not step:
            break
        label, mode, items, hammer, sacrifice, aim, source = step
        what, how = describe_craft(bag, mode, items, hammer, sacrifice)
        side = side_craft(settings, label)  # (an Auto Junk Cubes or Auto Tin Hammer craft, not the task's)
        task = None if side else task_id(craft_focus(bag, slot3, settings, book)[0], settings)
        if dry_run:
            print(f"BeetleCraft: next craft would be: {what}{how}.")
            return
        shown = True
        if not count_down(site, what, side, saved):  # paused, stopped or changed on the dashboard: not with what was picked before
            stop_run(state)
            return
        crafting_now("crafting", what, side)
        slots = items + [""] * (3 - len(items))
        payload = {"type": mode, "slot1": slots[0], "slot2": slots[1], "slot3": slots[2], "slot4": "",
                   "sacrifice": sacrifice, "hammer": hammer}
        status, body = site.signed_api("POST", "/api/beetle/action/craft", payload)
        body = body if isinstance(body, dict) else {}
        message, made = body.get("message"), body.get("result") if isinstance(body.get("result"), dict) else {}
        broke = " The hammer shattered!" if body.get("hammer_broke") else ""
        if broke:
            alert("hammer_broke", "A hammer broke", f"Your {bag.name(hammer)} shattered during a smash.")
        if message == "SUCCESS" and made.get("beetle"):
            cards[made["beetle"]] = made  # new discoveries count from now on
            log(f"BeetleCraft: {what} -> {made.get('beetle_name', made['beetle'])}{how}.{broke}")
            note_craft(state, task, label, what, made, label_of(aim, bag, book), source)
        elif message == "UNLUCKY_ROLL":
            aiming = describe_craft(bag, mode, items, hammer, sacrifice, [f"aiming for {label_of(aim, bag, book)}"])[1]
            log(f"BeetleCraft: {what}: smash failed{aiming}; the {bag.name(sacrifice)} is gone, the rest came back.{broke}")
            note_craft(state, task, label, what, None, label_of(aim, bag, book), source)
        elif message == "INVALID_RECIPE":
            off[label] = time.time() + FOR_GOOD
            remove_recipe(label, what)
            log(f"BeetleCraft: {what}: NOTHING HAPPENED, so this recipe doesn't exist; it's been removed from the recipe book.{broke}")
        elif message == "ALREADY_OWNED":
            off[label] = time.time() + 86400
            log(f"BeetleCraft: {what}: you can only own one of that; skipping it for a day.")
        else:
            log(f"BeetleCraft: {what} failed ({body.get('error') or message or f'HTTP {status}'}); trying again later.")
            if sacrifice == PURPLE and 400 <= status < 500 and status not in (401, 429):  # the game wouldn't take a Purple Beetle:
                off[PURPLE_OFF] = time.time() + 86400  # Green ones only for a day, so crafting doesn't stall on it
                log("BeetleCraft: Purple Beetles won't be sacrificed for a day; " + ("smashes wait meanwhile (Crafting Beetles is set to Purple)."
                    if settings.get("sacrifice") == PURPLE else "low-odds smashes use Green ones meanwhile."))
            next_check = BEETLE_RETRY
            break
        log_screen(bag, cards, mode, items, hammer, body, sacrifice)
        game = body["user"] if isinstance(body.get("user"), dict) else game
        save_game(game, cards)
        note_make_done(state, settings, Bag(game, cards), book)
        crafted += 1
        write_json(STATE_FILE, state)
    else:
        next_check = 0  # the run's limit was hit with more to do: carry on next time round
    if shown:
        crafting_now("done")
    bag, slot3 = Bag(game, cards), bool(game.get("crafting_slot3_unlocked"))
    if not dry_run:
        note_make_done(state, settings, bag, book)  # hunts and claims can bring it too
    goal = settings.get("goal")
    if goal and book and not goal_needs_left(bag, slot3, settings):
        done_key = f"{goal} {settings.get('goal_set_at')}"
        if state.get("goal_done") != done_key and not dry_run:
            log(f"BeetleCraft: the quest you picked ({goal}) is done. "
                + ("Next one from the queue." if read_json(OVERRIDES_FILE).get("goal_queue") else "Nothing more to craft until you pick another."))
            state["goal_done"] = done_key
            alert("quest_done", "Quest done", f"{quest_title(goal)} is done.")
    if not dry_run and book and advance_queues(bag, slot3, book):
        next_check = 0  # start on it straight away
    if dry_run:
        print("BeetleCraft: nothing to craft right now.")
        return
    state["craft_check_at"] = time.time() + next_check
    write_json(STATE_FILE, state)
