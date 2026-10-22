"""Pokes, friend requests, the activity feed, and looking at profiles."""

import collections
import json
import random
import socket
import sqlite3
import time
import urllib.parse
import urllib.request

import store
from remidex.core import (
    APP_DIR,
    DASH_DIR,
    DELAY,
    FEED_US_FILE,
    ICONS_PER_RUN,
    PEOPLE_PICS,
    SITE,
    STATE_FILE,
    USER_AGENT,
    VIEWS_KEPT,
    as_dict,
    editing,
    http_error,
    log,
    read_json,
    someone_looking,
    today,
    write_json,
)
from remidex.settings import alert
from remidex.state import move_to_store, read_notes, save_picture


FEED_URL = SITE + "/api/activityFeed"
EVERYONE_FILE = APP_DIR / "everyone.json"  # everyone discovered so far, and who has been crawled
PROFILE_EDIT_FILE = APP_DIR / "profile_edit.json"  # a profile change asked for on the dashboard, and how making it went
MY_ACHIEVEMENTS_FILE = DASH_DIR / "remilia_achievements.json"  # your RemiliaNET achievements, as your profile shows them
MY_PROFILE_FILE = DASH_DIR / "profile.json"  # your RemiliaNET bio and top 5 friends, for the dashboard's profile card
ACHIEVEMENT_LIST_FILE = DASH_DIR / "achievement_catalog.json"  # every achievement seen on anyone's profile: the site has no list
VIEWS_CHECK = 5 * 60  # how often to fetch who viewed your profile, for the dashboard
# Notifications kept for the dashboard's activity, besides page views: who poked you, sent you a friend request or accepted yours.
SOCIAL_NOTES = ("poke", "friend_request", "friend_accept")
# The dashboard's all-time counts, from notifications: (file, which kind, the history's counter).
COUNTED = (("views.json", "page_view", "viewed_you"), ("social.json", "poke", "pokes_received"))
COOLDOWN = 24 * 3600
FRIENDS_REFRESH = 3600  # re-fetch the friend list at least this often, to pick up new friends (and see who accepted a request)
FAILED_RETRY = 6 * 3600
UNKNOWN_COOLDOWN_RETRY = 3600
RATE_LIMIT_BACKOFF = 10
MAX_RETRIES = 3
FEED_IDLE_TIMEOUT = 300  # reconnect if the feed sends nothing for this long
FEED_RECONNECT = 10
# One poke at a time, around the clock: friends whose cooldown is up first, then everyone else.
# How far apart they are: see settings.POKE_GAP.
MAX_TRIES_PER_SLOT = 5  # people found to be on cooldown don't use up a slot; try this many in a row at most
DISCOVER_BELOW = 50  # look for more people when fewer than this many non-friends are due
CRAWL_PAGES_PER_RUN = 20  # friend-list pages read per look, to find more people
CRAWL_MAX_PAGES_PER_USER = 4  # first 200 friends of each person is plenty to keep discovering
ACCEPT_PER_RUN = 20  # friend requests accepted per look (see accept_requests)
ASK_AGAIN = 24 * 3600  # with pokes on manual, friend requests go out on their own: look at each person once a day at most
REMILIA_FEED_FILE = DASH_DIR / "remilia_activity.json"  # RemiliaNET's newest activity, for the dashboard (while it's open)
FEED_SHOWN = 60  # events kept for it
FEED_SHOW_EVERY = 2  # seconds: saved for it this often at most
VIEWS_LOGGED_EVERY = 3600  # the bot's own profile views go in the log as one line this often at most (they were most of it)
_VIEWS = {"n": 0, "since": 0.0}


