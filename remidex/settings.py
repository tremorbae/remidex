"""The dashboard's switches and picks (saved in overrides.json), and the Mac alerts."""

import time

from remidex.core import HAMMERS, OVERRIDES_FILE, notify, read_json, stamp


SOCIAL_SETTINGS = ("pokes", "friend_requests", "view_profiles")  # the dashboard's switches for what reaches other people
# BeetleBoy: the free claims whenever they're ready, then hunts with whatever cheese there is (each has its switch on the
# dashboard). Actions are named as the website names them.
BEETLE_SETTINGS = ("catchBeetle", "claimUBC", "junkFaucet", "beetleHunt")
POKE_GAP = (60, 120)  # seconds from one poke to the next: a random wait between these
HAMMER_LIMIT = 25  # a hammer smashes while its break chance is under this, unless its own limit is set on the dashboard
# What's worth a Mac notification (a hammer breaking isn't: the log and the dashboard say so).
ALERT_KINDS = {"signed_out": True, "streak": True, "quest_done": True, "make_done": True, "level_up": True, "achievement": True, "hammer_broke": False}
_ALERTED = {}


def alert(kind, title, message, once=None):
    """A Mac notification for something worth knowing right away (see ALERT_KINDS). The same thing isn't announced twice
    within six hours."""
    if not ALERT_KINDS.get(kind):
        return
    key, now = (kind, once or message), time.time()
    if now - _ALERTED.get(key, 0) < 6 * 3600:
        return
    _ALERTED[key] = now
    notify(message, title)


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def valid_make(make):
    """An Auto make picked on the dashboard: {"keys": what to make, "recipe": which recipe or None, "set_at", "start",
    "paused": whether it's on hold}."""
    return (isinstance(make, dict) and isinstance(make.get("keys"), list) and bool(make["keys"])
            and all(isinstance(k, str) for k in make["keys"]) and (make.get("recipe") is None or type(make.get("recipe")) is int)
            and is_number(make.get("start")) and is_number(make.get("set_at")) and isinstance(make.get("paused", False), bool))


def valid_skipped(skipped):
    """Steps skipped on the dashboard, for one Auto make or quest: {"task": its id (see task_id), "labels": [recipe labels]},
    or None."""
    if not (isinstance(skipped, dict) and isinstance(skipped.get("task"), str) and isinstance(skipped.get("labels"), list)):
        return None
    return {"task": skipped["task"], "labels": [label for label in skipped["labels"] if isinstance(label, str)]}


def load_settings():
    """How the bot runs: what was picked on the dashboard (overrides.json). A switch nobody has flipped yet is on, except
    junk cubes (a first sign-in turns them all off: see browser.first_sign_in_defaults)."""
    overrides = read_json(OVERRIDES_FILE)
    flipped = {**(overrides.get("beetleboy") or {}), **(overrides.get("social") or {}), **(overrides.get("crafting") or {})}

    def switch(action, default=True):
        return flipped[action] if isinstance(flipped.get(action), bool) else default

    picked = overrides.get("hammers") if isinstance(overrides.get("hammers"), dict) else {}
    order = [h for h in picked.get("order") or [] if h in HAMMERS]
    limits = picked.get("limits") if isinstance(picked.get("limits"), dict) else {}
    return {
        "friends_first": False,  # everyone due, in random order, rather than friends first
        "gap": POKE_GAP,
        **{action: switch(action) for action in SOCIAL_SETTINGS},
        "beetleboy": {action: switch(action) for action in BEETLE_SETTINGS},
        "junk_cubes": switch("junk_cubes", False),
        "goal": overrides.get("goal") if isinstance(overrides.get("goal"), str) else None,
        "goal_again": overrides.get("goal_again") is True,
        "goal_set_at": overrides.get("goal_set_at"),
        "goal_paused": overrides.get("goal_paused") is True,
        "make": overrides["make"] if valid_make(overrides.get("make")) else None,
        "max_break_chance": HAMMER_LIMIT,
        "hammer_order": list(dict.fromkeys(order + HAMMERS)),
        "hammer_limits": {h: limits[h] if is_number(limits.get(h)) else HAMMER_LIMIT for h in HAMMERS},
        "hammer_timing": picked.get("timing") is not False,
        "hammer_off": [h for h in HAMMERS if h in (picked.get("off") if isinstance(picked.get("off"), list) else [])],  # never smashed with (still upgraded)
        "locked": sorted({k for k in overrides["locked"] if isinstance(k, str)}) if isinstance(overrides.get("locked"), list) else [],  # items switched off on the dashboard: the bot never uses them
        "skipped": valid_skipped(overrides.get("skipped")),  # steps skipped on the dashboard: done last
    }


_SETTINGS = {"key": None, "value": None}


def current_settings():
    """load_settings, but read again only when what the dashboard saved has changed."""
    key = stamp(OVERRIDES_FILE)
    if key != _SETTINGS["key"]:
        _SETTINGS.update(key=key, value=load_settings())
    return _SETTINGS["value"]
