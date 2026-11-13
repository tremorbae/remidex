"""Writing in RemiliaNET's global chat from the dashboard: what the page may attach and send, and the bot sending it once,
the way the site's chat box does (temp files, a fake page and site: nothing is sent anywhere)."""
import json
import tempfile
import time
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
import dashboard  # noqa: E402

tmp = Path(tempfile.mkdtemp())
support.patch(CHAT_OUT_FILE=tmp / "chat_out.json", CHAT_FILES=tmp / "chat_files", CHAT_FILE=tmp / "chat.json", LOOKING_FILE=tmp / "looking.json",
              DASH_DIR=tmp, log=lambda m: None)
ask = support.serve(dashboard)
get = lambda path: ask(path)[0]  # noqa: E731


def post(path, body, kind="application/json"):
    status, _, answer = ask(path, body, kind)
    return status, json.loads(answer) if status == 200 else None


# ---- attaching: RemiliaNET's own kinds and sizes
status, picture = post("/api/chat/attach", b"\x89PNG fake picture", "image/png")
ok(status == 200 and (pb.CHAT_FILES / picture["id"]).read_bytes() == b"\x89PNG fake picture", f"a picture is kept for the message ({status})")
ok(post("/api/chat/attach", b"MZ", "application/x-msdownload")[0] == 403, "anything but a picture or video is refused")
ok(post("/api/chat/attach", b"x" * 100, "text/plain")[0] == 403, "plain text too")
dashboard.CHAT_TYPES["image/png"] = (".png", 0.00001)  # (10 bytes, for the test)
ok(post("/api/chat/attach", b"x" * 100, "image/png")[0] == 413, "too big: refused")
dashboard.CHAT_TYPES["image/png"] = (".png", 10)
status, video = post("/api/chat/attach", b"fake video", "video/mp4")
pictures = [post("/api/chat/attach", b"p", "image/jpeg")[1]["id"] for _ in range(4)]

# ---- sending: text or something attached; 4 pictures at most, or one video on its own
ok(post("/api/chat/send", {"text": "  "})[0] == 400, "nothing to send: refused")
ok(post("/api/chat/send", {"text": "x" * 4001})[0] == 400, "too long: refused")
ok(post("/api/chat/send", {"text": "hi", "attachments": ["../../etc/passwd"]})[0] == 400, "an attachment that isn't one: refused")
ok(post("/api/chat/send", {"text": "hi", "attachments": [picture["id"], *pictures]})[0] == 400, "five pictures: refused")
ok(post("/api/chat/send", {"text": "hi", "attachments": [video["id"], picture["id"]]})[0] == 400, "a video with a picture: refused")
ok(post("/api/chat/send", {"text": "hi", "reply_to": "1; drop"})[0] == 400, "a reply to something that isn't a message: refused")
status, sent = post("/api/chat/send", {"text": " gm chat ", "attachments": [picture["id"]], "reply_to": 83770})
queue = pb.read_json(pb.CHAT_OUT_FILE).get("queue") or []
ok(status == 200 and len(queue) == 1 and queue[0]["text"] == "gm chat" and queue[0]["reply_to"] == 83770 and queue[0]["files"][0]["name"] == picture["id"],
   f"a message with a picture, answering another, waits for the bot ({queue})")
ok(dashboard.chat_view()["outbox"]["waiting"][0]["id"] == sent["id"], "the page sees it on its way")


# ---- the bot sends it: the picture first, then the message
class ChatSite:
    look = {"userHandle": "me", "userId": 7}

    def __init__(self, upload_ok=True, answer=None):
        self.uploads, self.sent, self.upload_ok, self.answer = [], [], upload_ok, answer or {"ok": True}

    def upload(self, path):
        self.uploads.append(path.read_bytes())
        return (len(self.uploads) + 100, None) if self.upload_ok else (None, "the upload was refused, HTTP 413")

    def chat_send(self, chat_id, kind, payload, expect):
        self.sent.append((chat_id, kind, payload, expect))
        return self.answer


site = ChatSite()
pb.send_chat(site, {"handle": "me"})
out = pb.read_json(pb.CHAT_OUT_FILE)
ok(site.uploads == [b"\x89PNG fake picture"] and site.sent == [(1, "submit", {"text": "gm chat", "in_reply_to_id": 83770, "media_ids": [101]}, {"me": "me"})],
   f"uploaded, then sent with it, as an answer ({site.sent})")
ok(not out.get("queue") and out["results"][sent["id"]]["ok"] is True and not (pb.CHAT_FILES / picture["id"]).exists(), "sent once: off the queue, picture thrown away")
ok(dashboard.chat_view()["outbox"]["results"][sent["id"]]["ok"] is True, "the page sees it went through")

# ---- an upload refused: nothing is sent
status, refused = post("/api/chat/send", {"text": "", "attachments": [pictures[0]]})
site = ChatSite(upload_ok=False)
pb.send_chat(site, {"handle": "me"})
result = pb.read_json(pb.CHAT_OUT_FILE)["results"][refused["id"]]
ok(site.sent == [] and result["ok"] is False and "413" in result["error"], f"the picture couldn't be uploaded: the message isn't sent ({result})")

# ---- RemiliaNET says no (muted, too fast...): the reason is shown
post("/api/chat/send", {"text": "hello"})
site = ChatSite(answer={"ok": False, "error": "you are muted"})
pb.send_chat(site, {"handle": "me"})
ok(any(r.get("error") == "you are muted" for r in pb.read_json(pb.CHAT_OUT_FILE)["results"].values()), "RemiliaNET's reason is kept for the page")