def cache_profile_views(site, state):
    """Saves, for the dashboard, who has viewed your profile (views.json), and who has poked you, sent you a friend
    request or accepted yours (social.json), from your notifications. Only reads them: nothing is marked as read.
    Also keeps each person's profile picture, and your RemiliaNET achievements, bio and top 5 friends.

    The site starts a new notification when someone comes back later (repeat views soon after just raise its count),
    and reading your notifications on the site touches every one of them. So each notification is kept by its id,
    timed when it started, or when the bot saw its count go up, never by when it was read."""
    if time.time() < state.get("views_check_at", 0):
        return
    state["views_check_at"] = time.time() + VIEWS_CHECK
    fetched = 0
    for kinds, file in ((("page_view",), "views.json"), (SOCIAL_NOTES, "social.json")):
        saved = {v["id"]: v for v in read_json(DASH_DIR / file).get("notes", []) if isinstance(v, dict) and v.get("id")}
        if not all([read_notes(site, kind, saved) for kind in kinds]) and not saved:
            continue
        for counted_file, kind, counter in COUNTED:  # views of your profile and pokes you've received, all time
            if file == counted_file:
                try:
                    store.record_views([(f"{kind} {v['id']}" if kind == "poke" else v["id"], int(v.get("count") or 1), v.get("at"))
                                        for v in saved.values() if v.get("type", "page_view") == kind], counter)
                except sqlite3.Error as e:
                    log(f"History: couldn't count {counter.replace('_', ' ')} ({e}).")
        newest = []
        for kind in kinds:  # the newest of each kind
            newest += sorted((v for v in saved.values() if v.get("type", "page_view") == kind), key=lambda v: -v["at"])[:VIEWS_KEPT]
        for note in sorted(newest, key=lambda v: -v["at"]):
            if fetched >= ICONS_PER_RUN:
                break
            fetched += save_picture(site, note["handle"], note.get("pfpUrl"))
        write_json(DASH_DIR / file, {"notes": newest, "fetched_at": time.time()})
    status, profile = look_up(site, state["handle"]) if state.get("handle") else (0, None)
    user = (profile or {}).get("user") if status == 200 else None
    if isinstance(user, dict):  # your own achievements, and which you picked to show on your profile, in that order
        before = {a.get("id") for a in read_json(MY_ACHIEVEMENTS_FILE).get("all") or [] if isinstance(a, dict)} if MY_ACHIEVEMENTS_FILE.exists() else None
        for a in user.get("allAchievements") or []:
            if before is not None and isinstance(a, dict) and a.get("id") not in before:  # new since last time (not the first time it looks)
                alert("achievement", "Achievement unlocked", f"{a.get('title')}: {a.get('description')}", once=f"achievement {a.get('id')}")
        write_json(MY_ACHIEVEMENTS_FILE, {"all": user.get("allAchievements") or [], "shown": [as_dict(a).get("id") for a in user.get("achievementsDisplayed") or []],
                                          "friends": user.get("friendCount"), "fetched_at": time.time()})
        top = [f for f in user.get("friendsDisplayed") or [] if isinstance(f, dict) and f.get("userHandle")][:5]
        write_json(MY_PROFILE_FILE, {"bio": user.get("bio") or "", "name": user.get("displayName"), "fetched_at": time.time(),
                                     "top_friends": [{"handle": f["userHandle"], "name": f.get("displayName")} for f in top]})
        for f in top:
            save_picture(site, f["userHandle"], f.get("pfpUrl"))
    save_achievement_list()


def friends_page(body):
    """One page of someone's friend list, as the site answers it: (their handles, whether there's another page).
    Entries of another shape are skipped."""
    body = as_dict(body)
    handles = [as_dict(as_dict(f).get("user")).get("userHandle") for f in body.get("friends") or [] if isinstance(f, dict)]
    return [h for h in handles if isinstance(h, str) and h], bool(as_dict(body.get("pagination")).get("hasMore"))


def fetch_friends(site, handle):
    friends = []
    for page in range(1, 1000):
        query = urllib.parse.urlencode({"username": handle, "page": page, "limit": 50})
        status, body = site.api("GET", f"/api/profile/friends?{query}")
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"could not load friends page {page} (HTTP {status})")
        handles, more = friends_page(body)
        friends += handles
        if not more:
            return friends
    raise RuntimeError("friend list never ended")


