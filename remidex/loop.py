"""The bot's rounds: what's due and a batch of pokes, with a browser open, then a long rest with it closed. The website's
scheduler (hosted/web.py) starts each round (poke_browser.py --round) when round_wanted says one is wanted."""

import argparse
import random
import time
import traceback

from remidex.core import (
    APP_DIR,
    COOKIES_FILE,
    FEED_US_FILE,
    GAME_FILE,
    LOGIN_HELP,
    LOOKING_FILE,
    NeedsLogin,
    OVERRIDES_FILE,
    STATE_FILE,
    _HEALTH,
    changed,
    log,
    read_json,
    someone_looking,
    stamp,
    write_json,
)
from remidex.settings import current_settings, load_settings
from remidex.state import cache_for_dashboard, keep_history, save_game
from remidex.social import (
    PROFILE_EDIT_FILE,
    accept_requests,
    apply_profile_edit,
    cache_profile_views,
    poke_next,
    profile_edit_waiting,
    view_feed_users,
)
from remidex.beetle import CLAIM_FILE, claim_now, claims_waiting, play_beetleboy
from remidex.chat import CHAT_OUT_FILE, chat_waiting, send_chat, sync_chat
from remidex.craft import beetlecraft
from remidex.browser import Site, sign_in_from_dashboard


TICK = 2  # seconds between looks while waiting: how quickly it notices a change from the dashboard or the site
STATUS_FILE = APP_DIR / "bot_status.json"  # what the bot is doing and what's next, for the dashboard
FEED_REACT_GAP = 20  # react to live news about you at most this often (seconds)
GAME_SYNC_EVERY = 15  # while someone's looking, re-read the game this often, so what they do on the site shows up (seconds)
# A round opens a browser for a few minutes: it does what's due and a batch of pokes, then ends, and the browser stays
# closed through the rest between rounds (see settings.SESSION_REST).
CLOSE_WITHIN = 60  # a round ends once nothing's due within this (seconds)...
SESSION_MAX = 10 * 60  # ...or after this long, even with things still due: they wait for the next round
VIEW_TIME = 60  # a round spends this long at most viewing the profiles of people in the activity feed (seconds)
ERROR_RETRY = 60  # after a round that failed, the next starts this soon; after two in a row, after the usual rest
STOP_AFTER_FAILURES = 5  # this many failed pokes in a row: the site is unhappy, so no more pokes this round
_STATES = {}  # path -> (its mark of change, what's in it)


def peek_state(path=None):
    """state.json (or path: another bot's), for looking at only: it's big, so it's read again only when it changes."""
    path = path or STATE_FILE
    key = stamp(path)
    if path not in _STATES or _STATES[path][0] != key:
        _STATES[path] = (key, read_json(path))
    return _STATES[path][1]


def next_tasks(state, settings):
    """When each thing the bot does is next due ({name: time}): the next poke slot, the claims and hunts check, the
    craft check, who's poked or viewed you, and friend requests to accept. A time in the past means due now."""
    tasks = {"poke": state.get("next_slot_at", 0), "claims": state.get("beetle_check_at", 0), "news": state.get("views_check_at", 0)}
    tasks["craft"] = state.get("craft_check_at", 0)
    if settings["friend_requests"]:
        tasks["friends"] = state.get("accept_check_at", 0)
    return tasks


TASK_NAMES = {"poke": "Next poke", "claims": "Claims and hunts check", "news": "Check who's poked or viewed you", "craft": "Craft check", "friends": "Accept friend requests"}
_REPORTED = {"doing": None, "at": 0}


