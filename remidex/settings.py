"""The dashboard's switches and picks (saved in overrides.json)."""

from remidex.core import HAMMERS, OVERRIDES_FILE, PURPLE, SACRIFICE, read_json, stamp


SOCIAL_SETTINGS = ("pokes", "friend_requests", "view_profiles")  # the dashboard's switches for what reaches other people
# BeetleBoy: the free claims whenever they're ready, then hunts with whatever cheese there is (each has its switch on the
# dashboard). Actions are named as the website names them.
BEETLE_SETTINGS = ("catchBeetle", "claimUBC", "junkFaucet", "beetleHunt")
# The bot works in short rounds with its browser closed in between, so the server can run everyone's: a round pokes up
# to POKES_PER_SESSION people, POKE_GAP seconds apart, and then rests SESSION_REST seconds. (About as many pokes a day as
# one every 1–2 minutes, but with a browser open a few minutes in every half hour, not all day.)
POKE_GAP = (4, 10)  # seconds from one poke to the next within a round: a random wait between these
POKES_PER_SESSION = 20
SESSION_REST = (20 * 60, 30 * 60)  # legacy batch rest
LAYER_3 = False  # Layer 3 (batch limits and 20-30m rests) is removed: continuous poking for all due users
HAMMER_LIMIT = 25  # a hammer smashes while its break chance is under this, unless its own limit is set on the dashboard
def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def valid_make(make):
    """An Auto make picked on the dashboard: {"keys": what to make, "recipe": which recipe or None, "set_at", "start",
    "paused": whether it's on hold}."""
    return (isinstance(make, dict) and isinstance(make.get("keys"), list) and bool(make["keys"])
            and all(isinstance(k, str) for k in make["keys"]) and (make.get("recipe") is None or type(make.get("recipe")) is int)
            and is_number(make.get("start")) and is_number(make.get("set_at")) and isinstance(make.get("paused", False), bool))


def valid_steps(steps):
    """What's picked on the dashboard for the steps of one Auto make or quest, by recipe label: {"task": its id (see
    task_id), "skipped": [labels done last], "first": the label worked on first or None, "sacrifice": {label: the beetle
    it sacrifices}, "hammer": {label: the hammer it uses}}, or None."""
    if not (isinstance(steps, dict) and isinstance(steps.get("task"), str)):
        return None
    picked = lambda key, allowed: {k: v for k, v in (steps[key] if isinstance(steps.get(key), dict) else {}).items() if v in allowed}  # noqa: E731
    return {"task": steps["task"], "skipped": [k for k in steps["skipped"] if isinstance(k, str)] if isinstance(steps.get("skipped"), list) else [],
            "first": steps["first"] if isinstance(steps.get("first"), str) else None,
            "sacrifice": picked("sacrifice", (SACRIFICE, PURPLE)), "hammer": picked("hammer", HAMMERS)}


def load_settings(path=None):
    """How the bot runs: what was picked on the dashboard (overrides.json, or path: another bot's). A switch nobody has
    flipped yet is on, except junk cubes (a first sign-in turns them all off: see browser.first_sign_in_defaults)."""
    overrides = read_json(path or OVERRIDES_FILE)
    flipped = {**(overrides.get("beetleboy") or {}), **(overrides.get("social") or {}), **(overrides.get("crafting") or {})}

    def switch(action, default=True):
        return flipped[action] if isinstance(flipped.get(action), bool) else default

    picked = overrides.get("hammers") if isinstance(overrides.get("hammers"), dict) else {}
    order = [h for h in picked.get("order") or [] if h in HAMMERS]
    limits = picked.get("limits") if isinstance(picked.get("limits"), dict) else {}
    layer3 = overrides.get("layer3", LAYER_3)
    return {
        "friends_first": False,  # everyone due, in random order, rather than friends first
        "layer3": layer3,
        "gap": POKE_GAP,
        "batch": POKES_PER_SESSION if layer3 else 0,
        "rest": SESSION_REST if layer3 else (30, 60),
        **{action: switch(action) for action in SOCIAL_SETTINGS},
        "beetleboy": {action: switch(action) for action in BEETLE_SETTINGS},
        "junk_cubes": switch("junk_cubes", False),
        "tin_hammer": switch("tin_hammer"),  # Auto Tin Hammer: the bot makes one when you have none (see tin_hammer_crafts), and plans may forge one to smash with
        "goal": overrides.get("goal") if isinstance(overrides.get("goal"), str) else None,
        "goal_again": overrides.get("goal_again") is True,
        "goal_set_at": overrides.get("goal_set_at"),
        "goal_paused": overrides.get("goal_paused") is True,
        "make": overrides["make"] if valid_make(overrides.get("make")) else None,
        "sacrifice": overrides["sacrifice"] if overrides.get("sacrifice") in (SACRIFICE, PURPLE) else "auto",  # Crafting Beetles in Analytics: what every smash sacrifices (see pick_sacrifice)
        "max_break_chance": HAMMER_LIMIT,
        "hammer_order": list(dict.fromkeys(order + HAMMERS)),
        "hammer_limits": {h: limits[h] if is_number(limits.get(h)) else HAMMER_LIMIT for h in HAMMERS},
        "hammer_timing": picked.get("timing") is not False,
        "hammer_off": [h for h in HAMMERS if h in (picked.get("off") if isinstance(picked.get("off"), list) else [])],  # never smashed with (still upgraded)
        "locked": sorted({k for k in overrides["locked"] if isinstance(k, str)}) if isinstance(overrides.get("locked"), list) else [],  # items switched off on the dashboard: the bot never uses them
        "steps": valid_steps(overrides.get("steps")),  # what's picked for the plan's steps on the dashboard (see step_picks)
    }


_SETTINGS = {"key": None, "value": None}


def current_settings():
    """load_settings, but read again only when what the dashboard saved has changed."""
    key = stamp(OVERRIDES_FILE)
    if key != _SETTINGS["key"] or _SETTINGS["value"] is None:  # (None: not read yet, which no file at all would look like)
        _SETTINGS.update(key=key, value=load_settings())
    return _SETTINGS["value"]