def poke(site, friend, next_poke_at):
    """Returns "poked", "cooldown" or "failed", and records when the friend can next be poked."""
    quoted = urllib.parse.quote(friend, safe="")
    for attempt in range(MAX_RETRIES + 1):
        status, body = site.api("POST", f"/api/users/{quoted}/poke")
        if 200 <= status < 300:
            next_poke_at[friend] = time.time() + COOLDOWN + 5
            return "poked"

        if status == 401 and attempt < MAX_RETRIES:
            site.restore_sign_in()
            continue
        if status == 429:
            # Either this friend's 24h cooldown or a general rate limit; their profile tells which, and how long is left.
            profile_status, profile = look_up(site, friend)
            viewer = as_dict(as_dict(profile).get("viewerContext"))
            if profile_status == 200 and viewer.get("canPoke") is False:
                remaining = viewer.get("pokeCooldownSeconds") or 0
                next_poke_at[friend] = time.time() + (min(remaining, COOLDOWN) + 5 if remaining > 0 else UNKNOWN_COOLDOWN_RETRY)
                return "cooldown"
            if attempt < MAX_RETRIES:
                site.wait(RATE_LIMIT_BACKOFF)
                continue

        log(f"~{friend}: failed with {http_error(status, body)}")
        next_poke_at[friend] = time.time() + FAILED_RETRY
        return "failed"


def look_up(site, handle):
    """Their profile: (HTTP status, body, or None if the answer isn't a profile). Notes the achievements on it (see
    note_achievements)."""
    status, profile = site.api("GET", f"/api/profile/~{urllib.parse.quote(handle, safe='')}")
    profile = profile if isinstance(profile, dict) else None
    if status == 200:
        note_achievements(profile)
    return status, profile


_ACHIEVEMENTS = {"list": None, "changed": False}


def note_achievements(profile):
    """Adds the achievements on a profile to the list of every achievement seen (RemiliaNET publishes no list of them),
    for the dashboard to show which you don't have yet. Saved by save_achievement_list."""
    if _ACHIEVEMENTS["list"] is None:
        _ACHIEVEMENTS["list"] = read_json(ACHIEVEMENT_LIST_FILE).get("achievements", {})
    user = profile.get("user") if isinstance(profile, dict) else None
    for a in (user.get("allAchievements") if isinstance(user, dict) else None) or []:
        if isinstance(a, dict) and isinstance(a.get("id"), int):
            entry = {k: a.get(k) for k in ("id", "title", "description", "grantMessage", "season", "ownershipPercentage", "type")}
            if _ACHIEVEMENTS["list"].get(str(a["id"])) != entry:
                _ACHIEVEMENTS["list"][str(a["id"])] = entry
                _ACHIEVEMENTS["changed"] = True


def save_achievement_list():
    if _ACHIEVEMENTS["changed"]:
        write_json(ACHIEVEMENT_LIST_FILE, {"achievements": _ACHIEVEMENTS["list"]})
        _ACHIEVEMENTS["changed"] = False


def profile_edit_waiting():
    edit = read_json(PROFILE_EDIT_FILE)
    return bool(edit) and not edit.get("done_at")