def report(state, settings, doing, chrome=True):
    """Tells the dashboard what the bot is doing now and what's next. Written when what it's doing changes, and now and then."""
    now = time.time()
    if doing == _REPORTED["doing"] and now - _REPORTED["at"] < 60:
        return
    _REPORTED.update(doing=doing, at=now)
    tasks = next_tasks(state, settings)
    if not (settings["pokes"] or settings["friend_requests"]):
        tasks.pop("poke", None)  # pokes and friend requests are on manual: there's no poke to wait for
    rest = 0 if chrome else state.get("rest_until", 0)  # (resting: nothing happens before the next round)
    write_json(STATUS_FILE, {"at": now, "doing": doing, "chrome": chrome, "next": sorted(({"what": TASK_NAMES[k], "at": max(t, rest)} for k, t in tasks.items()), key=lambda x: x["at"]),
                             "ok_at": _HEALTH["ok_at"], "errors": _HEALTH["errors"]})


def news_about_you(state):
    """Live news about you from the activity feed (someone poked you, or a friendship changed): looks at who's poked or
    viewed you, and at friend requests, right away instead of at the next check. Once in a while at most."""
    due = max(time.time(), state.get("news_reacted_at", 0) + FEED_REACT_GAP)  # not more often than that
    state["news_reacted_at"] = due
    for check in ("views_check_at", "accept_check_at"):
        state[check] = min(state.get(check, 0), due)


_SYNCED = {"at": 0}


def sync_game(site, state):
    """Re-reads your game, for the dashboard, when someone's looking at it: what you do on RemiliaNET (craft, claim, hunt)
    shows up within seconds instead of at the bot's next check. New items get their cards read too. If the bag changed,
    crafting looks at it again straight away."""
    if time.time() - _SYNCED["at"] < GAME_SYNC_EVERY or not someone_looking():
        return
    _SYNCED["at"] = time.time()
    status, game = site.signed_api("GET", "/api/beetle/user")
    if status != 200 or not isinstance(game, dict):
        return
    saved = read_json(GAME_FILE)
    cards = saved.get("cards") if isinstance(saved.get("cards"), dict) else {}
    if any(k not in cards for k in game.get("inventory") or {}):
        cards_status, fresh = site.signed_api("GET", "/api/beetle/cards")
        cards = fresh if cards_status == 200 and isinstance(fresh, dict) else cards
    if (game.get("inventory") or {}) != ((saved.get("game") or {}).get("inventory") or {}):
        state["craft_check_at"] = 0  # the bag changed: see what that makes possible now
    without_timers = lambda g: {k: v for k, v in g.items() if k != "cooldowns"}  # noqa: E731  (they count down on their own)
    if without_timers(game) == without_timers(saved.get("game") or {}) and cards == saved.get("cards"):
        return  # nothing's changed: nothing to save, and nothing for the dashboard to redraw
    save_game(game, cards, outside=True)


def wait_and_watch(site, state, seconds):
    """Waits, making a profile change, a claim clicked on a card, and sending a chat message from the dashboard as soon as
    one is asked for, and keeping the game and the chat up to date for the dashboard while someone's looking at it."""
    until = time.time() + seconds
    while (left := until - time.time()) > 0:
        if profile_edit_waiting():
            apply_profile_edit(site, state)
        if claims_waiting():
            claim_now(site, state)
        if chat_waiting():
            send_chat(site, state)
        sync_game(site, state)
        sync_chat(site)
        if changed(FEED_US_FILE):
            news_about_you(state)
        site.wait(min(left, TICK))


def schedule_next(state, settings, poked_at, last=False):
    """Times the next poke: a few seconds on (see POKE_GAP), or after the round's last poke (last, nobody poked, or
    failed pokes in a row), the shortest rest between rounds on (see SESSION_REST): the next round starts with pokes.
    Returns whether that was the round's last poke."""
    if state.get("failures_in_a_row", 0) >= STOP_AFTER_FAILURES:
        log(f"{state['failures_in_a_row']} failed pokes in a row; no more until the next round.")
        last = True
    if not settings.get("layer3"):
        if poked_at is not None:
            state["next_slot_at"] = poked_at + random.uniform(*settings["gap"])
            write_json(STATE_FILE, state)
            return False
        now = time.time()
        next_times = [t for t in state.get("next_poke_at", {}).values() if t > now]
        earliest = min(next_times) if next_times else now + 60
        state["next_slot_at"] = max(now + 15, min(earliest, now + 300))
        write_json(STATE_FILE, state)
        return True
    last = last or poked_at is None
    rest = settings["rest"][0] if last else random.uniform(*settings["gap"])
    state["next_slot_at"] = (poked_at or time.time()) + rest
    write_json(STATE_FILE, state)
    return last


