"""The main loop: short rounds with Chrome (what's due, a batch of pokes), and long rests with it closed."""

import argparse
import random
import sys
import time
import traceback

from remidex.core import (
    APP_DIR,
    COOKIES_FILE,
    FEED_US_FILE,
    GAME_FILE,
    LOGIN_HELP,
    NeedsLogin,
    OVERRIDES_FILE,
    PlaywrightError,
    STATE_FILE,
    _HEALTH,
    changed,
    log,
    read_json,
    someone_looking,
    stamp,
    sync_playwright,
    write_json,
)
from remidex.settings import alert, current_settings, load_settings
from remidex.state import cache_for_dashboard, keep_history, save_game
from remidex.social import (
    accept_requests,
    apply_profile_edit,
    cache_profile_views,
    poke_next,
    profile_edit_waiting,
    view_feed_users,
    watch_feed,
)
from remidex.beetle import play_beetleboy
from remidex.chat import chat_waiting, send_chat, sync_chat
from remidex.craft import beetlecraft
from remidex.browser import Site, sign_in_from_dashboard


TICK = 2  # seconds between looks while waiting: how quickly it notices a change from the dashboard or the site
STATUS_FILE = APP_DIR / "bot_status.json"  # what the bot is doing and what's next, for the dashboard
FEED_REACT_GAP = 20  # react to live news about you at most this often (seconds)
GAME_SYNC_EVERY = 15  # while someone's looking, re-read the game this often, so what they do on the site shows up (seconds)
# The scheduled bot runs all the time (launchd just keeps it running), but opens Chrome only for short rounds, with
# Chrome closed in between (see settings.SESSION_REST): a round does what's due and a batch of pokes, then ends.
CLOSE_WITHIN = 60  # a round ends once nothing's due within this (seconds)...
SESSION_MAX = 10 * 60  # ...or after this long, even with things still due: they wait for the next round
VIEW_TIME = 60  # a round spends this long at most viewing the profiles of people in the activity feed (seconds)
ERROR_RETRY = 60  # after a round that failed, the next starts this soon; after two in a row, after the usual rest
STOP_AFTER_FAILURES = 5  # this many failed pokes in a row: the site is unhappy, so no more pokes this round
_STATE = {"key": None, "value": {}}
_ROUNDS = {"failed": 0}  # rounds in a row that ended in an error


def peek_state():
    """state.json for looking at only (it's big, so read again only when it changes)."""
    key = stamp(STATE_FILE)
    if key != _STATE["key"]:
        _STATE.update(key=key, value=read_json(STATE_FILE))
    return _STATE["value"]


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
    """Waits, making a profile change and sending a chat message from the dashboard as soon as one is asked for, and
    keeping the game and the chat up to date for the dashboard while someone's looking at it."""
    until = time.time() + seconds
    while (left := until - time.time()) > 0:
        if profile_edit_waiting():
            apply_profile_edit(site, state)
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
    last = last or poked_at is None
    state["next_slot_at"] = (poked_at or time.time()) + (settings["rest"][0] if last else random.uniform(*settings["gap"]))
    write_json(STATE_FILE, state)
    return last


def pause_for_login(error):
    """Pauses the bot until a new sign-in clears it, with a single notification."""
    state = read_json(STATE_FILE)
    if not state.get("needs_login"):
        state["needs_login"] = True
        write_json(STATE_FILE, state)
        alert("signed_out", "Signed out of RemiliaNET", f"The bot is paused: {error}. {LOGIN_HELP}")
    log(f"Paused: {error}. {LOGIN_HELP}")


def run_once(args):
    """python3 poke_browser.py [--dry-run]: one poke (or a preview) right now."""
    if not COOKIES_FILE.exists():
        sys.exit("Not signed in yet: sign in on RemiDex (http://remidex.localhost:8642)")
    state, settings = read_json(STATE_FILE), load_settings()
    with sync_playwright() as playwright:
        site = Site(playwright)
        try:
            poked_at = poke_next(site, state, args.dry_run, settings)
            if not args.dry_run:
                schedule_next(state, settings, poked_at)
            play_beetleboy(site, state, args.dry_run, settings)
            beetlecraft(site, state, args.dry_run, settings)
            if not args.dry_run:
                cache_for_dashboard(site)
                cache_profile_views(site, state)
            if settings["view_profiles"]:
                view_feed_users(site, state, args.dry_run)
            site.save_cookies()
        finally:
            site.close()


def react(site, state, settings):
    """Does whatever's due: claims and hunts, crafting, who's poked or viewed you, and friend requests, then saves what
    the dashboard shows. Each looks at its own timer, so what isn't due yet is left alone (a change from the dashboard
    counts as due, to the ones it affects)."""
    report(state, settings, "Checking in on your account")
    play_beetleboy(site, state, False, settings)
    beetlecraft(site, state, False, current_settings())  # (read again: a pause picked during the claims and hunts counts)
    cache_profile_views(site, state)
    if settings["friend_requests"]:
        accept_requests(site, state)
    cache_for_dashboard(site)