def apply_profile_edit(site, state):
    """Makes the profile change asked for on the dashboard (display name, bio, top 5 friends, achievements shown) on RemiliaNET, the way
    the site's own edit forms do: your whole profile as it is now, with just those parts changed, so the rest (your
    picture, achievements shown, location) stays as it is. Notes how it went, for the dashboard."""
    edit = read_json(PROFILE_EDIT_FILE)
    if not edit or edit.get("done_at"):
        return
    ok, status, body, what = True, 200, {}, []
    if any(k in edit for k in ("displayName", "bio", "friendsDisplayed", "achievementsDisplayed")):
        what.append("Profile")
        status, profile = look_up(site, state["handle"]) if state.get("handle") else (0, None)
        user = (profile or {}).get("user") if status == 200 else None
        if isinstance(user, dict):
            payload = {"pfp": user.get("pfp"), "bio": user.get("bio"), "displayName": user.get("displayName"), "userHandle": user.get("username"),
                       "achievementsDisplayed": [as_dict(a).get("id") for a in user.get("achievementsDisplayed") or []],
                       "friendsDisplayed": [as_dict(f).get("userHandle") for f in user.get("friendsDisplayed") or []], "location": user.get("location")}
            payload.update({k: edit[k] for k in ("displayName", "bio", "friendsDisplayed", "achievementsDisplayed") if k in edit})
            status, body = site.signed_api("POST", "/api/profile/updateProfile", payload)
        else:
            body = profile
        ok = user is not None and 200 <= status < 300
    if "shelves" in edit and ok:  # the trophy shelf has its own request on the site
        what.append("Shelf")
        status, body = site.signed_api("POST", "/api/profile/updateProfile/shelf", {"shelves": edit["shelves"]})
        ok = 200 <= status < 300
    said = body if isinstance(body, dict) else {}  # RemiliaNET says why it refused: {"field": "pfp", "message": "you do not own this profile picture"}
    with editing(PROFILE_EDIT_FILE):
        if read_json(PROFILE_EDIT_FILE).get("asked_at") == edit.get("asked_at"):  # (a newer change asked for meanwhile is next, not done)
            write_json(PROFILE_EDIT_FILE, {**edit, "done_at": time.time(), "ok": ok, "error": None if ok else said.get("message") or http_error(status, body),
                                           "field": None if ok else said.get("field")})
    log(f"{what[-1]}: saved your changes on RemiliaNET." if ok else f"{what[-1]}: couldn't save your changes on RemiliaNET ({http_error(status, body)}).")
    if ok:
        state["views_check_at"] = 0  # and save the new profile for the dashboard straight away


def remember_picture(site, handle, looked=None):
    """Keeps the profile picture of someone just poked or asked, for the dashboard (looked: their profile, if fetched)."""
    if (PEOPLE_PICS / handle).exists() or not handle.replace("_", "").isalnum():
        return
    status, profile = looked or look_up(site, handle)
    if status == 200 and isinstance(profile, dict):
        user = profile.get("user")
        save_picture(site, handle, user.get("pfpUrl") if isinstance(user, dict) else None)


def accept_requests(site, state):
    """Accepts the friend requests people have sent you (with Auto Add Friend on), up to ACCEPT_PER_RUN at a time,
    and keeps their profile pictures for the dashboard. Looks every VIEWS_CHECK."""
    if time.time() < state.get("accept_check_at", 0):
        return
    state["accept_check_at"] = time.time() + VIEWS_CHECK
    status, body = site.api("GET", "/api/profile/friends/incoming?page=1&limit=50")
    if status != 200 or not isinstance(body, dict):
        return
    for entry in (body.get("friends") or [])[:ACCEPT_PER_RUN]:
        person = entry.get("user") if isinstance(entry, dict) else None
        handle = person.get("userHandle") if isinstance(person, dict) else None
        if not isinstance(handle, str) or not handle:
            continue
        status, answer = site.api("POST", "/api/friendship/accept", {"username": handle})
        if 200 <= status < 300:
            state.setdefault("friend_requested", {}).pop(handle, None)
            log(f"~{handle} (friend): accepted their friend request.")
            save_picture(site, handle, person.get("pfpUrl"))
        else:
            log(f"~{handle} (other): couldn't accept their friend request ({http_error(status, answer)}).")
        site.wait(DELAY)
    write_json(STATE_FILE, state)


