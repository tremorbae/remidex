"""BeetleBoy: the daily cheese, the free beetle, and every hunt the cheese allows. Each can be left to you (manual), and
then a click on its card on the dashboard has the bot do it once, straight away (see claim_now)."""

import random
import time

from remidex.core import APP_DIR, BEETLE_RETRY, DELAY, GAME_FILE, STATE_FILE, editing, http_error, log, read_json, write_json
from remidex.state import note_level, save_game, streak_watch


HUNT_COST = 20  # cheese per hunt
HUNTS_PER_CYCLE = 3  # after the third hunt, hunting locks until HUNT_RECHARGE after the last one
HUNT_RECHARGE = 90 * 60
HUNT_GAP = (6, 15)  # seconds between hunts in a row, about as long as the website's hunt animation
BEETLE_CHECK_MAX = 15 * 60  # look at the game at least this often, even when nothing is due
CLAIMS = ("claimUBC", "catchBeetle", "junkFaucet", "beetleHunt")  # in the order they're done: cheese first, so hunts can spend it
CLAIM_FILE = APP_DIR / "claim_now.json"  # clicked on the dashboard: {action: {"asked_at"}}, then "done_at" and "error" (None: it worked)
CLAIM_KEPT = 10 * 60  # a click the bot hasn't got to by then (it's signed out, say) is dropped: it never claims much later


def beetle_action(site, action):
    """Does one BeetleBoy action the way the website does. Returns (result, the game state after it, error or None)."""
    status, body = site.signed_api("POST", f"/api/beetle/action/{action}", {})
    body = body if isinstance(body, dict) else {}
    user = body.get("user") if isinstance(body.get("user"), dict) else None  # (anything else isn't a game to go on with)
    if user:
        note_level(user)  # (XP after every action, so a level-up is pinned down to the action that caused it)
    if 200 <= status < 300 and body.get("success"):
        return body.get("result") if isinstance(body.get("result"), dict) else {}, user, None
    return {}, user, body.get("error") or body.get("message") or http_error(status, body)


def found(result):
    """What a catch, hunt or Cheeseman handout gave, by name."""
    cards = [result.get("beetleCard"), *(result.get("secondaryItemDrops") or [])]
    return " + ".join(c["beetle_name"] for c in cards if isinstance(c, dict) and c.get("beetle_name")) or "nothing"


def xp_gain(before, after):
    """" (+12 XP)" when an action's game state after it shows more XP than before, else ""."""
    xp = lambda g: g.get("xp") if isinstance(g, dict) and type(g.get("xp")) is int else None  # noqa: E731
    return f" (+{xp(after) - xp(before)} XP)" if xp(before) is not None and xp(after) is not None and xp(after) > xp(before) else ""


def claim(site, action, game):
    """One claim or hunt, with what it gave in the log the way the history reads it (see store.py), so the dashboard counts
    it whether the bot did it on its own or because you clicked. Returns (the game state after it, the error or None)."""
    result, after, error = beetle_action(site, action)
    if error:
        return after or game, error
    if action == "claimUBC":
        streak = f", streak {result['streak']}" if result.get("streak") else ""
        log(f"Daily cheese: +{result.get('cheese', '?')}{streak}{xp_gain(game, after)}.")
    elif action == "catchBeetle":
        log(f"Free beetle: {found(result)} (+{result.get('xp', 0)} XP).")
    elif action == "junkFaucet":
        log(f"Cheeseman: {found(result)}{xp_gain(game, after)}.")
    else:
        cheese = ((after or game).get("inventory") or {}).get("cheese")
        left = f" {cheese:,} cheese left." if isinstance(cheese, int) else ""
        log(f"Hunt: {found(result)} (+{result.get('xp', 0)} XP).{left}" if result.get("success") and result.get("beetleCard") else f"Hunt: found nothing.{left}")
    return after or game, None