def chrome_session(state, settings):
    """One round with Chrome: whatever's due (claims and hunts, crafting, who's poked or viewed you, friend requests), a
    batch of pokes a few seconds apart (see POKES_PER_SESSION), and up to VIEW_TIME of profile views in between. Returns
    once nothing's due within CLOSE_WITHIN, or after SESSION_MAX, and Chrome closes. While you're using the dashboard,
    or it has something for the bot to do, Chrome stays open, and changes from it are acted on within seconds."""
    with sync_playwright() as playwright:
        site = Site(playwright)
        try:
            opened, first, last_react, poked = time.time(), True, 0, 0
            views_until, viewed = None, False  # (viewed: no more profile views this round)
            site.require_sign_in()
            if profile_edit_waiting():
                apply_profile_edit(site, state)
            while True:
                keep_history()
                settings = current_settings()
                now = time.time()
                tasks = next_tasks(state, settings)
                edited = changed(OVERRIDES_FILE)  # something picked on the dashboard
                wanted = edited or someone_looking() or profile_edit_waiting() or chat_waiting()
                viewing = settings["view_profiles"] and state.get("handle") and not viewed
                if not wanted and (now - opened >= SESSION_MAX or (min(tasks.values()) - now > CLOSE_WITHIN and not viewing)):
                    break  # the round's done: close Chrome
                if tasks["poke"] <= now:
                    report(state, settings, "Poking")
                    poked += 1
                    if schedule_next(state, settings, poke_next(site, state, False, settings), last=poked >= settings["batch"]):
                        poked = 0  # (Chrome kept open for the dashboard: the next round's batch starts afresh)
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
                        if view_feed_users(site, state, False, until=until):
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
    """A calm word for the log about a hiccup the bot gets over on its own (the Mac sleeping, a moment offline), or None
    for a real error."""
    if "has been closed" in str(e):
        return "Chrome: it was closed from outside (usually the Mac sleeping or waking); opening it again soon."
    if "(HTTP 0)" in str(e):
        return "Network: RemiliaNET couldn't be reached (usually just after the Mac wakes); trying again soon."
    return None


def run_forever():
    """--scheduled: launchd starts this and keeps it running. Between rounds Chrome is closed and this only naps (it
    never keeps the Mac awake: a Mac that sleeps catches up when it wakes). A round starts when something's due (a poke,
    a claim or hunt, a craft check, who's poked you, friend requests) and the rest after the last round is over (see
    chrome_due_in), or straight away when you pick something on the dashboard, send a chat message or change your
    profile, or are using the dashboard. News about you from the activity feed waits for the next round. Waits quietly
    while a new sign-in is needed."""
    state = read_json(STATE_FILE)
    state["craft_check_at"] = 0  # a restart (an update, say) looks at crafting straight away, not at the next 15-minute check
    state["views_check_at"] = 0  # and at who's looked at your profile (the dashboard's all-time count)
    state.pop("rest_until", None)  # (and starts with a round)
    write_json(STATE_FILE, state)
    while True:
        keep_history()
        state = peek_state()
        if state.get("needs_login") or not COOKIES_FILE.exists():
            time.sleep(TICK)  # (carries on within seconds of a sign-in on the dashboard)
            continue
        settings = current_settings()
        if changed(FEED_US_FILE):  # something happened to you on RemiliaNET: look at it in the next round
            state = read_json(STATE_FILE)
            news_about_you(state)
            write_json(STATE_FILE, state)
            state = peek_state()
        due_in = chrome_due_in(state, settings)
        woken = [profile_edit_waiting(), chat_waiting(), someone_looking(), changed(OVERRIDES_FILE)]  # (all asked, so none is missed)
        if due_in > 0 and not any(woken):
            report(state, settings, "Resting between rounds (Chrome is closed)", chrome=False)
            time.sleep(min(due_in, TICK))  # short naps, so a Mac waking from sleep catches up straight away
            continue
        state = read_json(STATE_FILE)
        try:
            chrome_session(state, settings)
            _ROUNDS["failed"] = 0
        except NeedsLogin as e:
            pause_for_login(e)
            continue
        except Exception as e:  # anything else too: launchd would restart a crashed bot at once, over and over, each time at the site
            _ROUNDS["failed"] += 1
            again = "trying again in a minute" if _ROUNDS["failed"] == 1 else "trying again after the usual rest"
            if isinstance(e, (RuntimeError, OSError, PlaywrightError)):
                log(hiccup(e) or f"Error: {e} ({again})")
            else:
                log(f"Error: something unexpected ({type(e).__name__}: {e}); {again}.")
                traceback.print_exc()  # (the details, for working out what happened)
        rest = ERROR_RETRY if _ROUNDS["failed"] == 1 else random.uniform(*current_settings()["rest"])
        state["rest_until"] = time.time() + rest
        write_json(STATE_FILE, state)
        report(state, current_settings(), "Resting between rounds (Chrome is closed)", chrome=False)


def main():
    parser = argparse.ArgumentParser(description="Plays BeetleBoy and BeetleCraft, and pokes and views people as picked on the dashboard, through a signed-in headless Chrome.")
    parser.add_argument("--sign-in", action="store_true", help="set by the dashboard's sign-in page: signs in with the email, password (and two-factor code) it sends on stdin")
    parser.add_argument("--dry-run", action="store_true", help="show who is due a poke or a view, without doing either")
    parser.add_argument("--scheduled", action="store_true", help="set by launchd: runs forever, poking on schedule")
    parser.add_argument("--watch-feed", action="store_true", help="record everyone in the activity feed; runs forever (launchd keeps it up)")
    args = parser.parse_args()

    if args.watch_feed:
        watch_feed()
    if args.scheduled:
        run_forever()
    if args.sign_in:
        sign_in_from_dashboard()
        return
    try:
        run_once(args)
    except NeedsLogin as e:
        pause_for_login(e)
        sys.exit(1)
    except (RuntimeError, OSError, PlaywrightError) as e:
        log(f"Error: {e}")
        sys.exit(1)
