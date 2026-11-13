#!/usr/bin/env python3
"""RemiDex, the bot's dashboard, at http://remidex.localhost:8642 (or http://localhost:8642), so you can watch it in your browser.

Mostly read-only: it shows what the bot has saved (its state, the last BeetleBoy snapshot, item icons and the log) and
never uses your sign-in. It downloads, once each: RemiliaNET's public look (fonts, logo and background pattern), so
it can match it, and pictures of items you haven't discovered yet from the BeetleBoy Advance fan project (the game
only shows those once you've found them). What it changes (the auto/manual switches, the quest, Auto make and the
hammer settings) it saves to overrides.json for the bot to pick up. It only listens on this Mac. install_browser.sh
keeps it running.

    python3 dashboard.py    serve the dashboard (Ctrl-C to stop)
"""
import gzip
import hashlib
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import poke_browser as bot
import quests
import store
from remidex import browser, says

HOST, PORT = "127.0.0.1", 8642
NAMES = {f"remidex.localhost:{PORT}", f"localhost:{PORT}", f"127.0.0.1:{PORT}"}  # what the dashboard answers to (a .localhost name is always this computer)
ADDRESS = f"http://remidex.localhost:{PORT}"  # only answers on this Mac
ITEM_KEY = re.compile(r"^[a-z0-9_]{1,60}$")
MAX_LOCKED = 300  # items that can be switched off at once
PAGE = Path(__file__).with_name("dashboard.html")
SIGN_IN_PAGE = Path(__file__).with_name("signin.html")  # shown instead while the bot's signed out
LOG_FILE = bot.LOG_FILE
LOG_TAIL_BYTES = 600_000  # enough for today's lines without reading the whole log
LOG_LINES = 1500  # the page shows the newest 250 for whichever tab is open
JOBS = {"bot": "local.remilianet-poke-browser"}  # the background jobs the page says are running or not
# The site's public look, fetched once and kept in bot.DASH_DIR / "public".
PUBLIC = {
    "Hei.ttf": ("/fonts/Hei.ttf", "font/ttf"),
    "RemiliaPixel.ttf": ("/fonts/RemiliaPixel.ttf", "font/ttf"),
    "DS-Digital.ttf": ("/fonts/DS-DIGII.TTF", "font/ttf"),
    "logo.svg": ("/images/logos/remilianet-logo.svg", "image/svg+xml"),
    "pluses.png": ("/images/backgrounds/pluses.png", "image/png"),
    "cheeseman.png": ("/images/beetle/cheeseman.png", "image/png"),
}
ART = re.compile(r"^/art/([a-z0-9_]{1,60})$")  # (item keys are short: a long one would only make a file name too long)
CARD = re.compile(r"^/card/([a-z0-9_]{1,60})/(char|bg)$")
ACHIEVEMENT = re.compile(r"^/achievement/(\d{1,4})$")
VIEWER = re.compile(r"^/viewer/([A-Za-z0-9_]{1,40})$")
PIC = re.compile(r"^/rpic\?u=([^&]+)$")
MEDIA = re.compile(r"^/rmedia/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")  # a video from RemiliaNET's chat
MENTION = re.compile(r"^/api/mention\?q=([A-Za-z0-9_]{1,24})$")  # what's typed after a "~" in the chat box
SITE_MEDIA = re.compile(r"^/media/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
TROPHY_ICON = re.compile(r"^/trophy/(\d{1,7})$")
SHELF_TEXTURE = re.compile(r"^/trophy-shelf/([a-z0-9_]{1,40})$")
TROPHY_PATH = "/api/beetle/trophy/~"  # where the site keeps its public pictures of trophies and shelves
PIC_SOURCES = ("https://pfp.remilia.net/", bot.SITE + "/imgproxy/")  # where RemiliaNET's profile pictures come from (public)
# On every page and answer: nobody can frame the dashboard (to trick a click), and the page can load from and talk to
# only this dashboard, nothing else.
SAFE_HEADERS = (
    ("X-Frame-Options", "DENY"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
     "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
)
LIVE_FOR = 20  # a public profile read from RemiliaNET is reused this many seconds
COMPRESSED = ("application/json", "text/html", "image/svg+xml")
STREAM_LOOK = 0.4  # how often the live connection looks for a change (seconds)
NO_PICTURE_FOR = 3600  # someone whose picture couldn't be had isn't asked about again for this long
# Every item in the game, from community data (catalog.json); the game's own names and tiers win where it has them.
CATALOG = json.loads(Path(__file__).with_name("catalog.json").read_text()).get("items", {})
FAN_ART = "https://raw.githubusercontent.com/sneedragon/beetleboy-advance/HEAD/"
WIKI = "https://wiki.remilia.org/images/"  # card scenes the fan project doesn't have
EXTRA_ART = {"trophy_green": "icons/trophies/trophy_green.webp"}  # stands for "a pinned beetle" in the recipe book
NO_ART = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect x="10" y="10" width="44" height="44" rx="8" fill="none" '
          b'stroke="#9aa3ad" stroke-width="3" stroke-dasharray="6 5"/><text x="32" y="41" font-size="24" font-family="sans-serif" '
          b'text-anchor="middle" fill="#9aa3ad">?</text></svg>')  # for an item with no picture anywhere
CATEGORY_ORDER = {1: 0, 2: 1, 5: 2, 6: 3, 4: 4, 7: 5, 8: 6, 9: 7, 3: 8, 10: 9}
SWITCHES = {**{action: "beetleboy" for action in bot.BEETLE_SETTINGS}, **{action: "social" for action in bot.SOCIAL_SETTINGS},
            "junk_cubes": "crafting", "tin_hammer": "crafting"}
POKED = re.compile(r"^~([A-Za-z0-9_]+) \(")  # "~handle (friend): poked." and friend-request lines
# What a poke or friend-request line says about a friend request: one sent, theirs accepted, or ours declined.
FRIEND_EVENTS = (("accepted their friend request", "accepted"), ("accepted your friend request", "accepted_yours"),
                 ("declined our friend request", "declined"), ("friend request sent", "sent"))
# Recommended: one thing worth making in each of these (Chinese label, name, which items, or the order to go in).
RECOMMEND = [
    ("甲虫", "Beetle", lambda i: i["category"] == 2, None),
    ("花", "Flower", lambda i: i["category"] == 5 and not i["key"].startswith("pollen"), None),
    ("花粉", "Pollen", lambda i: i["key"].startswith("pollen"), None),
    ("锤子", "Hammer", None, bot.HAMMERS),  # the next upgrade
    ("特殊", "Special", lambda i: i["category"] in (6, 7), None),
    ("奖杯", "Trophy", None, ["trophy_" + t for t in quests.TRINKET_ORDER]),  # the next trophy down the trinket tree
]
REACH = 8  # about this many crafts away still counts as "within reach" for Recommended
HUNTED = 2  # for Recommended, count each thing only hunts and claims bring (like loose junk) as about two crafts' worth
_console = {"for": None}  # the craft console's plan, for one game snapshot and pick
_recommended = {"for": None, "rows": []}


def level_size_guess(level):
    """Roughly how much XP a level takes, for when there's nothing better (see level_progress)."""
    return 50 * level + 525


def fetch_once(url, local):
    """The file kept at local, downloaded from url the first time. Returns its bytes, or None if unavailable."""
    if not local.exists():
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": bot.USER_AGENT}), timeout=20) as r:
                data = r.read()
        except OSError:
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
    return local.read_bytes()


_live, _no_picture = {}, {}  # handle -> (when, their public profile or None); handle -> when their picture couldn't be had
_live_lock, _picture_turns = threading.Lock(), threading.Semaphore(2)  # two pictures fetched from RemiliaNET at a time, at most


def live_profile(handle):
    """Someone's public RemiliaNET profile ("user"), read live from the site (no sign-in needed), reused for LIVE_FOR
    seconds. None if it can't be had."""
    with _live_lock:
        hit = _live.get(handle)
    if hit and time.time() - hit[0] < LIVE_FOR:
        return hit[1]
    try:
        request = urllib.request.Request(f"{bot.SITE}/api/profile/~{urllib.parse.quote(handle, safe='')}", headers={"User-Agent": bot.USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as r:
            user = json.load(r).get("user")
    except (OSError, ValueError, AttributeError):
        user = None
    user = user if isinstance(user, dict) else None
    with _live_lock:
        _live[handle] = (time.time(), user)
    return user


def pic_source(url):
    """The full address of a RemiliaNET profile picture (profiles give some as "/imgproxy/..."), or None if it isn't one of the site's."""
    full = bot.SITE + url if isinstance(url, str) and url.startswith("/imgproxy/") else url
    return full if isinstance(full, str) and full.startswith(PIC_SOURCES) else None


def pic_link(url):
    """A RemiliaNET profile picture's address as the dashboard serves it (see remilia_picture), or None."""
    return "/rpic?u=" + urllib.parse.quote(full, safe="") if (full := pic_source(url)) else None


def remilia_picture(full):
    """A profile picture from RemiliaNET, downloaded once and kept by its address (a new picture has a new address)."""
    if not full.startswith(PIC_SOURCES):
        return None
    with _picture_turns:
        return fetch_once(full, bot.DASH_DIR / "rpic" / hashlib.sha1(full.encode()).hexdigest()[:24])


def person_picture(handle):
    """Someone's profile picture: the one the bot saved, else straight from their public profile (and then kept)."""
    local = bot.PEOPLE_PICS / handle
    if local.exists():
        return local.read_bytes()
    if time.time() - _no_picture.get(handle, 0) < NO_PICTURE_FOR:
        return None
    full = pic_source((live_profile(handle) or {}).get("pfpUrl"))
    data = remilia_picture(full) if full else None
    if data:
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
    else:
        _no_picture[handle] = time.time()
    return data


def my_profile(fresh=False):
    """(your handle, your public profile live from RemiliaNET or None). fresh: read it again now (just after a change)."""
    handle = bot.read_json(bot.STATE_FILE).get("handle")
    if fresh:
        with _live_lock:
            _live.pop(handle, None)
    return handle, (live_profile(handle) if handle else None)


def texture_name(case):
    """The shelf texture a trophy case uses, as a safe name ("graphite_slate")."""
    return re.sub(r"[^a-z0-9_]", "", str((case or {}).get("texture") or "").lower())[:40]


CHAT_STALE = 60  # seconds: an older read of the chat is shown with when it's from
# What a chat message can carry, as on RemiliaNET: pictures (4 at most) or one video, each at most this many MB.
CHAT_TYPES = {"image/jpeg": (".jpg", 10), "image/png": (".png", 10), "image/gif": (".gif", 10), "image/webp": (".webp", 10),
              "video/mp4": (".mp4", 100), "video/webm": (".webm", 100), "video/quicktime": (".mov", 100)}
CHAT_TEXT_MAX = 4000
CHAT_FILES_MAX = 40  # attachments waiting to be sent, at most
CHAT_QUEUE_MAX = 10  # messages waiting to be sent, at most
CHAT_FILE_KEPT = 86400  # an attachment never sent is thrown away after this long


def chat_view():
    """RemiliaNET's global chat as the bot last read it (it reads it every few seconds while the dashboard is open: reading
    it needs your sign-in): the newest messages, oldest first, each with who wrote it, any pictures, what it answers and
    its reactions."""
    saved = bot.read_json(bot.CHAT_FILE)
    my_id = bot.read_json(bot.DASH_DIR / "look.json").get("userId")  # (which reactions are yours)
    chat = bot.as_dict(saved.get("chat"))
    everything = bot.as_dict(chat.get("global"))
    messages, users = bot.as_dict(everything.get("messages")), bot.as_dict(everything.get("users"))

    def author(m):
        u = bot.as_dict(users.get(str(m.get("author_id"))))
        handle = u.get("handle") if isinstance(u.get("handle"), str) else None
        return {"handle": handle, "name": u.get("display_name") or handle or "SYSTEM", "color": u.get("color") if isinstance(u.get("color"), int) else None,
                "pic": pic_link(u.get("profile_pic_url"))}

    def text(m):
        return m.get("body") if isinstance(m.get("body"), str) else ""

    def reactor(user_id):  # who reacted, for the card that shows when the reaction is pointed at (as on the site)
        u = bot.as_dict(users.get(str(user_id)))
        return {"name": u.get("display_name") or u.get("handle") or "Unknown", "pic": pic_link(u.get("profile_pic_url")), "mine": user_id == my_id}

    shown = []
    for message_id in listed(chat.get("message_ids"))[-50:]:
        m = bot.as_dict(messages.get(str(message_id)))
        if not m or m.get("is_deleted") is True:
            continue
        replied = bot.as_dict(messages.get(str(m.get("reply_to_message_id")))) if m.get("reply_to_message_id") else {}
        reactions = [bot.as_dict(r) for r in listed(m.get("reactions"))]
        reactions = [{"emoji": e, "n": len(by), "mine": any(r.get("user_id") == my_id for r in by), "who": [reactor(r.get("user_id")) for r in by]}
                     for e in bot.REACTIONS if (by := [r for r in reactions if r.get("emoji") == e])]  # in the site's order
        images = [{"src": src, "w": i.get("width"), "h": i.get("height")} for i in listed(m.get("images"))
                  if isinstance(i, dict) and (src := pic_link(i.get("url")))]
        video = bot.as_dict(m.get("video"))
        clip = SITE_MEDIA.match(video.get("url")) if isinstance(video.get("url"), str) else None
        shown.append({"id": message_id, "at": (m.get("created_at") or 0) / 1000 if isinstance(m.get("created_at"), (int, float)) else 0,
                      **author(m), "text": text(m), "images": images, "reactions": reactions,
                      "video": {"src": f"/rmedia/{clip[1]}", "poster": pic_link(video.get("thumbnail_url")), "w": video.get("width"), "h": video.get("height"),
                                "animated": video.get("is_animated") is True} if clip else None,  # (a GIF comes as a looping video)
                      "reply": {"name": author(replied)["name"], "text": text(replied)[:120]} if replied else None})
    at = saved.get("at") if isinstance(saved.get("at"), (int, float)) else None
    return {"at": at, "stale": not at or time.time() - at > CHAT_STALE, "messages": shown, "outbox": chat_outbox()}


def chat_outbox():
    """What you wrote here: waiting for the bot to send it, and how sending went for the last ten minutes."""
    out, now = bot.read_json(bot.CHAT_OUT_FILE), time.time()
    waiting = [{"id": q["id"], "kind": q.get("kind") or "message", "text": q.get("text") or "", "files": len(listed(q.get("files"))),
                "message_id": q.get("message_id"), "emoji": q.get("emoji"), "on": q.get("on")}
               for q in listed(out.get("queue")) if isinstance(q, dict) and isinstance(q.get("id"), str)]
    results = {}
    for key, r in bot.as_dict(out.get("results")).items():
        if isinstance(r, dict) and isinstance(r.get("at"), (int, float)) and now - r["at"] < 600:
            stuck = r.get("sending") and now - r["at"] > 90  # the bot stopped mid-send
            results[key] = {"ok": None, "error": "the bot stopped while sending it"} if stuck else {k: r.get(k) for k in ("ok", "sending", "error")}
    return {"waiting": waiting, "results": results}


def react_request(request):
    """A reaction from the page: {"message_id", "emoji"} (one of RemiliaNET's). Added, or taken off if it's already yours,
    as on the site. Returns what to save for the bot to send, or None."""
    message_id, emoji = request.get("message_id"), request.get("emoji")
    if not (type(message_id) is int and message_id > 0 and emoji in bot.REACTIONS):
        return None
    shown = next((m for m in chat_view()["messages"] if m["id"] == message_id), None)
    mine = bool(shown) and any(r["emoji"] == emoji and r["mine"] for r in shown["reactions"])
    return {"id": secrets.token_hex(8), "kind": "react", "message_id": message_id, "emoji": emoji, "on": not mine, "asked_at": time.time()}


def chat_request(request):
    """A chat message from the page, checked: its text, its pictures (4 at most) or video (one), already attached (see
    Handler.chat_attach), and the message it answers. Returns what to save for the bot to send, or None."""
    text, files, reply_to = request.get("text", ""), request.get("attachments", []), request.get("reply_to")
    if not (isinstance(text, str) and len(text) <= CHAT_TEXT_MAX and isinstance(files, list) and (reply_to is None or type(reply_to) is int and reply_to > 0)):
        return None
    names = [f for f in files if isinstance(f, str) and bot.ATTACHMENT_NAME.match(f) and (bot.CHAT_FILES / f).is_file()]
    videos = [n for n in names if n.endswith((".mp4", ".webm", ".mov"))]
    if len(names) != len(files) or len(set(names)) != len(names) or len(names) > 4 or (videos and len(names) > 1) or not (text.strip() or names):
        return None
    return {"id": secrets.token_hex(8), "text": text.strip(), "reply_to": reply_to, "asked_at": time.time(),
            "files": [{"name": n, "label": "the video" if n in videos else "a picture"} for n in names]}


_mentions = {}  # what was typed -> (when, the people found): RemiliaNET's user search, reused for a minute


def mention_search(typed):
    """The people a ~mention could be, as the chat box on RemiliaNET finds them: its own user search (public), 6 at most."""
    hit = _mentions.get(typed.lower())
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    query = urllib.parse.urlencode({"q": typed, "limit": 6})
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{bot.SITE}/api/profile/search?{query}", headers={"User-Agent": bot.USER_AGENT, "Accept": "application/json"}), timeout=8) as r:
            found = json.load(r).get("result")
    except (OSError, ValueError, AttributeError):
        return []
    people = [{"handle": u["userHandle"], "name": u.get("displayName") or u["userHandle"], "pic": pic_link(u.get("pfpUrl"))}
              for u in listed(found) if isinstance(u, dict) and isinstance(u.get("userHandle"), str)][:6]
    if len(_mentions) > 300:
        _mentions.clear()
    _mentions[typed.lower()] = (time.time(), people)
    return people


def remilia_activity():
    """RemiliaNET's activity feed, as the feed watcher last saved it (while the dashboard is open): the newest events,
    oldest first, with everyone in them and their pictures."""
    shown = []
    for e in listed(bot.read_json(bot.REMILIA_FEED_FILE).get("events")):
        e = bot.as_dict(e)
        people = [bot.as_dict(e.get("from")), bot.as_dict(e.get("to")), *map(bot.as_dict, listed(e.get("users"))), bot.as_dict(e.get("user"))]
        people = [{"handle": p["userHandle"], "name": p.get("displayName") or p["userHandle"], "pic": pic_link(p.get("pfpUrl"))}
                  for p in people if isinstance(p.get("userHandle"), str)]
        if people and isinstance(e.get("type"), str):
            shown.append({"at": e.get("seen_at") if isinstance(e.get("seen_at"), (int, float)) else 0, "type": e["type"], "people": people})
    return {"events": shown}


def trophy_case(fresh=False):
    """Your trophy case as RemiliaNET shows it, live from your public profile: which trophy is in each of the 15 places
    (3 shelves of 5), the shelf's look, and how many you have. None if the site can't be reached. The pictures are served
    by trophy_picture and shelf_texture."""
    handle, user = my_profile(fresh)
    case = user.get("trophyCase") if user else None
    if not (user and isinstance(user.get("trophyShelves"), list) and isinstance(case, dict)):
        return None
    owned = {t["id"]: t for t in user.get("allTrophies") or [] if isinstance(t, dict) and isinstance(t.get("id"), int)}
    places = [i if isinstance(i, int) else 0 for i in user["trophyShelves"]]
    places = (places + [0] * 15)[:15]
    view = lambda i: ({"id": i, "key": owned[i].get("beetle"), "name": owned[i].get("beetle_name") or "Trophy",  # noqa: E731
                       "img": f"/trophy/{i}" if str(owned[i].get("icon")).startswith(TROPHY_PATH) else None} if i in owned else None)
    texture = texture_name(case)
    offset = lambda v: round(v * 60, 2) if isinstance(v, (int, float)) else 0  # noqa: E731  (the site's own scaling, in percent)
    return {"owner": user.get("displayName") or handle, "total": len(owned), "shelves": [[view(i) for i in places[n * 5:n * 5 + 5]] for n in range(3)], "owned": [view(i) for i in owned],
            "case": {"name": case.get("displayName") or "Trophy", "category": re.sub(r"[^a-z]", "", str(case.get("category") or "")), "texture": texture,
                     "x": offset(case.get("offsetX")), "y": offset(case.get("offsetY")), "image": f"/trophy-shelf/{texture}" if texture else None}}


def trophy_picture(trophy_id):
    """The picture of one of your trophies, as the site shows it on the shelf: downloaded once, from the site's public
    trophy pictures only. None if it isn't one of yours."""
    icon = next((t.get("icon") for t in (my_profile()[1] or {}).get("allTrophies") or [] if isinstance(t, dict) and t.get("id") == trophy_id), None)
    if not (isinstance(icon, str) and icon.startswith(TROPHY_PATH) and ".." not in icon):
        return None
    with _picture_turns:
        return fetch_once(bot.SITE + icon, bot.DASH_DIR / "trophies" / f"{trophy_id}-{hashlib.sha1(icon.encode()).hexdigest()[:10]}")


def shelf_texture(name):
    """The picture the site draws your shelf from (it's the one for the case you have now: name says which)."""
    handle, user = my_profile()
    if not handle or texture_name((user or {}).get("trophyCase")) != name:
        return None
    with _picture_turns:
        return fetch_once(f"{bot.SITE}{TROPHY_PATH}{urllib.parse.quote(handle, safe='')}/shelf", bot.DASH_DIR / "trophies" / f"shelf-{name}")


def me_live(fresh=False):
    """Your profile card, live from RemiliaNET: name, bio, picture and top 5 friends (with their pictures), and your
    friends' handles to pick top friends from. Falls back to what the bot saved a few minutes ago if the site can't
    be reached. fresh: don't reuse a profile read in the last LIVE_FOR seconds (just after a change)."""
    friends = sorted(bot.read_json(bot.STATE_FILE).get("friends") or [])
    handle, user = my_profile(fresh)
    if user:
        top = [f for f in user.get("friendsDisplayed") or [] if isinstance(f, dict) and f.get("userHandle")][:5]
        return {"live": True, "handle": handle, "name": user.get("displayName") or handle, "bio": user.get("bio") or "", "pfp": pic_link(user.get("pfpUrl")) or "/pfp", "friends": friends,
                "top": [{"handle": f["userHandle"], "name": f.get("displayName") or f["userHandle"], "pfp": pic_link(f.get("pfpUrl")) or f"/viewer/{f['userHandle']}"} for f in top]}
    saved = bot.read_json(bot.MY_PROFILE_FILE)
    pfp = "/pfp" if (bot.DASH_DIR / "pfp").exists() else ""  # none yet, on a new install, until the bot saves it
    return {"live": False, "handle": handle, "name": saved.get("name") or handle, "bio": saved.get("bio") or "", "pfp": pfp, "fetched_at": saved.get("fetched_at"), "friends": friends,
            "top": [{**f, "pfp": f"/viewer/{f['handle']}"} for f in saved.get("top_friends") or []]}


PROFILE_LIMITS = {"displayName": 24, "bio": 244, "friendsDisplayed": 5, "achievementsDisplayed": 7}  # RemiliaNET's own, from its edit forms


def profile_request(request):
    """A profile change from the page, checked against RemiliaNET's limits: any of display name, bio, top 5 friends
    (among your friends), achievements shown (among yours, up to 7, in order) and the trophy shelf (15 places, with
    trophies of yours). Returns what to save for the bot to make on RemiliaNET, or None."""
    friends = set(bot.read_json(bot.STATE_FILE).get("friends") or [])
    mine = {a.get("id") for a in bot.read_json(bot.MY_ACHIEVEMENTS_FILE).get("all") or [] if isinstance(a, dict)}
    own_trophies = lambda: {t.get("id") for t in (my_profile()[1] or {}).get("allTrophies") or [] if isinstance(t, dict)}  # noqa: E731
    checks = {"displayName": lambda v: isinstance(v, str) and 0 < len(v.strip()) <= PROFILE_LIMITS["displayName"],
              "bio": lambda v: isinstance(v, str) and len(v) <= PROFILE_LIMITS["bio"],
              "friendsDisplayed": lambda v: isinstance(v, list) and len(v) <= PROFILE_LIMITS["friendsDisplayed"] and len(set(v)) == len(v) and all(h in friends for h in v),
              "achievementsDisplayed": lambda v: isinstance(v, list) and len(v) <= PROFILE_LIMITS["achievementsDisplayed"] and len(set(v)) == len(v) and all(type(i) is int and i in mine for i in v)}
    checks["shelves"] = lambda v: (isinstance(v, list) and len(v) == 15 and all(type(i) is int for i in v) and len({i for i in v if i}) == sum(1 for i in v if i)  # 15 places, none twice,
                                   and {i for i in v if i} <= own_trophies())  # and only trophies you have
    edit = {k: request[k] for k in checks if k in request}
    if not edit or not all(checks[k](v) for k, v in edit.items()):
        return None
    if "displayName" in edit:
        edit["displayName"] = edit["displayName"].strip()
    return {**edit, "asked_at": time.time()}


def public_asset(name):
    """One of the site's public files (see PUBLIC), fetched once. None if unavailable."""
    return fetch_once(bot.SITE + PUBLIC[name][0], bot.DASH_DIR / "public" / name)


_logo = {}


def logo_fill(shape, sheet):
    """The colour one of the logo's shapes is filled with: its own, or its class's in the logo's style sheet. None if
    neither says."""
    if own := re.search(r'\bfill="(#[0-9a-fA-F]{3,8})"', shape):
        return own[1]
    names = {f".{c}" for c in (re.search(r'\bclass="([^"]*)"', shape) or ["", ""])[1].split()}
    return next((fill[1] for selectors, rules in re.findall(r"([^{}]+)\{([^}]*)\}", sheet)
                 if names & {s.strip() for s in selectors.split(",")} and (fill := re.search(r"(?<![-\w])fill:\s*(#[0-9a-fA-F]{3,8})", rules))), None)


def logo_flower():
    """RemiliaNET's flower, as it is in the site's own logo, 42 wide and 40 high: the shapes that start at its left, where
    the flower is (it's about as wide as the logo is high; the letters run on to the right), in the logo's own colours
    (its style sheet, if it has one). Whatever size the site draws its logo at. Empty if the logo can't be had. Notes the
    colours of the logo's first and last letters too, for ours (see remidex_logo)."""
    if "flower" not in _logo:
        original = (public_asset("logo.svg") or b"").decode("utf-8", "replace")
        box = re.search(r'viewBox="[-\d.]+[ ,]+[-\d.]+[ ,]+[\d.]+[ ,]+([\d.]+)"', original)
        height = float(box[1]) if box else 40.0
        paths = re.findall(r"<path[^>]*/>", original)
        shapes = [p for p in paths if (start := re.search(r'\bd="\s*[Mm]\s*(-?\d*\.?\d+)', p)) and float(start[1]) <= height * 1.05]
        letters = [p for p in paths if p not in shapes]
        style = re.search(r"<style[^>]*>(.*?)</style>", original, re.S)
        _logo["blue"] = (letters and logo_fill(letters[0], style[1] if style else "")) or "#1C2788"
        _logo["silver"] = (letters and logo_fill(letters[-1], style[1] if style else "")) or "#BEBEBE"
        _logo["flower"] = f'<svg width="42" height="40" viewBox="0 0 {height * 1.05:g} {height:g}">{style[0] if style else ""}{"".join(shapes)}</svg>' if shapes else ""
    return _logo["flower"]


def remidex_logo():
    """Our logo: the flower with "REMIDEX" beside it in the logo's blue (REMI) and silver (DEX), in place of
    "REMILIANET". None if the site's logo can't be had."""
    word = 'font-family="\'Times New Roman\', Times, serif" font-size="41" lengthAdjust="spacingAndGlyphs"'
    return (f'<svg width="150" height="40" viewBox="0 0 150 40" fill="none" xmlns="http://www.w3.org/2000/svg">{logo_flower()}'
            f'<text x="44" y="34" {word} textLength="58" fill="{_logo["blue"]}">REMI</text>'
            f'<text x="104" y="34" {word} textLength="44" fill="{_logo["silver"]}">DEX</text></svg>').encode() if logo_flower() else None


def remidex_icon():
    """The browser tab's icon: the flower on its own."""
    return f'<svg width="42" height="42" viewBox="-0.5 -1 42 42" fill="none" xmlns="http://www.w3.org/2000/svg">{logo_flower()}</svg>'.encode() if logo_flower() else None


def fan_file(path, folder, base=FAN_ART):
    """A file from the fan project (or a full URL, with base ""), fetched once into fan-art/<folder>. Returns its bytes or None."""
    return fetch_once(base + path, bot.DASH_DIR / "fan-art" / folder / path.rsplit("/", 1)[1])


def image_type(data):
    return "image/webp" if data[8:12] == b"WEBP" else "image/png" if data[:4] == b"\x89PNG" else "image/gif" if data[:3] == b"GIF" else "image/jpeg"


def card_art(key, part):
    """An item's card: its character art ("char") or the scene behind it ("bg"). The game's own, if the bot has saved
    it, else the fan project's. Returns (bytes, content type) or None."""
    own = bot.DASH_DIR / "cards" / f"{key}.{part}"
    data = own.read_bytes() if own.exists() else None
    known = CATALOG.get(key) or {}
    if not data and (path := known.get("card" if part == "char" else "card_bg")):
        data = fan_file(path, "card" if part == "char" else "card-bg")
    if not data and part == "bg" and str(known.get("bg", "")).startswith(WIKI):  # else the scene from the Remilia wiki
        data = fan_file(known["bg"], "card-bg", base="")
    return (data, image_type(data)) if data else None


def item_art(key):
    """An item's picture: the game's own icon if the bot has saved it, else the fan project's (fetched once).
    Returns (bytes, content type) or None."""
    own = bot.DASH_DIR / "icons" / f"{key}.png"
    if own.exists():
        return own.read_bytes(), "image/png"
    path = (CATALOG.get(key) or {}).get("art") or EXTRA_ART.get(key)
    data = fan_file(path, "") if path else None
    return (data, image_type(data)) if data else None


def all_items(bag, cards):
    """Every item in the game, owned or not: what the catalog lists plus anything the game has shown you."""
    keys = set(CATALOG) | set(cards) | set(bag.items)
    items = []
    for key in keys:
        known, card = CATALOG.get(key) or {}, cards.get(key) or {}
        items.append({"key": key, "name": card.get("beetle_name") or known.get("name") or key.replace("_", " ").title(),
                      "category": card.get("category") or known.get("category") or 3, "tier": card.get("rarity") or known.get("tier") or 1,
                      "count": bag.count(key), "discovered": key in cards,
                      "desc": card.get("species") or known.get("desc"), "latin": card.get("species_latin") or known.get("latin"),
                      "season": known.get("season"), "girl": card.get("girl_name"),
                      "card": bool(known.get("card") or (bot.DASH_DIR / "cards" / f"{key}.char").exists()),
                      "card_own": (bot.DASH_DIR / "cards" / f"{key}.char").exists(),
                      "art": (bot.DASH_DIR / "icons" / f"{key}.png").exists() or bool(known.get("art") or EXTRA_ART.get(key))})
    return sorted(items, key=lambda i: (i["tier"], CATEGORY_ORDER.get(i["category"], 9), i["name"]))


_jobs = {"at": 0.0, "running": {}}


def running_jobs():
    """Which background jobs have a live process (launchd's): asked at most every 10 seconds."""
    if time.time() - _jobs["at"] < 10:
        return _jobs["running"]
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    pids = {parts[2]: parts[0] for parts in (line.split("\t") for line in out.splitlines()) if len(parts) == 3}
    _jobs.update(at=time.time(), running={name: pids.get(label, "-") != "-" for name, label in JOBS.items()})
    return _jobs["running"]


QUIET = ("Chrome:", "Network:")  # hiccups the bot gets over on its own (see bot.hiccup): in the Console, but calm


def log_lines():
    """The newest log lines as (time, text), oldest first. The bot's own profile views are left out: the page doesn't show
    them (the Views tab is who viewed you), and they'd crowd out what it does show."""
    try:
        with LOG_FILE.open("rb") as f:
            f.seek(max(0, LOG_FILE.stat().st_size - LOG_TAIL_BYTES))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = []
    for raw in text.splitlines():
        if (match := store.LINE.match(raw)) and not match[2].startswith("Profile views from the activity feed"):
            lines.append((match[1], match[2]))
    return lines


_ingested = {"size": None}


def keep_history():
    """Reads anything new in the log into the history database (the bot does this too; whoever gets there first), when
    the log has changed since last time: the status asks several times, and each read takes the database's write lock."""
    try:
        size = LOG_FILE.stat().st_size
        if size != _ingested["size"]:
            store.ingest_log(LOG_FILE)
            _ingested["size"] = size
    except (sqlite3.Error, OSError):
        pass


def all_time():
    """Every poke, friend request, hunt and craft the bot has logged since it started: the game doesn't report totals.
    Kept in the history database (see store.py), which reads the log once, in order."""
    keep_history()
    try:
        return store.totals()
    except sqlite3.Error:
        return dict.fromkeys(store.COUNTERS, 0)


def kind_of(text):
    """Which part of the bot a log line is about, for the dashboard's filters."""
    # What happens inside a craft (an unlucky smash, a shattered hammer, NOTHING HAPPENED) is crafting; "problem" (the Console tab)
    # is the app's own trouble: errors, a paused bot, refused requests.
    if text.startswith("BeetleCraft") and re.search(r"NOTHING HAPPENED|shattered|smash failed", text):
        return "craft"
    if re.search(r"Error|Paused|couldn't|refused|failed with HTTP|failed \((?!sacrificing)", text) or text.startswith(("feed:", *QUIET)):
        return "problem"
    if text.startswith(("BeetleCraft", "Shelf:")):
        return "craft"
    if text.startswith("Cheeseman"):
        return "cheeseman"  # his own tab: what he hands out, and what he says about it
    if text.startswith(("Hunt:", "Free beetle", "Daily cheese", "BeetleBoy", "Level up")):
        return "beetle"
    if text.startswith("~") and ": poked" not in text and friend_of(text):
        return "friend"  # a friend request with pokes on manual
    return "poke"


def log_entries(lines, pics):
    """The activity feed's entries, oldest first: each log line as {at, ts, text, kind}, with who it's about for pokes and
    friend requests, and "say": what RemiliaNET's own screens say about it (the catch and hunt screen, the Cheeseman, the
    craft screen). A craft the bot made has its exact words in the lines that follow it in the log (says.SCREEN); those
    become part of its entry, not entries of their own. Older ones are worked out from the line itself (see says.from_log)."""
    entries, exact = [], set()
    for when, text in lines:
        if text.startswith(says.SCREEN):
            mine = next((e for e in reversed(entries[-6:]) if e["text"].startswith(("BeetleCraft: Assemble", "BeetleCraft: Smash")) and abs(e["ts"] - store.log_time(when)) <= 10), None)
            if mine is not None:
                if id(mine) not in exact:
                    exact.add(id(mine))
                    mine["say"] = []
                mine["say"].append(text[len(says.SCREEN):])
            continue
        entry = {"at": when, "ts": store.log_time(when), "text": text, "kind": kind_of(text), **({"quiet": True} if text.startswith(QUIET) else {})}
        if (who := POKED.match(text)):
            entry.update(who=who[1], pic=who[1] in pics, friend=friend_of(text))
        if (say := says.from_log(text)):
            entry["say"] = say
        entries.append(entry)
    return entries


def today_counts():
    """Today's numbers from the history database (see store.ingest_log and store.record_views)."""
    try:
        return store.day_counts(["crafts", "junk_cubes", "hunts", "viewed_you"])
    except sqlite3.Error:
        return {}


def older_cheeseman(window, pics):
    """The Cheeseman's lines from before the feed's window of the log, kept in the history database, for his tab."""
    keep_history()
    try:
        return log_entries(store.cheeseman(before=window[0][0] if window else None), pics)
    except sqlite3.Error:
        return []


def friend_of(text):
    """"sent", "accepted" (theirs), "accepted_yours" or "declined" for a line about a friend request (see FRIEND_EVENTS), else None."""
    return next((what for words, what in FRIEND_EVENTS if words in text), None) if text.startswith("~") else None


WATCHED = 300  # XP: a level the bot saw begin within this is known closely
RECENT_LEVELS = 8  # how many of the latest levels' sizes the trend goes by


def watched_starts(seen):
    """Where each level began, for the levels the bot saw begin closely: {level: (XP, how far off it could be)}. A level
    starts above the last XP seen at the level before, and at or below the first seen at it."""
    return {k: ((seen[k - 1][1] + low) / 2, (low - seen[k - 1][1]) / 2) for k, (low, _) in seen.items()
            if k - 1 in seen and 0 <= low - seen[k - 1][1] <= WATCHED}


def level_sizes(starts):
    """How much XP a level takes, as the game doesn't say: a straight line through the sizes of the levels between those the
    bot saw begin (they grow steadily, more at higher levels: so the most recent ones). (per level, at level 0, how far
    off one level's size is typically), or None with fewer than two such levels."""
    known = sorted(starts)
    points = [(m, (starts[j][0] - starts[i][0]) / (j - i)) for i, j in zip(known, known[1:]) for m in range(i, j)][-RECENT_LEVELS:]
    if len({m for m, _ in points}) < 2:
        return None
    n, mx, my = len(points), sum(m for m, _ in points) / len(points), sum(v for _, v in points) / len(points)
    slope = sum((m - mx) * (v - my) for m, v in points) / sum((m - mx) ** 2 for m, _ in points)
    off = (sum((v - (my + slope * (m - mx))) ** 2 for m, v in points) / n) ** .5
    return slope, my - slope * mx, off


def level_progress(level, xp):
    """For the EXP bar. The game reports only your level and total XP. When the bot watched this level begin ("watched"),
    the bar starts there. When it came in one big jump (a daily claim gives a couple of thousand XP), where it began is
    worked out from the last level it watched begin and the sizes of the levels since (see level_sizes), kept within what
    it does know. "slack": how far off that could be. How big the level is is always an estimate. "known" is False only
    when there's nothing to go on."""
    seen = {int(k): v for k, v in bot.read_json(bot.LEVELS_FILE).get("seen", {}).items()
            if k.isdigit() and isinstance(v, list) and len(v) == 2 and all(isinstance(n, (int, float)) for n in v)}
    starts, after = watched_starts(seen), seen[level - 1][1] if level - 1 in seen else None  # it began above after...
    upto = min(seen[level][0], xp) if level in seen else xp  # ...and at or below this
    fit, below = level_sizes(starts), max((k for k in starts if k < level), default=None)
    size_of = (lambda m: max(1, fit[0] * m + fit[1])) if fit else None
    if level in starts:
        start, slack = starts[level]
    elif fit and below is not None:  # the last level it watched begin, and the levels since
        start = starts[below][0] + sum(size_of(m) for m in range(below, level))
        slack = fit[2] * (level - below)
        if after is not None:
            start, slack = min(max(start, after + 1), upto), min(slack, (upto - after) / 2)
    elif after is not None:
        start, slack = (after + upto) / 2, (upto - after) / 2
    else:
        return {"level": level, "xp": xp, "known": False}
    sizes = [starts[k + 1][0] - starts[k][0] for k in sorted(starts) if k + 1 in starts]  # whole levels it watched
    size = round(size_of(level)) if fit else round(sizes[-1]) if sizes else level_size_guess(level)
    if xp - start >= size:  # (you're still on this level, so it's longer than that, or began later: some of each)
        over = xp - start - size + 1
        start, size = start + over / 2, size + round(over / 2) + 1
    return {"level": level, "xp": xp, "into": round(min(max(xp - start, 0), size)), "size": size, "slack": round(slack),
            "known": True, "watched": level in starts}


def one_of_a_kind_owned(item, bag, slot3):
    """Things you can only have one of, already yours: a hammer of that tier, a trophy, the key once slot 3 is open."""
    key = item["key"]
    return (item["category"] == 4 and (bag.count(key) > 0 or key in bag.hammers())
            or item["category"] == 9 and (item["discovered"] or item["count"] > 0)
            or key == bot.KEY and (slot3 or item["count"] > 0))


def recommended(bag, slot3, items, fetched_at):
    """One thing worth making in each category, with the craft that starts it or what it's waiting on. Hammers and
    trophies: the next one up. Others: the rarest you haven't found that's a handful of crafts away from what you have,
    else the nearest. Worked out once per game snapshot."""
    if _recommended["for"] == fetched_at:
        return _recommended["rows"]
    book, catalog = bot.recipe_book()
    by_key, rows, memo = {i["key"]: i for i in items}, [], {}
    for zh, group, wanted, order in RECOMMEND if book else []:
        best = None
        candidates = [by_key[k] for k in order if k in by_key] if order else [i for i in items if wanted(i)]
        for n, item in enumerate(candidates):
            if one_of_a_kind_owned(item, bag, slot3) or not bot.recipes_for(item["key"], book, catalog)[0]:
                continue
            crafts = bot.craft_cost(item["key"], bag, book, catalog, slot3, item["count"] + 1, memo=memo, basic=HUNTED)
            found, near = item["discovered"] or item["count"] > 0, crafts is not None and crafts <= REACH
            rank = (n,) if order else (found, crafts is None, not near, item["count"] > 0,  # within reach: the rarest first; further off: the nearest
                                       -item["tier"] if near else crafts or 0, (crafts or 0) if near else -item["tier"], item["name"])
            if best is None or rank < best[0]:
                best = (rank, item, crafts)
        if best:
            _, item, crafts = best
            plan = bot.Planner(bag, book, catalog, slot3, protect=[item["key"]], hunted=HUNTED)  # the way Auto make would go about it
            plan.obtain(item["key"], new=True)
            stuck = {spec for s in plan.steps for spec, f in zip(s["items"], s["from"]) if f == "wait"}  # what hunts and claims must bring first
            first = None if stuck else next(plan.ready(), None)
            rows.append({"zh": zh, "group": group, "key": item["key"], "name": item["name"], "tier": item["tier"], "count": item["count"],
                         "new": not (item["discovered"] or item["count"] > 0), "crafts": crafts,
                         "next": bot.describe_craft(bag, first[1], first[2], None)[0] if first else None,
                         "needs": sorted(re.sub(r"^(Any|An?) ", "", bot.label_of(k, bag, book)) for k in stuck)})
    _recommended.update({"for": fetched_at, "rows": rows})
    return rows


PLAN_STEPS = 400  # planned steps sent to the page at most


def step_view(s):
    """A planned step (see bot.Planner) as the page shows it."""
    return {"recipe": s["recipe"], "label": s["label"], "mode": "smash" if s["mode"] == bot.SMASH else "assemble", "in": s["items"], "from": s["from"], "out": s["out"],
            "tries": round(s["tries"], 1) if s["tries"] > 1.05 else None, "low": s["low"]}


def held_plans(steps, bag, slot3, book, catalog):
    """For the plan's recipe tree to show the whole recipe, not just what's left: how each thing the plan takes from what
    you hold is made, the planner's own way from nothing ({key: steps}). Those parts are done. What hunts and claims
    bring (loose junk, Green Beetles) has none."""
    empty, out = bot.Bag({**bag.game, "inventory": {}}, bag.cards), {}
    for key in sorted({k for s in steps if not s.get("unlock") for k, f in zip(s["items"], s["from"]) if f is None}):
        made = bot.Planner(empty, book, catalog, slot3)
        made.obtain(key)
        if made.steps:
            out[key] = [step_view(s) for s in made.steps]
    return out


def craft_console(bag, slot3, settings, state, step, titles):
    """The craft console and plan: the Auto make or quest being worked on, the crafts done toward it, the one the bot
    does next, and the whole plan (each step with where its items come from: what you hold, an earlier step or hunts),
    with how each thing you hold for it is made. Worked out once per game snapshot and pick."""
    book, catalog = bot.recipe_book()
    focus, _ = bot.craft_focus(bag, slot3, settings, book)
    kind = focus if focus in ("make", "goal") else "make" if bot.active_make(bag, settings, book) else "goal" if focus == "paused" else None
    if not kind:
        return None
    task = bot.task_id(kind, settings)
    record = bot.task_record(state, task) or (bot.task_record(state, bot.finished_key(task, settings["make"])) if kind == "make" and task else {})  # (a finished one: put away)
    name = lambda k: bot.label_of(k, bag, book)  # noqa: E731
    spare = bot.usable(bag, settings)  # the plan only uses what isn't switched off
    now = None
    if step and focus == kind:
        label, mode, items, hammer, sacrifice, aim, source = step
        what, how = bot.describe_craft(bag, mode, items, hammer, sacrifice)
        now = {"what": what, "out": name(aim), "how": how.strip(" ()"), "side": bool(bot.side_craft(settings, label)),
               "waiting": source == 2}  # (made for a waiting step: see bot.ready_crafts)
    key = json.dumps([bag.items, slot3, task, settings.get("make"), settings.get("goal_again"), step and [*step[:3], step[6]], bot.craft_stats() and bot._STATS["at"], bot._REMOVED["at"],
                      settings.get("locked"), settings.get("sacrifice"), settings.get("tin_hammer"), int(time.time() // 600)], sort_keys=True, default=str)
    if _console["for"] != key:
        build = (lambda hunted: bot.plan_make(spare, slot3, settings["make"], book, catalog, hunted, sacrifice=bot.sacrifice_kept(settings),
                                                  forge=settings["tin_hammer"])) if kind == "make" else \
            (lambda hunted: bot.plan_goal(bag, slot3, settings, book, catalog, hunted))
        plan = build(bot.HUNTED_COST)
        steps, mark = [], step if now and not now["side"] else None
        for s in plan.steps:
            if s.get("unlock"):
                steps.append({"unlock": True})
                continue
            here = bool(mark and s["label"] == mark[0] and s["items"] == mark[2])
            mark = None if here else mark
            steps.append({**step_view(s), "now": here, "long": bot.long_shot(book["recipes"][s["recipe"]])})  # (done after the sure steps)
        _console.update({"for": key, "steps": steps[:PLAN_STEPS], "more": max(0, len(steps) - PLAN_STEPS), "held": held_plans(plan.steps, spare, slot3, book, catalog)})
    return {"task": kind, "title": titles[kind], "paused": focus != kind, "done": aimed(record.get("done", []), bag, book, catalog), "count": record.get("count", 0), "made": bot.made_count(record) if record else 0,
            "now": now, "steps": _console["steps"], "more": _console["more"], "held": _console["held"]}


_AIMS = {}  # an older failed smash's words -> what its recipe makes


def aimed(done, bag, book, catalog):
    """A task's crafts as recorded (see bot.note_craft). Older ones didn't keep what they aimed for, so a failed smash among
    them gets what its recipe makes, worked out from the items it used (what the plan wanted from it wasn't kept)."""
    keys, out = None, []
    for entry in done:
        if isinstance(entry, list) and len(entry) == 3 and entry[2] is None and isinstance(entry[1], str) and entry[1].startswith("Smash "):
            if entry[1] not in _AIMS:
                keys = keys or bag.keys_by_name()
                items = [keys.get(n) for n in entry[1][len("Smash "):].split(" + ")]
                fit = None not in items and bot.craft_specs("smash", items, None, book, catalog)
                _AIMS[entry[1]] = " or ".join(bot.label_of(k, bag, book) for k in fit[0]["out"]) if fit else None
            entry = [*entry, _AIMS[entry[1]], None]
        out.append(entry)
    return out


def recipe_check():
    """What your own crafts on remilia.net have shown about each recipe in the book, by its index: "works" (it made
    something it makes) or "real" (the game knows it, but it hasn't worked yet: unlucky smashes, or something else came
    out). Recipes not tried yet aren't listed, and ones the game said don't exist are gone from the book."""
    book, stats, check = bot.recipe_book()[0], bot.craft_stats(), {}
    for index, r in enumerate(book["recipes"] if book else []):
        if seen := stats.get(bot.recipe_label(r)):
            worked, came_out = bot.times_worked(r, seen), sum(seen["outs"].values())
            check[index] = {"state": "works" if worked else "real", "tries": seen["n"], "worked": worked,
                            "unlucky": seen["n"] - came_out, "other": came_out - worked}
    return check


ACHIEVEMENT_ART = "https://www.remilia.net/images/achievements/pfp-{:03d}.png"  # public, like the site's fonts
# What a locked achievement asks for, where the dashboard can tell how far along you are.
ACHIEVEMENT_PROGRESS = [(re.compile(r"Accrue (\d+) friends"), "friends"), (re.compile(r"Cheese claim streak of (\d+) days"), "streak")]


def remilia_achievements():
    """Your RemiliaNET achievements (saved by the bot from your profile), in the order you picked to show them, then
    the rest; and every other achievement the bot has seen on anyone's profile, which you don't have yet, with how far
    along you are where that can be told (friends, and the daily cheese streak)."""
    mine = bot.read_json(bot.MY_ACHIEVEMENTS_FILE)
    if not mine:
        return None
    owned, shown = {a["id"]: a for a in mine.get("all") or [] if isinstance(a, dict) and "id" in a}, mine.get("shown") or []
    keep_history()
    progress = {"friends": mine.get("friends"), "streak": store.streak()}
    order = [i for i in shown if i in owned] + sorted((i for i in owned if i not in shown), key=lambda i: owned[i].get("grantedAt") or "", reverse=True)
    locked = []
    for key, a in (bot.read_json(bot.ACHIEVEMENT_LIST_FILE).get("achievements") or {}).items():
        if not key.isdigit() or int(key) in owned:
            continue
        have = need = None
        for pattern, what in ACHIEVEMENT_PROGRESS:
            if (m := pattern.search(a.get("description") or "")) and progress.get(what) is not None:
                have, need = progress[what], int(m[1])
        locked.append({**a, "have": have, "need": need})
    locked.sort(key=lambda a: (-(a.get("season") or 0), -(a.get("ownershipPercentage") or 0)))
    return {"mine": [{**owned[i], "shown": i in shown} for i in order], "locked": locked}


def settings_view(settings):
    """The settings the page shows and changes."""
    return {"beetleboy": settings["beetleboy"], **{k: settings[k] for k in ("pokes", "friend_requests", "view_profiles", "junk_cubes", "tin_hammer")},
            "hammer_timing": settings["hammer_timing"], "locked": settings["locked"], "sacrifice": settings["sacrifice"],
            "steps": {k: v for k, v in step_picks_now(settings).items() if k != "task"}}


def step_picks_now(settings):
    """What's picked for the steps of the Auto make or quest they were picked for (see bot.step_picks)."""
    picks = bot.step_picks("make", settings)
    return picks if picks["task"] else bot.step_picks("goal", settings)


def hammer_rack(bag, cards, settings):
    """All five hammers in the order the bot uses them, with their limits, and wear for the ones you own."""
    stats, owned, rack = bag.hammer_stats(), bag.hammers(), []
    for key in settings["hammer_order"]:
        known, st = CATALOG.get(key) or {}, stats.get(key) or {}
        rack.append({"key": key, "name": (cards.get(key) or {}).get("beetle_name") or known.get("name") or key,
                     "tier": (cards.get(key) or {}).get("rarity") or known.get("tier") or 2, "owned": key in owned,
                     "wear": owned[key][1] if key in owned else None, "limit": settings["hammer_limits"][key], "smash": key not in settings["hammer_off"],
                     "bonus": st.get("craft_bonus")})
    return rack


_next = {"for": None, "step": None, "wait": None}  # the craft the bot does next (or why it waits), for one game snapshot and pick
_status_cache = {"at": 0.0, "body": b""}
_status_lock = threading.Lock()


SIGN_IN_TIMEOUT = 150 + browser.CODE_WAIT  # the most a sign-in from the dashboard takes (it's usually half a minute, plus typing a code)
CODE = re.compile(r"\d{4,10}")  # a two-factor code (RemiliaNET's are 6 digits)
_sign_in = {"running": False, "step": None, "error": None, "attempt": None, "stopped": False, "proc": None, "at": 0.0}  # the last one
_sign_in_lock = threading.Lock()


def run_sign_in(email, password):
    """Signs the bot in with the email and password from the sign-in page: the bot's own --sign-in, given them on its
    input (other programs on this computer can read a command line, not that), and then a two-factor code if RemiliaNET
    wants one (see send_code). Keeps how it's going for the page, and passes the bot's own log lines to its log."""
    error, timed_out = "RemiDex couldn't finish signing in. Try again", []
    try:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("poke_browser.py")), "--sign-in"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    except OSError:
        proc = None
    if proc:
        watchdog = threading.Timer(SIGN_IN_TIMEOUT, lambda: (timed_out.append(True), proc.kill()))
        watchdog.start()
        with _sign_in_lock:
            _sign_in["proc"] = proc
        result = {}
        try:
            proc.stdin.write(json.dumps({"email": email, "password": password}) + "\n")
            proc.stdin.flush()
            for line in proc.stdout:
                line = line.strip()
                try:
                    said = json.loads(line) if line.startswith("{") else None
                except ValueError:
                    said = None
                if not isinstance(said, dict):
                    if line:
                        with LOG_FILE.open("a") as log:  # (the activity feed shows the bot's log)
                            log.write(line + "\n")
                elif said.get("step") == "code":
                    with _sign_in_lock:
                        if not _sign_in["stopped"]:  # (a "start over" already sent stops it at this question)
                            _sign_in.update(step="code", error=said.get("error"), at=time.time())
                elif "ok" in said:
                    result = said
            proc.wait(timeout=30)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            proc.kill()
        watchdog.cancel()
        error = None if result.get("ok") is True else str(result.get("error") or ("Signing in took too long. Try again" if timed_out else error))[:300]
    with _sign_in_lock:
        _sign_in.update(running=False, step=None, proc=None, error=None if _sign_in["stopped"] else error, at=time.time())
    _status_cache["at"] = 0


def tell_sign_in(message):
    """Sends the running sign-in a line ({"code"} or {"stop": true}). Returns whether it could."""
    proc = _sign_in["proc"]
    try:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        return True
    except (AttributeError, OSError, ValueError):
        return False


def sign_in_view():
    """GET /api/sign-in, for the sign-in page: whether the bot's signed in; how a sign-in from it is going ("step": "code"
    while it waits for your two-factor code, "checking" once sent; "attempt" says which sign-in, so a page only shows its
    own errors); and your profile's colour (for the page's colour theme)."""
    hue = bot.read_json(bot.DASH_DIR / "look.json").get("color")
    return {"signed_in": not browser.signed_out(), "running": _sign_in["running"], "step": _sign_in["step"], "error": _sign_in["error"],
            "attempt": _sign_in["attempt"], "hue": hue if isinstance(hue, (int, float)) else 204}


def status_body():
    """The status as JSON, worked out at most once a second (so a few open tabs, or a burst of changes, don't each cost
    a full rebuild); callers wait for the one being built."""
    with _status_lock:
        if time.time() - _status_cache["at"] >= 1.0:
            _status_cache.update(body=json.dumps(status()).encode(), at=time.time())
        return _status_cache["body"]


def live_version():
    """A mark of everything the dashboard shows (the log, the game, settings, who's poked you...): it changes whenever any
    of that does, so the page can update the moment something happens."""
    files = [LOG_FILE, bot.GAME_FILE, bot.STATE_FILE, bot.OVERRIDES_FILE, bot.STATUS_FILE, bot.CRAFT_NOW_FILE, bot.FEED_US_FILE, bot.CRAFT_STATS_FILE, bot.REMOVED_FILE,
             bot.PROFILE_EDIT_FILE, bot.MY_ACHIEVEMENTS_FILE, bot.DASH_DIR / "social.json", bot.DASH_DIR / "views.json", bot.DASH_DIR / "quests.json"]
    return tuple(bot.stamp(f) for f in files)


def with_live_pokes(social, pics):
    """Adds pokes to you the activity feed has shown in the last half hour, which RemiliaNET's notifications don't have
    yet (they're read every few minutes): shown at once, and left out once the notification arrives."""
    now, have = time.time(), {}
    for n in social:
        if n["type"] == "poke":
            have[n["handle"]] = max(have.get(n["handle"], 0), n["at"] or 0)
    extra = []
    for e in bot.read_json(bot.FEED_US_FILE).get("events") or []:
        who, at = e.get("from") or {}, e.get("seen_at") or 0
        handle = who.get("userHandle")
        if e.get("type") != "poke" or not handle or now - at > 1800 or have.get(handle, 0) >= at - 60:
            continue
        have[handle] = at
        extra.append({"id": f"feed-{e.get('id')}", "type": "poke", "handle": handle, "name": who.get("displayName") or handle, "count": 1, "at": at, "state": "unread",
                      "mutual": None, "pic": handle in pics})
    return social + extra


_activity = {"for": None, "parts": {}}


def activity(pics):
    """The activity feed's entries and the Cheeseman's older lines (see log_entries and older_cheeseman), read again only
    when the log or the saved pictures of people change."""
    key = (bot.stamp(LOG_FILE), len(pics))
    if _activity["for"] != key:
        lines = log_lines()[-LOG_LINES:]
        _activity.update({"for": key, "parts": {"log": log_entries(lines, pics), "older_cheeseman": older_cheeseman(lines, pics)}})
    return _activity["parts"]


def status():
    now = time.time()
    state = bot.read_json(bot.STATE_FILE)
    settings = bot.load_settings()

    look = bot.read_json(bot.DASH_DIR / "look.json")
    pics = set(os.listdir(bot.PEOPLE_PICS)) if bot.PEOPLE_PICS.is_dir() else set()
    saved_views = bot.read_json(bot.DASH_DIR / "views.json")
    viewers = [{"id": v.get("id") or f"{v['handle']} {v.get('at')}", **{k: v.get(k) for k in ("handle", "name", "count", "at")},
                "pic": v["handle"] in pics}
               for v in saved_views.get("notes") or [] if isinstance(v, dict) and v.get("handle")]
    result = {
        "now": now,
        "look": {"hue": look.get("color") if isinstance(look.get("color"), (int, float)) else 204,
                 "name": look.get("displayName"), "pfp": (bot.DASH_DIR / "pfp").exists()},
        "jobs": running_jobs(),
        "needs_login": bool(state.get("needs_login")) or not browser.COOKIES_FILE.exists(),
        "handle": state.get("handle"),
        "pokes": {"next_at": state.get("next_slot_at"), "friends": len(state.get("friends") or [])},
        "today": today_counts(),
        "totals": all_time(),
        "settings": settings_view(settings),
        **activity(pics),
        "names": {v["name"]: k for k, v in CATALOG.items()},  # for icons in the activity log; the game's own names are added below
        "viewers": viewers,
        "achievements": remilia_achievements(),
        "profile_edit": {k: v for k, v in bot.read_json(bot.PROFILE_EDIT_FILE).items() if k in ("asked_at", "done_at", "ok", "error", "field")} or None,
        "social": with_live_pokes([{**{k: n.get(k) for k in ("id", "type", "handle", "name", "count", "at", "state", "mutual")}, "pic": n["handle"] in pics}
                                   for n in bot.read_json(bot.DASH_DIR / "social.json").get("notes", []) if isinstance(n, dict) and n.get("handle") and n.get("id")], pics),
        "bot": bot.read_json(bot.STATUS_FILE) or None,  # what the bot says it's doing and what's next (see report in remidex/loop.py)
        "crafting": bot.read_json(bot.CRAFT_NOW_FILE) or None,  # what crafting is doing this moment (see crafting_now in remidex/craft.py)
        "craft_check_at": state.get("craft_check_at"),  # when the bot next looks at crafting
    }

    saved = bot.read_json(bot.GAME_FILE)
    game, fetched = saved.get("game"), saved.get("fetched_at")
    if isinstance(game, dict):
        bag = bot.Bag(game, saved.get("cards"))
        cards = bag.cards  # (only real cards: see Bag)
        number = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # noqa: E731
        cooldowns = game.get("cooldowns") if isinstance(game.get("cooldowns"), dict) else {}
        timer = lambda action: fetched + cooldowns[action] / 1000 if number(fetched) and number(cooldowns.get(action)) else None  # noqa: E731
        used = number(game.get("beetleHuntsUsed")) or 0
        unlock = (number(game.get("lastBeetleHuntDate")) or 0) / 1000 + bot.HUNT_RECHARGE if used >= bot.HUNTS_PER_CYCLE else None
        slot3 = bool(game.get("crafting_slot3_unlocked"))
        off = state.get("craft_off") if isinstance(state.get("craft_off"), dict) else {}
        key = json.dumps([fetched, game.get("hammers"), bag.items, slot3, settings, off, bot.craft_stats() and bot._STATS["at"], bot._REMOVED["at"],
                          int(now // 60)], sort_keys=True, default=str)
        if _next["for"] != key:  # (planning it is most of the work: only again when something it depends on changes)
            step = bot.next_craft(bag, slot3, settings, off)
            _next.update({"for": key, "step": step, "wait": bot.craft_wait(bag, slot3, settings, off) if not step else None})
        step = _next["step"]
        result["game"] = {
            "level": game.get("level"), "xp": game.get("xp"),
            "xp_bar": level_progress(game["level"], game["xp"]) if isinstance(game.get("level"), int) and isinstance(game.get("xp"), int) else None,
            "cheese": bag.count("cheese"), "beetles": game.get("beetle_count"), "streak": store.streak(),
            "slot3": slot3,
            "timers": {"catchBeetle": timer("catchBeetle"), "claimUBC": timer("claimUBC"), "junkFaucet": timer("junkFaucet")},
            "hunts": {"used": used, "unlock_at": unlock if unlock and unlock > now else None},
            "items": all_items(bag, cards),
            "rack": hammer_rack(bag, cards, settings),
            "next_craft": " ".join(bot.describe_craft(bag, *step[1:5])).replace(" (", "\n(") if step else None,
            "wait": _next["wait"],
            "tin_made": (bot.craft_stats().get(bot.TIN_HAMMER_CRAFT) or {}).get("n", 0),  # Tin Hammers made, by the bot and by you on the site
            "craft_off": sorted(label for label, until in off.items() if number(until) and now < until < now + bot.FOR_GOOD / 2),  # not the removed ones
            "discovered": len(cards),
        }
        book = bot.recipe_book()[0]
        result["names"].update((n, k) for n, k in bag.keys_by_name().items() if n)
        items = result["game"]["items"]
        rows, marks = quests.craftable(book, items, game) if book else ([], set())
        result["game"]["craftable"] = rows
        result["game"]["recommended"] = recommended(bot.usable(bag, settings), slot3, items, (fetched, tuple(settings["locked"]), tuple(settings["hammer_off"])))
        result["recipe_check"] = recipe_check()
        result["recipes_removed"] = len(bot.read_json(bot.REMOVED_FILE).get("recipes") or {})  # the page reloads the book when this changes
        for item in items:
            item["can"] = item["key"] in marks
            item["locked"] = item["key"] in settings["locked"]
            item["only_one"] = one_of_a_kind_owned(item, bag, slot3)  # you can't make another
        done_file = bot.DASH_DIR / "quests.json"
        done = bot.read_json(done_file).get("done", {})
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        viewers_today = len({v["handle"] for v in viewers if (v["at"] or 0) >= midnight})
        result["quests"], changed = quests.check(items, game, state, viewers_today, done, {**result["totals"], "streak": result["game"]["streak"]})
        found = {i["key"] for i in items if i["discovered"] or i["count"] > 0}
        owned = {i["key"] for i in items if i["count"] > 0}
        beetles = {i["key"] for i in items if i["category"] == 2}
        for q in result["quests"]:
            q["workable"] = q["id"] in quests.GOALS
            q["completed"] = bool(q["done_at"]) and quests.completed(q["id"], found, owned, slot3, beetles) is True
        make = settings.get("make")
        if make and book:
            result["make"] = {"keys": make["keys"], "recipe": make.get("recipe"), "title": bot.make_title(make["keys"], bag, book),
                              "done": bot.make_finished(bag, make, book), "paused": bool(make.get("paused"))}
        overrides = bot.read_json(bot.OVERRIDES_FILE)
        titles = {q["id"]: q["title"] for q in result["quests"]}
        if book:
            result["make_queue"] = [{"keys": m["keys"], "recipe": m.get("recipe"), "title": bot.make_title(m["keys"], bag, book)}
                                    for m in listed(overrides.get("make_queue")) if isinstance(m, dict) and isinstance(m.get("keys"), list) and m["keys"]]
        result["goal_queue"] = [{"id": g["quest"], "again": g.get("again"), "title": titles.get(g["quest"], g["quest"])}
                                for g in listed(overrides.get("goal_queue")) if isinstance(g, dict) and isinstance(g.get("quest"), str)]
        goal = settings.get("goal")
        if goal in quests.GOALS:
            title = next((q["title"] for q in result["quests"] if q["id"] == goal), goal)
            result["goal"] = {"id": goal, "title": title, "again": settings.get("goal_again"),
                              "left": len(quests.goal_needs(goal, found, owned, slot3, beetles, settings.get("goal_again"))),
                              "paused": settings.get("goal_paused"),  # pending: waiting while an Auto make goes first
                              "pending": bool(result.get("make")) and not result["make"]["done"] and not result["make"]["paused"]}
        if book:
            titles = {kind: (result.get(kind) or {}).get("title") for kind in ("make", "goal")}
            result["console"] = craft_console(bag, slot3, settings, state, step, titles)
        if changed:
            bot.write_json(done_file, {"done": done})
    return result


def listed(value):
    """value if it's a list, else none: for what's saved in files, which may be from an older version or cut short."""
    return value if isinstance(value, list) else []


def make_request(keys, recipe):
    """An Auto make from the page, checked: what to make (1-3 item keys or "@group"s a recipe makes) and, optionally,
    the recipe to make it by. Returns what to save, with how many you hold now (it's made once you hold more), or None."""
    book, catalog = bot.recipe_book()
    saved = bot.read_json(bot.GAME_FILE)
    if not book or not isinstance(saved.get("game"), dict) or not isinstance(keys, list) or not 1 <= len(keys) <= 3:
        return None
    if recipe is not None and not (type(recipe) is int and 0 <= recipe < len(book["recipes"]) and not book["recipes"][recipe].get("gamble")):
        return None
    made = {o for r in book["recipes"] if not r.get("gamble") for o in r["out"]}
    for key in keys:
        groups = [o[1:] for o in made if o.startswith("@")] if recipe is None else [o[1:] for o in book["recipes"][recipe]["out"] if o.startswith("@")]
        outputs = made if recipe is None else set(book["recipes"][recipe]["out"])
        if not isinstance(key, str) or not (key in outputs or any(bot.in_group(key, g, book, catalog) for g in groups)):
            return None
    return bot.new_make(keys, recipe, bot.Bag(saved["game"], saved.get("cards") or {}), book)


MAX_PICKED = 40  # steps with something picked, at most


def pick_for_step(overrides, request):
    """Picks something for a step of the Auto make or quest being worked on, from the step's options. Steps are known by
    their recipe ("label"), and what's picked lasts while that Auto make or quest does. One of: "skip": true (done last)
    or false (back in order); "first": true (worked on first, with the steps it needs; one step at a time) or false; and
    for a smash, "sacrifice": "green" or "purple", or "hammer": one of yours, either null for the bot's own choice.
    Returns False if the request doesn't make sense."""
    task, label = request.get("task"), request.get("label")
    tid = bot.task_id(task, bot.load_settings()) if task in ("make", "goal") else None
    recipe = bot.recipe_named(label) if isinstance(label, str) else None
    if not (tid and recipe):
        return False
    kept = bot.valid_steps(overrides.get("steps"))
    picks = kept if kept and kept["task"] == tid else {"task": tid, "skipped": [], "first": None, "sacrifice": {}, "hammer": {}}
    smash, choice = recipe["mode"] == "smash", lambda key, allowed: key in request and request[key] in (None, *allowed)  # noqa: E731
    if isinstance(request.get("skip"), bool):
        picks["skipped"] = [k for k in picks["skipped"] if k != label] + ([label] if request["skip"] else [])
        picks["first"] = None if request["skip"] and picks["first"] == label else picks["first"]
    elif isinstance(request.get("first"), bool):
        picks["first"] = label if request["first"] else None if picks["first"] == label else picks["first"]
        picks["skipped"] = [k for k in picks["skipped"] if not (request["first"] and k == label)]  # (worked on first: not skipped)
    elif smash and (choice("sacrifice", (bot.SACRIFICE, bot.PURPLE)) or choice("hammer", bot.HAMMERS)):
        key = "sacrifice" if "sacrifice" in request else "hammer"
        picks[key] = {k: v for k, v in picks[key].items() if k != label} | ({label: request[key]} if request[key] else {})
    else:
        return False
    if len(set(picks["skipped"]) | set(picks["sacrifice"]) | set(picks["hammer"])) > MAX_PICKED:
        return False
    overrides["steps"] = picks
    return True


def set_goal(overrides, quest, again=False):
    """Makes quest (or None) the current quest, newly picked and playing."""
    overrides.update(goal=quest, goal_again=again is True, goal_set_at=time.time(), goal_paused=False)


def pause_other(overrides, task):
    """Only one plays at a time: pauses the quest when it's the Auto make's turn ("make"), and the other way round."""
    if task == "make" and overrides.get("goal"):
        overrides["goal_paused"] = True
    elif task == "quest" and isinstance(overrides.get("make"), dict):
        overrides["make"]["paused"] = True


def start_task(overrides, task, what):
    """Makes this the current Auto make or quest, and pauses the other one so only this one is worked on."""
    if task == "make":
        overrides["make"] = what
    else:
        set_goal(overrides, what["quest"], what.get("again"))
    pause_other(overrides, task)


def next_in_line(overrides, task):
    """After a stop: the first one queued becomes the current Auto make or quest."""
    if task == "make":
        while overrides.get("make_queue") and "make" not in overrides:
            nxt = overrides["make_queue"].pop(0)
            if made := make_request(nxt.get("keys"), nxt.get("recipe")):
                start_task(overrides, "make", made)
    elif overrides.get("goal_queue"):
        start_task(overrides, "quest", overrides["goal_queue"].pop(0))


def reorder(overrides, task, queue, order):
    """Puts the current Auto make or quest and its queue in a new order (order: their old positions, the current one
    being 0). Whatever ends up first becomes the current one, playing or paused as the current one was."""
    if task == "make":
        current = overrides.get("make") if isinstance(overrides.get("make"), dict) else None
    else:
        current = {"quest": overrides["goal"], "again": overrides.get("goal_again") is True} if overrides.get("goal") else None
    items = ([current] if current else []) + queue
    if not isinstance(order, list) or sorted(o for o in order if type(o) is int) != list(range(len(items))) or len(order) != len(items):
        return False
    items = [items[i] for i in order]
    if current and items[0] is not current:
        top = items[0]
        if task == "make":
            made = make_request(top.get("keys"), top.get("recipe"))
            if not made:
                return False
            overrides["make"] = {**made, "paused": bool(current.get("paused"))}
        else:
            overrides.update(goal=top["quest"], goal_again=top.get("again") is True, goal_set_at=time.time())
    elif not current:
        items.insert(0, None)  # nothing current: it's only the queue's order
    rest = items[1:]
    queue[:] = [{"keys": m["keys"], "recipe": m.get("recipe")} for m in rest] if task == "make" else [{"quest": g["quest"], "again": g.get("again") is True} for g in rest]
    return True


def edit_queue(overrides, request):
    """Adds to, removes from or reorders the Auto make or quest queue. With nothing current, an added one starts now.
    Returns False if the request doesn't make sense."""
    task, op, index = request.get("task"), request.get("op"), request.get("index")
    field = {"make": "make_queue", "quest": "goal_queue"}.get(task) if isinstance(task, str) else None
    if not field:
        return False
    queue = overrides.setdefault(field, [])
    if op == "remove":
        if type(index) is not int or not 0 <= index < len(queue):
            return False
        queue.pop(index)
        return True
    if op == "order":
        return reorder(overrides, task, queue, request.get("order"))
    if op != "add":
        return False
    if task == "make":
        made = make_request(request.get("keys"), request.get("recipe"))
        if not made:
            return False
        if not overrides.get("make"):
            start_task(overrides, "make", made)
            return True
        entry = {"keys": made["keys"], "recipe": made["recipe"]}
    else:
        quest, again = request.get("quest"), request.get("again", False)
        if not isinstance(quest, str) or quest not in quests.GOALS or not isinstance(again, bool):
            return False
        if not overrides.get("goal"):
            start_task(overrides, "quest", {"quest": quest, "again": again})
            return True
        entry = {"quest": quest, "again": again}
    if len(queue) >= bot.QUEUE_MAX or entry in queue:
        return False
    queue.append(entry)
    return True


class Handler(BaseHTTPRequestHandler):
    def local_only(self):
        """Whether the request was addressed to this dashboard by one of its names (NAMES) on our port. A web page can make a
        browser send requests to this Mac under another name (DNS rebinding) to read what's here; those are refused."""
        return self.headers.get("Host") in NAMES

    def end_with_safe_headers(self):
        for name, value in SAFE_HEADERS:
            self.send_header(name, value)
        self.end_headers()

    def do_GET(self):
        if not self.local_only():
            self.send_error(403)
            return
        body, kind, cache = None, None, "no-store"
        name, art = self.path.removeprefix("/public/"), ART.match(self.path)
        if self.path == "/api/stream":
            self.stream()
            return
        if self.path in ("/", "/index.html", "/sign-in") and (self.path == "/sign-in") != browser.signed_out():
            self.go_to("/sign-in" if browser.signed_out() else "/")  # signed out: the sign-in page; signed in: the dashboard
            return
        if self.path == "/api/status":
            body, kind = status_body(), "application/json"
        elif self.path in ("/", "/index.html"):
            body, kind = PAGE.read_bytes(), "text/html; charset=utf-8"
        elif self.path == "/sign-in":
            body, kind = SIGN_IN_PAGE.read_bytes(), "text/html; charset=utf-8"
        elif self.path == "/api/sign-in":
            body, kind = json.dumps(sign_in_view()).encode(), "application/json"
        elif self.path == "/logo.svg" and (body := remidex_logo()):
            kind, cache = "image/svg+xml", "max-age=86400"
        elif self.path == "/icon.svg" and (body := remidex_icon()):
            kind, cache = "image/svg+xml", "max-age=86400"
        elif self.path.startswith("/public/") and name in PUBLIC:
            body, kind, cache = public_asset(name), PUBLIC[name][1], "max-age=86400"
        elif (card := CARD.match(self.path)) and (found := card_art(card[1], card[2])):
            (body, kind), cache = found, "max-age=3600"
        elif art and (found := item_art(art[1])):
            (body, kind), cache = found, "max-age=3600"
        elif (badge := ACHIEVEMENT.match(self.path)) and (body := fetch_once(ACHIEVEMENT_ART.format(int(badge[1])), bot.DASH_DIR / "achievements" / f"{int(badge[1]):03d}.png")):
            kind, cache = image_type(body), "max-age=86400"
        elif art:  # no picture anywhere yet: a plain stand-in rather than an error
            body, kind, cache = NO_ART, "image/svg+xml", "max-age=600"
        elif self.path == "/favicon.ico" and (found := item_art("green")):
            (body, kind), cache = found, "max-age=86400"
        elif self.path == "/recipes.json" and (book := bot.recipe_book()[0]):  # without the recipes the game said don't exist
            body = json.dumps({**book, "recipes": [{k: v for k, v in r.items() if k != "_label"} for r in book["recipes"]]}).encode()
            kind, cache = "application/json", "no-cache"
        elif self.path in ("/api/me", "/api/me?fresh=1"):
            body, kind = json.dumps(me_live(fresh=self.path.endswith("fresh=1"))).encode(), "application/json"
        elif self.path == "/api/chat":
            body, kind = json.dumps(chat_view()).encode(), "application/json"
        elif media := MEDIA.match(self.path):
            self.relay_media(media[1])
            return
        elif asked := MENTION.match(self.path):
            body, kind = json.dumps(mention_search(asked[1])).encode(), "application/json"
        elif self.path == "/api/remilia-activity":
            body, kind = json.dumps(remilia_activity()).encode(), "application/json"
        elif self.path in ("/api/trophies", "/api/trophies?fresh=1"):
            body, kind = json.dumps(trophy_case(fresh=self.path.endswith("fresh=1"))).encode(), "application/json"
        elif (trophy := TROPHY_ICON.match(self.path)) and (body := trophy_picture(int(trophy[1]))):
            kind, cache = image_type(body), "max-age=3600"
        elif (shelf := SHELF_TEXTURE.match(self.path)) and (body := shelf_texture(shelf[1])):
            kind, cache = image_type(body), "max-age=3600"
        elif (viewer := VIEWER.match(self.path)) and (body := person_picture(viewer[1])):
            kind, cache = image_type(body), "max-age=3600"
        elif (pic := PIC.match(self.path)) and (body := remilia_picture(urllib.parse.unquote(pic[1]))):
            kind, cache = image_type(body), "max-age=86400"
        elif self.path == "/pfp" and (bot.DASH_DIR / "pfp").exists():
            body = (bot.DASH_DIR / "pfp").read_bytes()
            kind = image_type(body)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", cache)
        if len(body) > 1024 and kind.split(";")[0] in COMPRESSED and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body, 4)  # the status is about half a megabyte of text: a tenth of that, compressed
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.end_with_safe_headers()
        self.wfile.write(body)

    def go_to(self, where):
        """Sends the browser to another page of the dashboard."""
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_with_safe_headers()

    def stream(self):
        """GET /api/stream: a live connection that says "changed" the moment anything the dashboard shows changes, so the
        page can update straight away instead of on a timer. A quiet line every 15 seconds keeps it open."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_with_safe_headers()
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            last, quiet = live_version(), 0.0
            while True:
                time.sleep(STREAM_LOOK)
                version = live_version()
                quiet += STREAM_LOOK
                if version != last:
                    last, quiet = version, 0.0
                    self.wfile.write(b"data: changed\n\n")
                elif quiet >= 15:
                    quiet = 0.0
                    self.wfile.write(b": still here\n\n")
                else:
                    continue
                self.wfile.flush()
        except (OSError, ValueError):
            return  # the page closed or went away

    def do_POST(self):
        """What the dashboard can change, all saved to overrides.json for the bot:
            POST /api/switch {"action": "catchBeetle", "auto": true}     a BeetleBoy task, pokes or friend requests: auto or manual
            POST /api/goal {"quest": "mars_rhino"} (or null)             the quest the bot crafts toward
            POST /api/make {"keys": ["camellia"], "recipe": 12 or null}  Auto make: craft this (by this recipe) first; keys null stops it
            POST /api/hammers {"order": [...], "limits": {...}, "off": [...]}  which hammer goes first, each one's break-chance limit,
                                                                          and which are never smashed with (still upgraded)
            POST /api/pause {"task": "make" or "quest", "paused": true}  hold the Auto make or quest, or carry on with it
            POST /api/queue {"task": "make" or "quest", "op": "add", ...}  line one up (keys and recipe, or quest and again),
                 "op": "remove" with "index", or "op": "order" with the new "order"; stopping the current one starts the next
            POST /api/profile {"displayName", "bio", "friendsDisplayed": [handles], "achievementsDisplayed": [ids], "shelves": [15 trophy ids, 0 = empty]}
                                                                          change your RemiliaNET profile or trophy shelf (the bot makes it)
            POST /api/lock {"key": "pollen_tin", "locked": true}          switch an item off: the bot never uses it in a craft (or false: back on)
            POST /api/sacrifice {"beetle": "green", "auto" or "purple"}   Crafting Beetles: what every smash sacrifices (a step's own pick wins)
            POST /api/step {"task": "make" or "goal", "label": a recipe, "skip": true}  a step's options: do it last (or false: back
                 in order); or "first": true (work on it first) or false; or "sacrifice" or "hammer" for it (null: the bot's choice)
            POST /api/looking {}                                          the page is open and visible: the bot keeps the game up to date
            POST /api/chat/attach (a picture or video, sent as its own type)  kept for a chat message: answers {"id"}
            POST /api/chat/send {"text", "attachments": [ids], "reply_to": a message id or null}  a message for the global chat
            POST /api/sign-in {"email", "password"}                       while the bot's signed out: sign it back in (see sign_in)
            POST /api/sign-in/code {"code"}                               its two-factor code, when it asks; POST /api/sign-in/stop {}: start over
        Only the dashboard's own page may do this: other sites in your browser can't send the JSON header without
        permission, and requests addressed to any other host name are refused."""
        origin = self.headers.get("Origin")
        origin_ok = not origin or origin.split("//", 1)[-1] in NAMES
        attaching = self.path == "/api/chat/attach"  # (the file itself, sent as its own type: see chat_attach)
        if (self.path not in ("/api/switch", "/api/goal", "/api/make", "/api/hammers", "/api/pause", "/api/queue", "/api/profile", "/api/lock", "/api/sacrifice", "/api/step", "/api/looking",
                              "/api/chat/attach", "/api/chat/send", "/api/chat/react", "/api/sign-in", "/api/sign-in/code", "/api/sign-in/stop") or not self.local_only()
                or (self.headers.get("Content-Type") not in CHAT_TYPES if attaching else self.headers.get("Content-Type") != "application/json") or not origin_ok):
            self.send_error(403)
            return
        if attaching:
            self.chat_attach()
            return
        try:  # (a bio can be 244 emoji, a chat message 4,000 characters)
            request = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 60000 if self.path == "/api/chat/send" else 4000)))
        except ValueError:
            self.send_error(400)
            return
        request = request if isinstance(request, dict) else {}
        if self.path == "/api/looking":  # nothing to save in overrides: just that someone's looking right now
            bot.write_json(bot.LOOKING_FILE, {"at": time.time()})
            self.send_response(204)
            self.end_with_safe_headers()
            return
        if self.path == "/api/profile":  # (its own file, not your settings, and it reads your live profile first: not behind the lock)
            self.save_change(request)
            return
        if self.path in ("/api/chat/send", "/api/chat/react"):
            self.chat_send(request)
            return
        if self.path in ("/api/sign-in", "/api/sign-in/code", "/api/sign-in/stop"):
            self.sign_in(request)
            return
        with bot.editing(bot.OVERRIDES_FILE):  # one change at a time, the bot's included, so none undoes another
            self.save_change(request)

    def save_change(self, request):
        """Makes the change a POST asks for (see do_POST), and answers with the settings as they are now."""
        if self.path == "/api/profile":
            if not (edit := profile_request(request)):
                self.send_error(400)
                return
            with bot.editing(bot.PROFILE_EDIT_FILE):
                bot.write_json(bot.PROFILE_EDIT_FILE, edit)  # the bot makes it on RemiliaNET within seconds (see bot.apply_profile_edit)
            self.answer_settings()
            return
        overrides = bot.read_json(bot.OVERRIDES_FILE)
        if self.path == "/api/switch":
            action, auto = request.get("action"), request.get("auto")
            if not isinstance(action, str) or action not in SWITCHES or not isinstance(auto, bool):
                self.send_error(400)
                return
            overrides.setdefault(SWITCHES[action], {})[action] = auto
        elif self.path == "/api/hammers":
            order, limits, timing, off = request.get("order"), request.get("limits"), request.get("timing", True), request.get("off", [])
            if (not isinstance(order, list) or sorted(map(str, order)) != sorted(bot.HAMMERS) or not isinstance(limits, dict)
                    or set(limits) - set(bot.HAMMERS) or not all(type(v) is int and 5 <= v <= 95 for v in limits.values()) or not isinstance(timing, bool)
                    or not isinstance(off, list) or not set(map(str, off)) <= set(bot.HAMMERS)):
                self.send_error(400)
                return
            overrides["hammers"] = {"order": order, "limits": limits, "timing": timing, "off": sorted(set(off))}
        elif self.path == "/api/pause":
            task, paused = request.get("task"), request.get("paused")
            if task not in ("make", "quest") or not isinstance(paused, bool) or not overrides.get("make" if task == "make" else "goal"):
                self.send_error(400)
                return
            if task == "make":
                overrides["make"]["paused"] = paused
            else:
                overrides["goal_paused"] = paused
            if not paused:
                pause_other(overrides, task)
        elif self.path == "/api/lock":
            key, locked = request.get("key"), request.get("locked")
            kept = [k for k in overrides.get("locked") or [] if isinstance(k, str) and k != key]
            if not (isinstance(key, str) and ITEM_KEY.match(key) and isinstance(locked, bool)) or len(kept) >= MAX_LOCKED:
                self.send_error(400)
                return
            overrides["locked"] = kept + [key] if locked else kept
        elif self.path == "/api/sacrifice":
            beetle = request.get("beetle")
            if beetle not in (bot.SACRIFICE, "auto", bot.PURPLE):
                self.send_error(400)
                return
            if beetle == "auto":
                overrides.pop("sacrifice", None)
            else:
                overrides["sacrifice"] = beetle
        elif self.path == "/api/step":
            if not pick_for_step(overrides, request):
                self.send_error(400)
                return
        elif self.path == "/api/queue":
            if not edit_queue(overrides, request):
                self.send_error(400)
                return
        elif self.path == "/api/make":
            keys, recipe = request.get("keys"), request.get("recipe")
            if keys is None:
                overrides.pop("make", None)
                next_in_line(overrides, "make")
            else:
                make = make_request(keys, recipe)
                if not make:
                    self.send_error(400)
                    return
                start_task(overrides, "make", make)
        else:
            quest, again = request.get("quest"), request.get("again", False)
            if quest is not None and (not isinstance(quest, str) or quest not in quests.GOALS) or not isinstance(again, bool):
                self.send_error(400)
                return
            if quest is None:
                set_goal(overrides, None)
                next_in_line(overrides, "quest")
            else:
                start_task(overrides, "quest", {"quest": quest, "again": again})
        bot.write_json(bot.OVERRIDES_FILE, overrides)
        self.answer_settings()

    def chat_attach(self):
        """POST /api/chat/attach, with a picture or video as the body (sent as its type: image/png, video/mp4...): kept for a
        chat message until the bot sends it. Answers {"id"}, for /api/chat/send."""
        ext, most = CHAT_TYPES[self.headers.get("Content-Type")]
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = 0
        if not 0 < size <= most * 1024 * 1024:
            self.send_error(413 if size else 400)
            return
        bot.CHAT_FILES.mkdir(parents=True, exist_ok=True)
        waiting = list(bot.CHAT_FILES.iterdir())
        for old in waiting:
            if time.time() - old.stat().st_mtime > CHAT_FILE_KEPT:  # attached, but never sent
                old.unlink(missing_ok=True)
        if sum(1 for f in bot.CHAT_FILES.iterdir()) >= CHAT_FILES_MAX:
            self.send_error(429)
            return
        name = secrets.token_hex(12) + ext
        part, left = bot.CHAT_FILES / f"{name}.part", size
        with open(part, "wb") as f:
            while left and (chunk := self.rfile.read(min(left, 1 << 20))):
                f.write(chunk)
                left -= len(chunk)
        if left:
            part.unlink(missing_ok=True)
            self.send_error(400)
            return
        part.rename(bot.CHAT_FILES / name)
        self.answer_json({"id": name})

    def chat_send(self, request):
        """POST /api/chat/send {"text", "attachments": [ids from /api/chat/attach], "reply_to": a message id or null}: a
        message for the global chat, which the bot sends within seconds (see chat.send_chat). Answers {"id"}."""
        item = react_request(request) if self.path == "/api/chat/react" else chat_request(request)
        if not item:
            self.send_error(400)
            return
        with bot.editing(bot.CHAT_OUT_FILE):
            out = bot.read_json(bot.CHAT_OUT_FILE)
            queue = [q for q in listed(out.get("queue")) if isinstance(q, dict)]
            if len(queue) >= CHAT_QUEUE_MAX:
                self.send_error(429)
                return
            bot.write_json(bot.CHAT_OUT_FILE, {**out, "queue": queue + [item]})
        self.answer_json({"id": item["id"]})

    def sign_in(self, request):
        """From the sign-in page, while the bot's signed out (it leaves Chrome alone then):
            POST /api/sign-in {"email", "password"}   signs the bot in to RemiliaNET, in the background (about half a
                                                      minute); answers {"started": true, "attempt"}. GET /api/sign-in says how it's going.
            POST /api/sign-in/code {"code"}           the two-factor code, once it says "step": "code"
            POST /api/sign-in/stop {}                 stops it (to start over)
        The password and code go straight to the bot's Chrome, and are never saved. 409: not now (signed in already, a
        sign-in's under way, or it isn't waiting for a code)."""
        if self.path == "/api/sign-in/code":
            code = re.sub(r"[\s-]", "", str(request.get("code") or ""))
            if not CODE.fullmatch(code):
                self.send_error(400)
                return
            with _sign_in_lock:
                if not (_sign_in["running"] and _sign_in["step"] == "code") or not tell_sign_in({"code": code}):
                    self.send_error(409)
                    return
                _sign_in.update(step="checking", error=None, at=time.time())
            self.answer_json({"ok": True})
            return
        if self.path == "/api/sign-in/stop":
            with _sign_in_lock:
                if _sign_in["running"]:
                    _sign_in.update(stopped=True, step=None, error=None, at=time.time())
                    tell_sign_in({"stop": True})
            self.answer_json({"ok": True})
            return
        email, password = request.get("email"), request.get("password")
        if not (isinstance(email, str) and 0 < len(email.strip()) <= 254 and isinstance(password, str) and 0 < len(password) <= 1024):
            self.send_error(400)
            return
        with _sign_in_lock:
            if _sign_in["running"] or not browser.signed_out():
                self.send_error(409)
                return
            attempt = int(time.time() * 1000)
            _sign_in.update(running=True, step=None, error=None, attempt=attempt, stopped=False, at=time.time())
        threading.Thread(target=run_sign_in, args=(email.strip(), password), daemon=True).start()
        _status_cache["at"] = 0
        self.answer_json({"started": True, "attempt": attempt})

    def relay_media(self, media_id):
        """GET /rmedia/<id>: a video from RemiliaNET's chat (they're public), passed through as it comes, a part at a time
        when the page's player asks for one, so it plays and skips ahead without being kept here."""
        asked = self.headers.get("Range")
        headers = {"User-Agent": bot.USER_AGENT, **({"Range": asked} if asked and re.fullmatch(r"bytes=\d*-\d*", asked) else {})}
        started = False
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{bot.SITE}/media/{media_id}", headers=headers), timeout=20) as r:
                kind = (r.headers.get("Content-Type") or "").split(";")[0]
                if not (kind.startswith("video/") or kind == "application/octet-stream"):  # (some come as a plain file: still a video)
                    self.send_error(404)
                    return
                self.send_response(r.status)
                started = True
                self.send_header("Content-Type", kind if kind.startswith("video/") else "video/mp4")  # (the player works out which kind from the file)
                for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
                    if r.headers.get(name):
                        self.send_header(name, r.headers[name])
                self.send_header("Cache-Control", "max-age=86400")
                self.end_with_safe_headers()
                while chunk := r.read(1 << 16):
                    self.wfile.write(chunk)
        except urllib.error.HTTPError as e:
            self.send_error(416 if e.code == 416 else 404)
        except OSError:
            if not started:
                self.send_error(502)  # RemiliaNET didn't answer
            # (else the page stopped watching part way: nothing more to send)

    def answer_json(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_with_safe_headers()
        self.wfile.write(body)

    def answer_settings(self):
        """After a change: the settings as they are now."""
        _status_cache["at"] = 0  # the page asks for the status straight after: it must show this change, not a second-old copy
        self.answer_json({"settings": settings_view(bot.load_settings())})

    def log_message(self, *args):
        pass  # keep the log for the bot


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"RemiDex: {ADDRESS}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