# ---- sent, but not confirmed: reported, and never sent again
status, unsure = post("/api/chat/send", {"text": "did this go"})
site = ChatSite(answer={"ok": None, "error": "the connection dropped before RemiliaNET confirmed it"})
pb.send_chat(site, {"handle": "me"})
pb.send_chat(site, {"handle": "me"})
ok(len(site.sent) == 1 and pb.read_json(pb.CHAT_OUT_FILE)["results"][unsure["id"]]["ok"] is None, "not confirmed: sent once, not again")

# ---- the bot stops mid-send (Chrome closed): it's off the queue, so it isn't sent twice, and the page is told


class Crash(ChatSite):
    def chat_send(self, *args):
        raise RuntimeError("Chrome went away")


status, cut = post("/api/chat/send", {"text": "cut off"})
try:
    pb.send_chat(Crash(), {"handle": "me"})
except RuntimeError:
    pass
out = pb.read_json(pb.CHAT_OUT_FILE)
ok(not out.get("queue") and out["results"][cut["id"]].get("sending"), "stopped mid-send: off the queue, noted as sending")
out["results"][cut["id"]]["at"] = time.time() - 120
pb.write_json(pb.CHAT_OUT_FILE, out)
ok(dashboard.chat_view()["outbox"]["results"][cut["id"]] == {"ok": None, "error": "the bot stopped while sending it"}, "and after a while the page says so")

# ---- how the chat shows: a GIF (a looping video) and a video, and which reactions are yours
pb.write_json(tmp / "look.json", {"userHandle": "me", "userId": 7})
uuid = "0898c92d-8bb3-4c37-8b17-3065b0df4a5f"
pb.write_json(pb.CHAT_FILE, {"at": time.time(), "chat": {"message_ids": [5, 6], "global": {"users": {"3": {"handle": "salty", "display_name": "salty"}}, "messages": {
    "5": {"id": 5, "author_id": 3, "body": "", "created_at": 1792630000000, "video": {"url": f"/media/{uuid}", "thumbnail_url": "/imgproxy/abc", "width": 456, "height": 270, "is_animated": True},
          "reactions": [{"emoji": "🔥", "user_id": 7}, {"emoji": "🔥", "user_id": 9}, {"emoji": "😹", "user_id": 9}]},
    "6": {"id": 6, "author_id": 3, "body": "x", "created_at": 1792630001000, "video": {"url": "https://elsewhere.example/v.mp4"}}}}}})
shown = dashboard.chat_view()["messages"]
ok(shown[0]["video"]["src"] == f"/rmedia/{uuid}" and shown[0]["video"]["animated"] and shown[0]["video"]["poster"].startswith("/rpic?u="),
   f"a GIF plays from the dashboard, as a looping video ({shown[0]['video']})")
ok([{k: r[k] for k in ("emoji", "n", "mine")} for r in shown[0]["reactions"]] == [{"emoji": "😹", "n": 1, "mine": False}, {"emoji": "🔥", "n": 2, "mine": True}],
   f"reactions in the site's order, yours marked ({shown[0]['reactions']})")
ok([p["name"] for p in shown[0]["reactions"][1]["who"]] == ["Unknown", "Unknown"] and shown[0]["reactions"][1]["who"][0]["mine"],
   "who reacted, for the card (someone the chat doesn't name is Unknown, as on the site)")
ok(shown[1]["video"] is None, "a video from anywhere but RemiliaNET isn't shown")
ok(get(f"/rmedia/{uuid}/../../etc") == 404 and get("/rmedia/not-a-video") == 404, "the video pass-through only takes RemiliaNET's own videos")

# ---- reacting: added, or taken back when it's already yours, as on the site
ok(post("/api/chat/react", {"message_id": 5, "emoji": "🍕"})[0] == 400, "an emoji the chat doesn't offer: refused")
ok(post("/api/chat/react", {"message_id": "5", "emoji": "🔥"})[0] == 400, "a message that isn't one: refused")
post("/api/chat/react", {"message_id": 5, "emoji": "😹"})
post("/api/chat/react", {"message_id": 5, "emoji": "🔥"})
queue = pb.read_json(pb.CHAT_OUT_FILE)["queue"]
ok([(q["emoji"], q["on"]) for q in queue] == [("😹", True), ("🔥", False)], f"😹 added, your 🔥 taken back ({queue})")
site = ChatSite()
pb.send_chat(site, {"handle": "me"})
ok(site.sent == [(1, "react", {"message_id": 5, "emoji": "😹"}, {"myId": 7}), (1, "unreact", {"message_id": 5, "emoji": "🔥"}, {"myId": 7})],
   f"sent the way the site does ({site.sent})")

# ---- ~mentions: RemiliaNET's own user search, asked once a minute at most per thing typed
asked = []


class Found:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self.body


def fake_search(request, timeout=None):
    asked.append(request.full_url)
    return Found(json.dumps({"result": [{"userHandle": "believe", "displayName": "Believe", "pfpUrl": "/imgproxy/x"}, {"userHandle": "belfort"}, {"nope": 1}]}).encode())


real_open = dashboard.urllib.request.urlopen
dashboard.urllib.request.urlopen = fake_search
people = dashboard.mention_search("bel")
dashboard.mention_search("BEL")
ok([p["handle"] for p in people] == ["believe", "belfort"] and people[1]["name"] == "belfort" and people[0]["pic"].startswith("/rpic?u="),
   f"the people found, with their names and pictures ({people})")
ok(len(asked) == 1 and "q=bel" in asked[0] and "limit=6" in asked[0], f"asked once, as the site asks ({asked})")
dashboard.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
ok(dashboard.mention_search("zzz") == [], "RemiliaNET can't be reached: nobody listed, no error")
dashboard.urllib.request.urlopen = real_open
ok(get("/api/mention?q=a%20b") == 404 and get("/api/mention?q=" + "x" * 25) == 404, "only what a handle can be is looked up")
ok.done()
