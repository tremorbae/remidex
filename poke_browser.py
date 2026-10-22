#!/usr/bin/env python3
"""Poke one RemiliaNET user every few minutes, around the clock, without an API key: friends whose
24h cooldown is up first, then everyone else it can find. After poking someone who isn't a friend it sends
them a friend request (or accepts theirs), and never asks anyone who declined again. Also views the profile
of everyone else who shows up in the activity feed (the site notifies them), and plays BeetleBoy: the daily
cheese, the free beetle (every 2 hours) and the Cheeseman's junk as soon as each is ready, then every hunt the cheese allows,
and crafts its way up to rarer beetles in BeetleCraft.

Drives a headless Google Chrome (through Playwright) that is signed in to
remilia.net in its own profile, separate from your everyday Chrome. launchd keeps it
running; it times each poke itself, keeping the page open while the next poke is near,
and views whoever the feed has shown in between, with the same requests the website
makes. Having the page open regularly also keeps the website's sign-in fresh. Runs
next to an always-on --watch-feed listener (see install_browser.sh).

    python3 poke_browser.py --login       one-time: sign in in a Chrome window (tick "Remember me")
    python3 poke_browser.py --dry-run     show who is due a poke or a view, without doing either
    python3 poke_browser.py               poke the next person now, and view everyone who is due
    python3 poke_browser.py --watch-feed  record everyone in the activity feed (runs forever)
"""
# The bot lives in the remidex/ package; this file is how you run it, and keeps the names dashboard.py and the tests use.

from remidex.core import (  # noqa: F401
    ASSEMBLE,
    DASH_DIR,
    FEED_US_FILE,
    GAME_FILE,
    HAMMERS,
    JUNK_CUBE_CRAFT,
    KEY,
    LOG_FILE,
    LOOKING_FILE,
    LOOKING_FOR,
    OVERRIDES_FILE,
    PEOPLE_PICS,
    QUEUE_MAX,
    SITE,
    SMASH,
    STATE_FILE,
    USER_AGENT,
    as_dict,
    changed,
    editing,
    read_json,
    someone_looking,
    stamp,
    write_json,
)
from remidex.settings import (  # noqa: F401
    BEETLE_SETTINGS,
    SOCIAL_SETTINGS,
    _ALERTED,
    _SETTINGS,
    alert,
    current_settings,
    load_settings,
)
from remidex.recipes import (  # noqa: F401
    CRAFT_STATS_FILE,
    REMOVED_FILE,
    HUNTED_COST,
    STUCK_COST,
    TASK_LOGS_KEPT,
    _BOOK,
    _REMOVED,
    _STATS,
    Bag,
    Planner,
    active_make,
    craft_cost,
    craft_focus,
    craft_specs,
    craft_stats,
    goal_crafts,
    in_group,
    label_of,
    long_shot,
    made_count,
    make_finished,
    make_title,
    new_make,
    note_craft,
    note_outside_crafts,
    plan_goal,
    plan_make,
    proven_low_odds,
    rebuild_task_log,
    recipe_book,
    recipe_label,
    recipe_named,
    recipes_for,
    remove_recipe,
    skipped_labels,
    task_id,
    finished_key,
    task_record,
    times_worked,
    two_pass,
    usable,
)
from remidex.state import (  # noqa: F401
    LEVELS_FILE,
    quest_title,
    streak_watch,
)
from remidex.social import (  # noqa: F401
    ACHIEVEMENT_LIST_FILE,
    MY_ACHIEVEMENTS_FILE,
    MY_PROFILE_FILE,
    PROFILE_EDIT_FILE,
    REMILIA_FEED_FILE,
    apply_profile_edit,
    profile_edit_waiting,
)
from remidex.beetle import (  # noqa: F401
    HUNTS_PER_CYCLE,
    HUNT_RECHARGE,
    beetle_action,
)
from remidex.craft import (  # noqa: F401
    FOR_GOOD,
    PURPLE_OFF,
    beetlecraft,
    craft_candidates,
    craft_wait,
    describe_craft,
    log_screen,
    next_craft,
    pick_sacrifice,
)
from remidex.chat import (  # noqa: F401
    ATTACHMENT_NAME,
    CHAT_FILE,
    CHAT_FILES,
    CHAT_OUT_FILE,
    GLOBAL_CHAT,
    REACTIONS,
    send_chat,
)
from remidex.loop import (  # noqa: F401
    STATUS_FILE,
    TICK,
    _SYNCED,
    chrome_session,
    main,
    report,
    sync_game,
    wait_and_watch,
)


if __name__ == "__main__":
    main()