def befriend(site, handle, state, looked=None):
    """After poking someone who isn't a friend: sends them a friend request, or accepts theirs. Returns what happened.
    looked: their profile, if it was just fetched.

    Each request sent is remembered. If that person later shows as neither a friend nor pending, they declined,
    and they're never asked again."""
    requested = state.setdefault("friend_requested", {})
    declined = state.setdefault("friend_declined", {})
    if handle in declined:
        return "declined a friend request before, not asking again"
    status, profile = looked or look_up(site, handle)
    viewer = as_dict(as_dict(profile).get("viewerContext"))
    if status != 200:
        return f"couldn't check friendship (HTTP {status})"
    if viewer.get("areFriends"):
        requested.pop(handle, None)
        return "already friends"
    if viewer.get("pendingRequestTo"):
        return "friend request still pending"
    if viewer.get("pendingRequestFrom"):
        status, body = site.api("POST", "/api/friendship/accept", {"username": handle})
        if 200 <= status < 300:
            return "accepted their friend request"
        return f"couldn't accept their friend request ({http_error(status, body)})"
    if handle in requested:
        declined[handle] = time.time()
        del requested[handle]
        return "declined our friend request, won't ask again"
    status, body = site.api("POST", "/api/friendship/request", {"username": handle})
    if 200 <= status < 300:
        requested[handle] = time.time()
        return "friend request sent"
    return f"friend request failed ({http_error(status, body)})"


def poke_next(site, state, dry_run, settings):
    """Pokes one person: the friend whose cooldown ended longest ago, or else someone else who is due
    (or, with friends_first off, anyone due at random).
    Returns when the poke was sent, or None if nobody was poked."""
    handle = site.require_sign_in()
    if state.get("handle") != handle:
        # First run, or signed in as someone else: start a fresh schedule.
        state.clear()
        state["handle"] = handle
    state.pop("needs_login", None)
    next_poke_at = state.setdefault("next_poke_at", {})

    friends = state.get("friends") or []
    if not friends or time.time() - state.get("friends_fetched_at", 0) >= FRIENDS_REFRESH:
        friends = fetch_friends(site, handle)
        state["friends"], state["friends_fetched_at"] = friends, time.time()
        requested = state.setdefault("friend_requested", {})
        for who in [h for h in friends if h in requested]:  # the site doesn't say when someone accepts, but here they are
            del requested[who]
            log(f"~{who} (friend): accepted your friend request.")
    write_json(STATE_FILE, state)

    counts = today(state)
    if dry_run:
        print(f"Today: {counts['pokes']} pokes, {counts['friend_requests']} friend requests.")

    def due(people):
        """Who in people can be poked now, longest-waiting first (ties, like never-poked, in random order)."""
        now = time.time()
        return [h for _, _, h in sorted((next_poke_at.get(h, 0), random.random(), h) for h in people if next_poke_at.get(h, 0) <= now)]

    not_others = set(friends) | {handle}
    others = set(read_json(EVERYONE_FILE).get("known", [])) - not_others
    if not dry_run and (settings["pokes"] or settings["friend_requests"]) and len(due(others)) < DISCOVER_BELOW:  # nothing to find them for otherwise
        others = discover(site, state) - not_others
    friends_due, others_due = due(friends), due(others)
    queue = friends_due + others_due
    if not settings["friends_first"]:
        random.shuffle(queue)
    if dry_run:
        print(f"~{handle}: {len(friends_due)} of {len(friends)} friends and {len(others_due)} of {len(others)} others due for a poke.")
        if queue:
            print(f"Next poke: ~{queue[0]}" if settings["pokes"] else "Pokes are on manual.")
        return
    if not settings["pokes"]:
        if settings["friend_requests"]:
            ask_next(site, state, others_due, counts)
        return None

    for target in queue[:MAX_TRIES_PER_SLOT]:
        poked_at = time.time()
        result = poke(site, target, next_poke_at)
        state["failures_in_a_row"] = state.get("failures_in_a_row", 0) + 1 if result == "failed" else 0
        write_json(STATE_FILE, state)
        if result != "cooldown":
            kind = "friend" if target in friends else "other"
            if result == "poked":
                counts["pokes"] += 1
                if kind == "other" and settings["friend_requests"]:
                    result += "; " + ask_friend(site, target, state, counts)
                else:
                    write_json(STATE_FILE, state)
                    remember_picture(site, target)
            log(f"~{target} ({kind}): {result}. Today: {counts['pokes']} pokes, {counts['friend_requests']} friend requests. "
                f"Still due: {len(due(friends))} friends, {len(due(others))} others.")
            return poked_at
        site.wait(DELAY)