def pause_for_login(error):
    """Pauses the bot until a new sign-in clears it."""
    state = read_json(STATE_FILE)
    if not state.get("needs_login"):
        state["needs_login"] = True
        write_json(STATE_FILE, state)
    log(f"Paused: {error}. {LOGIN_HELP}")


def react(site, state, settings):
    """Does whatever's due: claims and hunts, crafting, who's poked or viewed you, and friend requests, then saves what
    the dashboard shows. Each looks at its own timer, so what isn't due yet is left alone (a change from the dashboard
    counts as due, to the ones it affects)."""
    report(state, settings, "Checking in on your account")
    play_beetleboy(site, state, settings)
    beetlecraft(site, state, current_settings())  # (read again: a pause picked during the claims and hunts counts)
    cache_profile_views(site, state)
    if settings["friend_requests"]:
        accept_requests(site, state)
    cache_for_dashboard(site)


def chrome_session(state, settings):
    """One round: whatever's due (claims and hunts, crafting, who's poked or viewed you, friend requests),
    a batch of pokes a few seconds apart (see POKES_PER_SESSION), and up to VIEW_TIME of profile views in between. Returns
    once nothing's due within CLOSE_WITHIN, or after SESSION_MAX. While you're using the
    dashboard, or it has something for the bot to do, the round goes on, and changes from it are acted on within seconds."""
    site = Site()
    try:
        opened, first, last_react, poked = time.time(), True, 0, 0
        views_until, viewed = None, False  # (viewed: no more profile views this round)
        site.require_sign_in()
        if profile_edit_waiting():
            apply_profile_edit(site, state)
        if claims_waiting():
            claim_now(site, state)
        while True:
            keep_history()
            settings = current_settings()
            now = time.time()
            tasks = next_tasks(state, settings)
            edited = changed(OVERRIDES_FILE)  # something picked on the dashboard
            wanted = edited or someone_looking() or profile_edit_waiting() or chat_waiting() or claims_waiting()
            viewing = settings["view_profiles"] and state.get("handle") and not viewed
            if LOOKING_FILE.exists() and not someone_looking():
                log("App closed in browser; stopping bot round.")
                break
            if not wanted and (now - opened >= SESSION_MAX or (min(tasks.values()) - now > CLOSE_WITHIN and not viewing)):
                break  # the round's done: close the session
            if tasks["poke"] <= now:
                report(state, settings, "Poking")
                poked += 1
                poked_at = poke_next(site, state, settings)
                batch_limit = settings.get("batch") if settings.get("layer3") else 0
                is_last = (poked >= batch_limit) if batch_limit else (poked_at is None)
                if schedule_next(state, settings, poked_at, last=is_last):
                    poked = 0  # (the round kept going for the dashboard: the next batch starts afresh)
                site.save_cookies()
                continue
            if (first or edited or any(t <= now for k, t in tasks.items() if k != "poke")) and now - last_react >= TICK:
                first, last_react = False, now
                react(site, state, settings)
                continue
            if viewing:  # in small batches between the pokes, so nothing waits long
                views_until = views_until or now + VIEW_TIME
                until = min(tasks["poke"] - 1, views_until, now + 8)
                if until > now:
                    if view_feed_users(site, state, until=until):
                        continue
                    viewed = True  # nobody (more) to view
                elif now >= views_until:
                    viewed = True
            report(state, settings, "Waiting")
            wait_and_watch(site, state, TICK)
    finally:
        site.close()


def chrome_due_in(state, settings):
    """Seconds until the next round: when the first thing is due, but not before the rest after the last round is over."""
    return max(min(next_tasks(state, settings).values()), state.get("rest_until", 0)) - time.time()