def hunts_left(game):
    """How many hunts the game allows right now: 3, then none until 90 minutes after the third; 20 cheese each."""
    used = game.get("beetleHuntsUsed") or 0
    if used >= HUNTS_PER_CYCLE:
        if time.time() * 1000 < (game.get("lastBeetleHuntDate") or 0) + HUNT_RECHARGE * 1000:
            return 0
        used = 0
    cheese = (game.get("inventory") or {}).get("cheese", 0)
    return max(0, min(HUNTS_PER_CYCLE - used, cheese // HUNT_COST))


def next_beetle_check(game, retry_at, enabled):
    """Seconds until something in the game could be ready: a claim's cooldown, a refused action's retry, or hunts
    unlocking. Between one minute and BEETLE_CHECK_MAX."""
    now = time.time()
    cooldowns = game.get("cooldowns") or {}
    waits = []
    for action in enabled:
        wait = max((cooldowns.get(action) or 0) / 1000, retry_at.get(action, 0) - now)
        if action == "beetleHunt":
            if (game.get("inventory") or {}).get("cheese", 0) < HUNT_COST:
                continue  # waits on cheese, which comes with the daily claim
            if (game.get("beetleHuntsUsed") or 0) >= HUNTS_PER_CYCLE:
                wait = max(wait, (game.get("lastBeetleHuntDate") or 0) / 1000 + HUNT_RECHARGE - now)
        waits.append(wait + 5)
    return min([BEETLE_CHECK_MAX, *waits]) if waits else BEETLE_CHECK_MAX


def play_beetleboy(site, state, dry_run, settings):
    """Claims the daily cheese, the free beetle and the Cheeseman's junk whenever each is ready, then spends cheese
    on every hunt the game allows. Each part can be set to manual on the dashboard. Only looks at
    the game when something could be due (and, with everything on manual, now and then to keep the dashboard current)."""
    enabled = [a for a, on in settings["beetleboy"].items() if on]
    switched = state.get("beetle_switches") != enabled  # a switch was flipped: look now rather than at the planned time
    state["beetle_switches"] = enabled
    if not dry_run and not switched and time.time() < state.get("beetle_check_at", 0):
        return
    status, game = site.api("GET", "/api/beetle/user")
    if status != 200 or not isinstance(game, dict):
        log(f"BeetleBoy: couldn't load the game (HTTP {status}).")
        state["beetle_check_at"] = time.time() + BEETLE_RETRY
        return
    cards = None
    if not read_json(GAME_FILE).get("cards"):
        cards_status, cards = site.api("GET", "/api/beetle/cards")
        cards = cards if cards_status == 200 and isinstance(cards, dict) else None  # (an error answer isn't your cards)
    save_game(game, cards, outside=True)
    retry_at = state.setdefault("beetle_retry_at", {})

    def ready(action):
        return action in enabled and not (game.get("cooldowns") or {}).get(action) and retry_at.get(action, 0) <= time.time()

    claims = [a for a in ("claimUBC", "catchBeetle", "junkFaucet") if ready(a)]  # cheese first, so hunts can spend it
    if dry_run:
        names = {"claimUBC": "daily cheese", "catchBeetle": "free beetle", "junkFaucet": "Cheeseman junk"}
        hunts = hunts_left(game) if ready("beetleHunt") else 0
        cheese = (game.get("inventory") or {}).get("cheese", 0)
        print(f"BeetleBoy: would claim {', '.join(names[a] for a in claims) or 'nothing'}; "
              f"{hunts} hunts available ({cheese} cheese).")
        return

    for action in claims:
        game, error = claim(site, action, game)
        if error:
            retry_at[action] = time.time() + BEETLE_RETRY
            log(f"BeetleBoy: {action} refused ({error}); trying again in {BEETLE_RETRY // 60} minutes.")
        else:
            retry_at.pop(action, None)
            state["craft_check_at"] = 0  # new items: see what they make now, not at the next craft check
        write_json(STATE_FILE, state)
        site.wait(DELAY)

    for _ in range(HUNTS_PER_CYCLE):
        if not (ready("beetleHunt") and hunts_left(game)):
            break
        game, error = claim(site, "beetleHunt", game)
        if error:
            retry_at["beetleHunt"] = time.time() + BEETLE_RETRY
            log(f"BeetleBoy: hunt refused ({error}); trying again in {BEETLE_RETRY // 60} minutes.")
            break
        state["craft_check_at"] = 0
        write_json(STATE_FILE, state)
        if hunts_left(game):
            site.wait(random.uniform(*HUNT_GAP))

    state["beetle_check_at"] = time.time() + next_beetle_check(game, retry_at, enabled)
    write_json(STATE_FILE, state)
    streak_watch(game)
    save_game(game)


def claims_waiting(path=None):
    """What's been clicked on the dashboard and not done yet: {action: when it was asked} (path: another bot's CLAIM_FILE)."""
    now = time.time()
    return {a: c["asked_at"] for a, c in read_json(path or CLAIM_FILE).items()
            if a in CLAIMS and isinstance(c, dict) and not c.get("done_at") and type(c.get("asked_at")) in (int, float) and 0 <= now - c["asked_at"] < CLAIM_KEPT}


def claim_now(site, state):
    """Does what was clicked on the dashboard (a card on manual) straight away, the way the game's own buttons do: the
    daily cheese, the free beetle, the Cheeseman's junk, or one hunt (and never sooner after the last hunt than the site's
    own animation allows). Notes how each went, for the dashboard."""
    asked = claims_waiting()
    if not asked:
        return
    status, game = site.signed_api("GET", "/api/beetle/user")
    errors = {}
    if status != 200 or not isinstance(game, dict):
        errors = dict.fromkeys(asked, f"couldn't load the game (HTTP {status})")
    else:
        save_game(game, outside=True)  # (a fresh look: anything you did on RemiliaNET since is worked out from it)
        for action in [a for a in CLAIMS if a in asked]:  # cheese first, so a hunt can spend it
            if action == "beetleHunt" and not hunts_left(game):
                errors[action] = f"it takes {HUNT_COST} cheese" if (game.get("inventory") or {}).get("cheese", 0) < HUNT_COST else "hunting is recharging"
                continue
            if action != "beetleHunt" and (game.get("cooldowns") or {}).get(action):
                errors[action] = "not ready yet"  # (the dashboard then shows when it will be)
                continue
            if action == "beetleHunt" and (since := time.time() - (game.get("lastBeetleHuntDate") or 0) / 1000) < HUNT_GAP[0]:
                site.wait(HUNT_GAP[0] - since)
            game, errors[action] = claim(site, action, game)
            if errors[action]:
                log(f"BeetleBoy: {action} refused ({errors[action]}).")
            else:
                state["craft_check_at"] = 0  # new items: see what they make now, not at the next craft check
            site.wait(DELAY)
        save_game(game)
        write_json(STATE_FILE, state)
    with editing(CLAIM_FILE):
        claims = read_json(CLAIM_FILE)
        for action, asked_at in asked.items():
            if (claims.get(action) or {}).get("asked_at") == asked_at:  # (not one clicked again meanwhile)
                claims[action] = {"asked_at": asked_at, "done_at": time.time(), "error": errors.get(action)}
        write_json(CLAIM_FILE, claims)
