"""What the bot writes down for the dashboard: the game state, the history, pictures, notes and the streak warning."""

import sqlite3
import time
import urllib.request

import quests
import store
from remidex.core import (
    APP_DIR,
    DASH_DIR,
    GAME_FILE,
    ICONS_PER_RUN,
    LOG_FILE,
    PEOPLE_PICS,
    STATE_FILE,
    USER_AGENT,
    VIEWS_KEPT,
    as_dict,
    log,
    read_json,
    write_json,
)
from remidex.settings import alert
from remidex.recipes import note_outside_crafts


FEED_FILE = APP_DIR / "feed.json"  # written only by --watch-feed
LEVELS_FILE = DASH_DIR / "levels.json"  # the lowest and highest XP seen at each level, to learn how much XP a level takes
ART_RETRY = 24 * 3600  # a picture the site doesn't have is asked for again only this often
STREAK_WARN = 4 * 3600  # warn when today's daily cheese is still unclaimed this close to the end of the day (UTC)


def quest_title(quest_id):
    return next((q[2] for q in quests.QUESTS if q[1] == quest_id), quest_id)


def streak_watch(game):
    """Warns when today's daily cheese is still unclaimed late in the day (UTC, when the streak turns over), which would
    break the streak. Run after the bot has had its go at claiming it, so it only speaks up when the claim is left to
    you (switched to manual) or didn't go through."""
    if (game.get("cooldowns") or {}).get("claimUBC") and not game.get("isStreakAtRisk"):
        return  # already claimed today
    now = time.gmtime()
    left = 86400 - (now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec)
    try:
        streak = store.streak()
    except sqlite3.Error:
        streak = None
    if left > STREAK_WARN and not game.get("isStreakAtRisk"):
        return
    hours, minutes = divmod(left // 60, 60)
    alert("streak", "Your cheese streak is at risk", f"Today's daily cheese isn't claimed, and the day ends in {hours}h {minutes:02d}m (UTC)."
          + (f" Your {streak}-day streak would start over." if streak and streak > 1 else ""), once=f"streak {time.strftime('%Y-%m-%d', now)} {'last hour' if left < 3600 else 'evening'}")


def save_game(game, cards=None, outside=False):
    """Keeps the latest BeetleBoy state (and item cards, when known) for the dashboard, and notes level-ups.
    outside: what changed since the last one wasn't done by the bot (it's a fresh read of the game), so crafts you made
    on RemiliaNET yourself are worked out from it (see note_outside_crafts)."""
    if not isinstance(game, dict):
        return
    saved = read_json(GAME_FILE)
    if outside and isinstance(saved.get("game"), dict):
        try:
            note_outside_crafts(saved["game"], game, {**(saved.get("cards") or {}), **(cards if isinstance(cards, dict) else {})})
        except Exception as e:  # a guess must never get in the way of saving the game
            log(f"BeetleCraft: couldn't work out what you made on RemiliaNET ({e}).")
    write_json(GAME_FILE, {"game": game, "fetched_at": time.time(), "cards": cards if isinstance(cards, dict) else saved.get("cards", {})})
    level, xp, before = game.get("level"), game.get("xp"), (saved.get("game") or {}).get("level")
    if not (isinstance(level, int) and isinstance(xp, int)):
        return
    if isinstance(before, int) and level > before:
        log(f"Level up! You're now level {level}.")
        alert("level_up", f"Level {level}!", f"You're now level {level} in BeetleBoy.")
    note_level(game)


def note_level(game):
    """Notes the XP seen at this level. The game only reports your level and total XP, not how much a level takes; the
    XP seen either side of each level-up tells the dashboard's EXP bar, so the closer together those two looks are (the
    bot looks after every action, not just once a run), the better it knows where a level began."""
    level, xp = game.get("level"), game.get("xp")
    if not (isinstance(level, int) and isinstance(xp, int)):
        return
    seen = read_json(LEVELS_FILE).get("seen", {})
    low, high = seen.get(str(level), [xp, xp])
    if [min(low, xp), max(high, xp)] != seen.get(str(level)):
        seen[str(level)] = [min(low, xp), max(high, xp)]
        write_json(LEVELS_FILE, {"seen": seen})


_HISTORY = {"at": 0}


def keep_history():
    """Reads what's new in the log into the history (see store.ingest_log), at most every 30 seconds, and keeps the log
    from growing for ever (see store.trim_log)."""
    if time.time() - _HISTORY["at"] < 30:
        return
    _HISTORY["at"] = time.time()
    try:
        store.ingest_log(LOG_FILE)
        store.trim_log(LOG_FILE)
    except (sqlite3.Error, OSError) as e:
        log(f"History: couldn't read the log ({e}).")


def move_to_store():
    """The first time after an update: brings who was viewed and who the activity feed showed into the history database,
    out of the state file and feed.json, where they were rewritten all the time."""
    state = read_json(STATE_FILE)
    try:
        if store.migrate(state, read_json(FEED_FILE).get("seen")):
            state.pop("viewed_at", None)
            write_json(STATE_FILE, state)
            FEED_FILE.unlink(missing_ok=True)
            log("History: moved who you've viewed and the activity feed into the history database.")
    except sqlite3.Error as e:
        log(f"History: couldn't move them ({e}).")


def cache_for_dashboard(site):
    """Saves what the dashboard shows but can't fetch itself, since it needs your sign-in: your theme and profile
    picture, and the icon and card art of each item you've found. Only fetches what's new or missing."""
    look = getattr(site, "look", None)
    if not look:
        return
    DASH_DIR.mkdir(parents=True, exist_ok=True)
    saved = read_json(DASH_DIR / "look.json")
    if look.get("pfpUrl") and (look["pfpUrl"] != saved.get("pfpUrl") or not (DASH_DIR / "pfp").exists()):
        picture = site.download(look["pfpUrl"])
        if picture:
            (DASH_DIR / "pfp").write_bytes(picture)
    if look != saved:
        write_json(DASH_DIR / "look.json", look)

    snapshot = read_json(GAME_FILE)
    icons, art = DASH_DIR / "icons", DASH_DIR / "cards"
    icons.mkdir(exist_ok=True)
    art.mkdir(exist_ok=True)
    missing = read_json(DASH_DIR / "missing.json")  # pictures the site didn't have: {path: when it last said so}
    fetched, before = 0, dict(missing)
    for key, card in (snapshot.get("cards") or {}).items():
        if not key.replace("_", "").isalnum():
            continue
        # The icon, and for its card: the character art and the scene behind it.
        for field, local in (("icon", icons / f"{key}.png"), ("character", art / f"{key}.char"), ("background", art / f"{key}.bg")):
            path = (card or {}).get(field)
            if (fetched >= ICONS_PER_RUN or not isinstance(path, str) or not path.startswith("/") or local.exists()
                    or time.time() - missing.get(path, 0) < ART_RETRY):
                continue
            picture = site.download(path)
            fetched += 1
            if picture:
                local.write_bytes(picture)
                missing.pop(path, None)
            else:
                missing[path] = time.time()
    if missing != before:
        write_json(DASH_DIR / "missing.json", missing)


def save_picture(site, handle, url):
    """Keeps someone's profile picture for the dashboard, once. Returns whether it had to try downloading it."""
    if not isinstance(handle, str) or not handle.replace("_", "").isalnum() or not isinstance(url, str):
        return False
    local = PEOPLE_PICS / handle
    if local.exists():
        return False
    if url.startswith("/"):
        picture = site.download(url)
    elif url.startswith("https://pfp.remilia.net/"):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as r:
                picture = r.read()
        except OSError:
            picture = None
    else:
        return False
    if picture:
        PEOPLE_PICS.mkdir(parents=True, exist_ok=True)
        local.write_bytes(picture)
    return True


def read_notes(site, kind, saved):
    """Adds one kind of notification to saved (by id), reading back far enough to fill the list the first time and
    just the newest page after that. Returns whether the site answered. (The site doesn't filter every kind: asked
    for friend_accept it sends all kinds, so each notification's own type is checked.)"""
    first = not any(v.get("type", "page_view") == kind for v in saved.values())
    for page in range(1, (VIEWS_KEPT // 50 if first else 1) + 1):
        status, body = site.api("GET", f"/api/notifications?type={kind}&page={page}&limit=50")
        if status != 200 or not isinstance(body, dict):
            return page > 1
        for note in body.get("notifications") or []:
            if not isinstance(note, dict) or note.get("type") != kind:
                continue
            content = as_dict(note.get("content"))
            person, note_id = as_dict(content.get("from")), note.get("_id")
            handle = person.get("userHandle")
            if not isinstance(handle, str) or not handle or not note_id:
                continue
            count = content.get("count") or 1
            started, touched = (note.get("createdAt") or 0) / 1000, min((note.get("updatedAt") or 0) / 1000, time.time())
            old = saved.get(note_id)
            if old and old["count"] == count:
                at = old["at"]  # nothing new, even if reading it on the site touched it
            elif old or note.get("state") != "read":
                at = max(touched, started)  # something new: the last of it is when the notification last changed
            else:
                at = started  # first seen, already read: when it started (reading it changed the last-touched time)
            saved[note_id] = {"id": note_id, "type": kind, "handle": handle, "name": person.get("displayName") or handle,
                              "count": count, "at": at, "state": note.get("state"), "mutual": content.get("mutualFriends"),
                              "pfpUrl": person.get("pfpUrl")}
        if not as_dict(body.get("pagination")).get("hasMore"):
            break
    return True