def ask_friend(site, handle, state, counts):
    """Looks them up, sends a friend request or accepts theirs (see befriend), counts it, and keeps their picture.
    Returns what happened."""
    looked = look_up(site, handle)
    outcome = befriend(site, handle, state, looked)
    counts["friend_requests"] += outcome == "friend request sent"
    write_json(STATE_FILE, state)
    remember_picture(site, handle, looked)
    return outcome


def ask_next(site, state, others_due, counts):
    """With pokes on manual but friend requests on auto: sends a friend request to the next person who'd have been
    poked (anyone already asked, or who declined, is skipped; each person is looked at once a day at most)."""
    asked = state.setdefault("asked_at", {})
    now = time.time()
    for handle in [h for h, t in asked.items() if now - t >= ASK_AGAIN]:
        del asked[handle]
    skip = set(asked) | set(state.get("friend_requested") or {}) | set(state.get("friend_declined") or {})
    target = next((h for h in others_due if h not in skip), None)
    if not target:
        return
    asked[target] = now
    outcome = ask_friend(site, target, state, counts)
    log(f"~{target} (other): {outcome} (pokes are on manual). Today: {counts['pokes']} pokes, {counts['friend_requests']} friend requests.")


def feed_handles(event):
    """Everyone an activity-feed event mentions (from/to on pokes, users on friendships, user on joins, ...)."""
    if isinstance(event, dict):
        if isinstance(event.get("userHandle"), str):
            yield event["userHandle"]
        for value in event.values():
            yield from feed_handles(value)
    elif isinstance(event, list):
        for value in event:
            yield from feed_handles(value)


def discover(site, state):
    """Finds more people by reading public friend lists, starting from your friends and anyone the activity feed shows."""
    everyone = read_json(EVERYONE_FILE)
    known = set(everyone.get("known", []))
    crawled = set(everyone.get("crawled", []))
    known |= set(state.get("friends", [])) | set(store.feed_recent())
    known.discard(state["handle"])
    pages = 0
    for handle in sorted(known - crawled, key=lambda h: (h not in state.get("friends", []), random.random())):
        if pages >= CRAWL_PAGES_PER_RUN:
            break
        quoted = urllib.parse.quote(handle, safe="")
        for page in range(1, CRAWL_MAX_PAGES_PER_USER + 1):
            status, body = site.api("GET", f"/api/profile/friends?username={quoted}&page={page}&limit=50")
            pages += 1
            site.wait(DELAY)
            if status != 200:
                break
            handles, more = friends_page(body)
            known |= set(handles)
            if not more or pages >= CRAWL_PAGES_PER_RUN:
                break
        crawled.add(handle)
    known.discard(state["handle"])
    write_json(EVERYONE_FILE, {"known": sorted(known), "crawled": sorted(crawled)})
    return known


_ME = {"handle": None, "at": 0}


def my_handle():
    """Your RemiliaNET handle, from the bot's state (looked at again every minute)."""
    if time.time() - _ME["at"] > 60:
        _ME.update(handle=read_json(STATE_FILE).get("handle"), at=time.time())
    return _ME["handle"]


