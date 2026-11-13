"""RemiliaNET's global chat, for the dashboard: reading it while the dashboard is open, and sending what you write there
(with any pictures or video), the way the site's own chat box does."""

import re
import time

from remidex.core import APP_DIR, DASH_DIR, as_dict, editing, log, read_json, someone_looking, write_json


CHAT_FILE = DASH_DIR / "chat.json"  # the newest messages in RemiliaNET's global chat, for the dashboard
GLOBAL_CHAT = 1  # the global chat's id on RemiliaNET
CHAT_SYNC_EVERY = 5  # while someone's looking, read the chat this often (seconds)
CHAT_OUT_FILE = APP_DIR / "chat_out.json"  # what you wrote on the dashboard, waiting to be sent, and how sending went
CHAT_FILES = APP_DIR / "chat_files"  # its pictures and videos, until they're sent
ATTACHMENT_NAME = re.compile(r"^[0-9a-f]{24}\.(jpg|png|gif|webp|mp4|webm|mov)$")  # as the dashboard saves them
RESULTS_KEPT = 20
_CHATTED = {"at": 0}


def sync_chat(site):
    """While someone's looking at the dashboard: reads the newest messages in RemiliaNET's global chat, for its Global Chat
    tab (reading it needs your sign-in, which only the bot has). Only reads: nothing is marked read."""
    if time.time() - _CHATTED["at"] < CHAT_SYNC_EVERY or not someone_looking():
        return
    _CHATTED["at"] = time.time()
    status, body = site.signed_api("GET", f"/api/chats/{GLOBAL_CHAT}/messages?limit=30")
    if status == 200 and isinstance(body, dict):
        write_json(CHAT_FILE, {"at": time.time(), "chat": body})


def chat_waiting(path=None):
    """Whether there's a message from the dashboard to send (path: another bot's CHAT_OUT_FILE)."""
    return bool(read_json(path or CHAT_OUT_FILE).get("queue"))


def attachments(item):
    """A message's pictures or video, as saved by the dashboard: [{"name": the file in CHAT_FILES, "label"}]."""
    return [f for f in item.get("files") or [] if isinstance(f, dict) and isinstance(f.get("name"), str) and ATTACHMENT_NAME.match(f["name"])]


def note_result(item_id, result):
    with editing(CHAT_OUT_FILE):
        out = read_json(CHAT_OUT_FILE)
        results = as_dict(out.get("results"))
        results.pop(item_id, None)
        out["results"] = {**dict(list(results.items())[-RESULTS_KEPT + 1:]), item_id: {**result, "at": time.time()}}
        write_json(CHAT_OUT_FILE, out)


REACTIONS = ("😹", "🤍", "👍", "😮", "🔥")  # the ones RemiliaNET's chat offers


def send_one(site, item, me):
    """A reaction, or a message (its pictures or video uploaded first). Returns {"ok": True, False, or None (sent, but
    RemiliaNET didn't confirm it)}, "error": why not}."""
    if item.get("kind") == "react":
        if type(item.get("message_id")) is not int or item.get("emoji") not in REACTIONS:
            return {"ok": False, "error": "not a reaction"}
        my_id = as_dict(getattr(site, "look", None)).get("userId")
        answer = as_dict(site.chat_send(GLOBAL_CHAT, "react" if item.get("on") is not False else "unreact",
                                        {"message_id": item["message_id"], "emoji": item["emoji"]}, {"myId": my_id if type(my_id) is int else None}))
        return {"ok": answer.get("ok") if answer.get("ok") in (True, False) else None, "error": answer.get("error")}
    media = []
    for f in attachments(item):
        media_id, error = site.upload(CHAT_FILES / f["name"])
        if media_id is None:
            return {"ok": False, "error": f"couldn't upload {f.get('label') or 'the attachment'} ({error})"}
        media.append(media_id)
    payload = {"text": item.get("text") or ""}
    if type(item.get("reply_to")) is int:
        payload["in_reply_to_id"] = item["reply_to"]
    if media:
        payload["media_ids"] = media
    answer = as_dict(site.chat_send(GLOBAL_CHAT, "submit", payload, {"me": me}))
    return {"ok": answer.get("ok") if answer.get("ok") in (True, False) else None, "error": answer.get("error")}


def send_chat(site, state):
    """Sends what was written on the dashboard, oldest first. How each went is noted for the dashboard. Each is sent once:
    one RemiliaNET didn't confirm is reported, never sent again (it may have gone through)."""
    for item in read_json(CHAT_OUT_FILE).get("queue") or []:
        if not (isinstance(item, dict) and isinstance(item.get("id"), str)):
            continue
        with editing(CHAT_OUT_FILE):  # taken off the queue before sending: a crash mid-send can't send it twice
            out = read_json(CHAT_OUT_FILE)
            out["queue"] = [q for q in out.get("queue") or [] if as_dict(q).get("id") != item["id"]]
            write_json(CHAT_OUT_FILE, out)
        note_result(item["id"], {"ok": None, "sending": True, "error": None})
        result = send_one(site, item, state.get("handle"))
        note_result(item["id"], result)
        for f in attachments(item):
            (CHAT_FILES / f["name"]).unlink(missing_ok=True)
        if result["ok"] is not True:
            what = "reaction" if item.get("kind") == "react" else "message"
            log(f"Chat: your {what} {'may not have been sent' if result['ok'] is None else 'was not sent'} ({result['error']}).")
        _CHATTED["at"] = 0  # show it in the chat straight away