def hiccup(e):
    """A calm word for the log about a hiccup the bot gets over on its own (its browser closing, a moment offline), or
    None for a real error."""
    if "has been closed" in str(e):
        return "Browser: it closed unexpectedly; opening it again soon."
    if "(HTTP 0)" in str(e):
        return "Network: RemiliaNET couldn't be reached; trying again soon."
    return None


def round_wanted(home):
    """Whether a bot should have a round now: when something's due (a poke, a claim or hunt, a craft check, who's poked
    you, friend requests) and the rest after the last round is over (see chrome_due_in), or straight away when you pick
    something on the dashboard, click a claim, send a chat message or change your profile, or are using the dashboard. Never while a
    new sign-in is needed. News about you from the activity feed is noted for the next round. home: the bot's folder
    (the website's scheduler asks about everyone's)."""
    where = lambda f: home / f.name  # noqa: E731
    state = peek_state(where(STATE_FILE))
    if state.get("needs_login") or not where(COOKIES_FILE).exists():
        return False
    if where(LOOKING_FILE).exists() and not someone_looking(where(LOOKING_FILE)):
        return False  # app closed completely in browser: no rounds
    if changed(where(FEED_US_FILE)):  # something happened to you on RemiliaNET: look at it in the next round
        state = read_json(where(STATE_FILE))
        news_about_you(state)
        write_json(where(STATE_FILE), state)
    settings = load_settings(where(OVERRIDES_FILE))
    woken = [profile_edit_waiting(where(PROFILE_EDIT_FILE)), chat_waiting(where(CHAT_OUT_FILE)), someone_looking(where(LOOKING_FILE)),
             changed(where(OVERRIDES_FILE)), claims_waiting(where(CLAIM_FILE))]  # (all asked, so none is missed)
    return chrome_due_in(peek_state(where(STATE_FILE)), settings) <= 0 or any(woken)


def run_round():
    """--round (the website's scheduler starts one for a bot when round_wanted says so): one round (see chrome_session),
    then the rest after it (see chrome_due_in). After a round that failed, the next starts in a minute; after two in a
    row, after the usual rest. (They're counted in the state: each round is a program of its own.)"""
    state = read_json(STATE_FILE)
    try:
        chrome_session(state, current_settings())
        state.pop("rounds_failed", None)
    except NeedsLogin as e:
        pause_for_login(e)
        return
    except Exception as e:  # anything else too: noted, and the next round tries again (not at once, over and over, at the site)
        state["rounds_failed"] = state.get("rounds_failed", 0) + 1
        again = "trying again in a minute" if state["rounds_failed"] == 1 else "trying again after the usual rest"
        if isinstance(e, (RuntimeError, OSError)):
            log(hiccup(e) or f"Error: {e} ({again})")
        else:
            log(f"Error: something unexpected ({type(e).__name__}: {e}); {again}.")
            traceback.print_exc()  # (the details, for working out what happened)
    if state.get("rounds_failed") == 1:
        rest = ERROR_RETRY
    elif someone_looking():
        rest = random.uniform(20, 45)  # app is open in browser: next round starts in 20-45s
    else:
        rest = random.uniform(*current_settings()["rest"])
    state["rest_until"] = time.time() + rest
    write_json(STATE_FILE, state)
    report(state, current_settings(), "Resting between rounds", chrome=False)


def main():
    parser = argparse.ArgumentParser(description="One person's RemiDex bot, as the website (hosted/web.py) runs it in their folder (REMIDEX_HOME).")
    parser.add_argument("--round", action="store_true", help="set by the website's scheduler: one round now, then the rest after it")
    parser.add_argument("--sign-in", action="store_true", help="set by the dashboard's sign-in: signs in with the email, password (and two-factor code) it sends on stdin")
    args = parser.parse_args()
    if args.round:
        run_round()
    elif args.sign_in:
        sign_in_from_dashboard()
    else:
        parser.print_help()