def watch_feed():
    """Runs forever: notes when each user last showed up in the public activity feed, for runs to view them.

    Sightings older than a day are dropped (see store.FEED_KEEP)."""
    move_to_store()
    drops = 0  # disconnects since the feed last connected
    recent, shown_at = collections.deque(maxlen=FEED_SHOWN), 0.0  # the newest events, for the dashboard's RemiliaNET Activity
    while True:
        try:
            request = urllib.request.Request(FEED_URL, headers={"User-Agent": USER_AGENT, "Accept": "text/event-stream"})
            with urllib.request.urlopen(request, timeout=FEED_IDLE_TIMEOUT) as stream:
                drops = 0
                for raw in stream:
                    line = raw.decode("utf-8", "replace")
                    if not line.startswith("data:"):
                        continue
                    now = time.time()
                    try:
                        events = json.loads(line[5:])
                        events = events if isinstance(events, list) else [events]
                    except ValueError:
                        continue  # one garbled message: skip it, keep the connection
                    recent.extend({**e, "seen_at": now} for e in events if isinstance(e, dict))
                    if now - shown_at >= FEED_SHOW_EVERY and someone_looking():
                        write_json(REMILIA_FEED_FILE, {"events": list(recent), "at": now})
                        shown_at = now
                    try:
                        store.feed_touch(set(feed_handles(events)))
                    except sqlite3.Error as e:
                        log(f"History: couldn't note who the feed showed ({e}).")
                    me = my_handle()
                    mine = [e for e in events if isinstance(e, dict) and me and me in set(feed_handles(e))
                            and as_dict(e.get("from")).get("userHandle") != me]  # not what you did yourself
                    if mine:  # live news about you, for the bot to look at right away and the dashboard to show
                        news = (read_json(FEED_US_FILE).get("events") or []) + [{**e, "seen_at": now} for e in mine]
                        write_json(FEED_US_FILE, {"events": news[-40:], "at": now})
        except socket.timeout:
            pass  # a quiet feed; just reconnect
        except (OSError, ValueError) as e:
            drops += 1
            if drops > 1 or not isinstance(e, ConnectionResetError):  # the site drops the stream now and then; that alone isn't news
                log(f"feed: disconnected ({e}), reconnecting in {FEED_RECONNECT}s")
        time.sleep(FEED_RECONNECT)


def view_profile(site, handle):
    """Opens their profile the way the website does (the profile data, then the page itself). Returns the HTTP status."""
    status, profile = site.signed_api("GET", f"/api/profile/~{urllib.parse.quote(handle, safe='')}")
    if status == 200:
        note_achievements(profile)
        site.api("HEAD", f"/~{urllib.parse.quote(handle, safe='')}")
    return status


def view_feed_users(site, state, dry_run, until=None):
    """Views everyone the feed has shown since their last view, newest first; you and your friends are skipped.
    Stops at until (a time), so views fill the wait before the next poke without delaying it."""
    already = store.viewed_lately()
    skip = set(state.get("friends") or []) | {state.get("handle")}
    todo = [h for h in store.feed_recent() if h not in skip and h not in already]  # newest first
    if not todo:
        return 0
    if dry_run:
        print("Would view: " + ", ".join(f"~{h}" for h in todo))
        return 0

    viewed = 0
    for handle in todo:
        if until is not None and time.time() >= until:
            break
        if profile_edit_waiting():  # one from the dashboard goes first
            apply_profile_edit(site, state)
        status = view_profile(site, handle)
        if status in (200, 404):  # a 404 is a deleted account: nothing to retry
            store.mark_viewed(handle)
            viewed += status == 200
        elif status == 429:
            log("Rate limited while viewing profiles; the rest wait for the next run.")
            break
        else:
            log(f"~{handle}: profile view failed with HTTP {status}")
        site.wait(DELAY)
    save_achievement_list()
    note_views(viewed)
    return viewed


def note_views(viewed):
    """Counts profile views, and logs how many once an hour at most."""
    now = time.time()
    if viewed and not _VIEWS["n"]:
        _VIEWS["since"] = now
    _VIEWS["n"] += viewed
    if _VIEWS["n"] and now - _VIEWS["since"] >= VIEWS_LOGGED_EVERY:
        log(f"Profile views from the activity feed: {_VIEWS['n']} since {time.strftime('%H:%M', time.localtime(_VIEWS['since']))}.")
        _VIEWS.update(n=0, since=now)
