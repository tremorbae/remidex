#!/usr/bin/env python3
"""One person's RemiDex bot, as the website runs it (see hosted/web.py), in their folder (REMIDEX_HOME): it plays
BeetleBoy (the daily cheese, the free beetle, the Cheeseman's junk and every hunt the cheese allows) and crafts toward
what's picked on the dashboard in BeetleCraft, and pokes, friends and views people as picked there. Each round runs
direct HTTP requests signed in to remilia.net, with the same requests the website
makes.

    python3 poke_browser.py --round     one round now (the website's scheduler starts these)
    python3 poke_browser.py --sign-in   sign in (the dashboard's sign-in runs this: see browser.sign_in_from_dashboard)
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
    PURPLE,
    QUEUE_MAX,
    SACRIFICE,
    SHARED_DIR,
    SITE,
    SMASH,
    STATE_FILE,
    TIN_HAMMER_CRAFT,
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
    _SETTINGS,
    current_settings,
    load_settings,
    valid_steps,
)
from remidex.recipes import (  # noqa: F401
    CRAFT_STATS_FILE,
    REMOVED_FILE,
    HUNTED_COST,
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
    recipe_book,
    recipe_label,
    recipe_named,
    recipes_for,
    remove_recipe,
    sacrifice_kept,
    step_picks,
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
    CLAIM_FILE,
    CLAIM_KEPT,
    CLAIMS,
    HUNTS_PER_CYCLE,
    HUNT_RECHARGE,
    beetle_action,
    claim_now,
    claims_waiting,
)
from remidex.craft import (  # noqa: F401
    CRAFT_NOW_FILE,
    FOR_GOOD,
    PURPLE_OFF,
    beetlecraft,
    craft_candidates,
    craft_wait,
    describe_craft,
    log_screen,
    next_craft,
    pick_sacrifice,
    side_craft,
)
from remidex.chat import (  # noqa: F401
    ATTACHMENT_NAME,
    CHAT_FILE,
    CHAT_FILES,
    CHAT_OUT_FILE,
    REACTIONS,
    send_chat,
)
from remidex.loop import (  # noqa: F401
    STATUS_FILE,
    TICK,
    _SYNCED,
    chrome_session,
    main,
    sync_game,
    wait_and_watch,
)


if __name__ == "__main__":
    main()
