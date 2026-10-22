"""The headless Chrome session: the Site wrapper that makes API calls as the signed-in page, and signing in."""

import sys
import time

from remidex.core import (
    APP_DIR,
    COOKIES_FILE,
    NeedsLogin,
    OVERRIDES_FILE,
    PlaywrightError,
    SITE,
    STATE_FILE,
    as_dict,
    note_health,
    read_json,
    sync_playwright,
    write_json,
)
from remidex.settings import BEETLE_SETTINGS, SOCIAL_SETTINGS


PROFILE_DIR = APP_DIR / "chrome-profile"
SIGN_IN_WAIT = 45  # how long a run gives the page to restore its sign-in
LOGIN_TIMEOUT = 15 * 60
# Runs inside the page: same-origin requests carry the authToken cookie the website keeps fresh.
FETCH = """async ([method, path, payload]) => {
  try {
    const headers = {Accept: 'application/json'};
    const init = {method, headers, credentials: 'same-origin'};
    if (payload !== null) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(payload);
    }
    const res = await fetch(path, init);
    let body = null;
    try { body = await res.json(); } catch {}
    return {status: res.status, body};
  } catch {
    return {status: 0, body: null};
  }
}"""

# Sends one thing to RemiliaNET's chat the way the site does, over its live connection (signed in by the page's cookies):
# kind "submit" (a message: payload {text, in_reply_to_id, media_ids}) or "react"/"unreact" (payload {message_id, emoji}).
# Then waits for RemiliaNET to echo it back (expect: {me: your handle} or {myId: your user id}), or to say why not.
# {ok: true}, {ok: false, error} (refused: not sent), or {ok: null, error} (sent, not confirmed: it may have gone through,
# so it's never sent again).
CHAT_SEND = """([chatId, kind, payload, expect]) => new Promise(resolve => {
  let done = false, sent = false;
  const ws = new WebSocket(`wss://${location.host}/api/ws`);
  const finish = result => { if (done) return; done = true; clearTimeout(timer); try { ws.close(); } catch {} resolve(result); };
  const unconfirmed = why => finish(sent ? {ok: null, error: why} : {ok: false, error: "couldn't reach RemiliaNET's chat"});
  const timer = setTimeout(() => unconfirmed("RemiliaNET didn't confirm it in time"), 20000);
  const send = () => {
    if (sent || done) return;
    sent = true;
    ws.send(JSON.stringify({type: kind, payload: {chat_id: chatId, ...payload}}));
  };
  const echoed = p => {  // the server's copy of what was sent
    const everything = p.global || {}, messages = everything.messages || {};
    if (kind === "submit") {
      const m = messages[p.message_id] || {}, author = (everything.users || {})[m.author_id] || {};
      return (!author.handle || author.handle === expect.me) && (m.body || "").trim() === (payload.text || "").trim();
    }
    const m = messages[payload.message_id];
    return !!m && (m.reactions || []).some(r => r.emoji === payload.emoji && (expect.myId == null || r.user_id === expect.myId)) === (kind === "react");
  };
  ws.onopen = () => { ws.send(JSON.stringify({type: "subscribe", payload: {chat_id: chatId}})); setTimeout(send, 2000); };
  ws.onmessage = event => {
    let frame;
    try { frame = JSON.parse(event.data); } catch { return; }
    const p = (frame && frame.payload) || {};
    if (frame.type === "subscribed" && p.chat_id === chatId) send();
    else if (frame.type === "error" && (p.chat_id === undefined || p.chat_id === chatId)) finish({ok: false, error: String(p.message || "RemiliaNET refused it")});
    else if (sent && p.chat_id === chatId && frame.type === (kind === "submit" ? "deliver" : "reaction") && echoed(p)) finish({ok: true});
  };
  ws.onerror = () => unconfirmed("the connection dropped before RemiliaNET confirmed it");
  ws.onclose = () => unconfirmed("the connection closed before RemiliaNET confirmed it");
})"""
# A picture or video for a chat message, uploaded the way the site does it (then confirmed), from a file field the bot
# fills. {mediaId} or {status, error}.
UPLOAD = """async field => {
  try {
    const form = new FormData();
    form.append("file", field.files[0]);
    const sent = await fetch("/media/upload/", {method: "POST", body: form, credentials: "same-origin"});
    const token = (await sent.json().catch(() => null) || {}).token;
    if (!sent.ok || !token) return {status: sent.status, error: "the upload was refused"};
    const confirmed = await fetch("/media/upload/confirm", {method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", Accept: "application/json"}, body: JSON.stringify({tokens: [token]})});
    const media = ((await confirmed.json().catch(() => null) || {}).media || [])[0];
    return media && media.media_id ? {mediaId: media.media_id} : {status: confirmed.status, error: "the upload wasn't confirmed"};
  } catch (e) {
    return {status: 0, error: String(e)};
  } finally {
    field.remove();
  }
}"""


class Site:
    """Chrome with remilia.net open: headless for runs, a visible window for --login."""

    def __init__(self, playwright, headless):
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        # Your installed Chrome. Unseen, the page needs no animations ("reduced motion") or pictures: that's a third of
        # Chrome's CPU, and the site and its sign-in work the same. (The bot's own downloads, like item icons, don't go
        # through the page.) The sign-in window you see (--login) stays as it is.
        self.context = playwright.chromium.launch_persistent_context(PROFILE_DIR, channel="chrome", headless=headless,
                                                                     args=["--blink-settings=imagesEnabled=false"] if headless else [],
                                                                     reduced_motion="reduce" if headless else "no-preference")
        # Chrome drops session-only cookies when it quits, and the sign-in may use them; put back the ones saved last run.
        saved = read_json(COOKIES_FILE).get("cookies")
        if saved:
            self.context.add_cookies(saved)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.goto(SITE, wait_until="load", timeout=60000)

    def api(self, method, path, body=None):
        """Sends the request (with body as JSON, if given) from inside the page, so it carries the website's sign-in.
        Status 0 means it couldn't be sent, or (for a change, not a read) that it's not known whether it went through."""
        for _ in range(5):
            try:
                result = self.page.evaluate(FETCH, [method, path, body])
                note_health(result["status"])
                return result["status"], result["body"]
            except PlaywrightError:
                # The page navigated mid-call (it does while restoring a sign-in); let it settle and try again.
                self.page.wait_for_load_state("load")
                if method not in ("GET", "HEAD"):
                    return 0, None  # a craft, poke or claim may have gone through already: never send it twice
                self.wait(1)
        raise RuntimeError(f"the page kept navigating during {method} {path}")

    def signed_api(self, method, path, body=None):
        """Like api, but if the site says the sign-in lapsed, renews it and asks once more."""
        status, answer = self.api(method, path, body)
        if status == 401:
            self.restore_sign_in()
            status, answer = self.api(method, path, body)
        return status, answer

    def wait(self, seconds):
        self.page.wait_for_timeout(seconds * 1000)

    def signed_in_handle(self, wait):
        """Polls until the page has restored its sign-in. Returns (handle or None, the last whoami status)."""
        deadline = time.time() + wait
        while True:
            status, body = self.api("GET", "/api/profile/whoami")
            handle = as_dict(body).get("userHandle")
            if status == 200 and isinstance(handle, str) and handle:
                self.look = {k: body.get(k) for k in ("userHandle", "userId", "displayName", "theme", "color", "pfpUrl")}
                return handle, status
            if time.time() >= deadline:
                return None, status
            self.wait(2)

    def require_sign_in(self):
        handle, status = self.signed_in_handle(SIGN_IN_WAIT)
        if handle:
            self.save_cookies()
            return handle
        if status == 401:
            raise NeedsLogin("the website isn't signed in any more")
        raise RuntimeError(f"couldn't check the sign-in (HTTP {status})")

    def restore_sign_in(self):
        """Reloads the page so it renews its sign-in."""
        self.page.reload(wait_until="load", timeout=60000)
        self.require_sign_in()

    def download(self, path):
        """A file from the site, fetched with the website's sign-in (item icons need it). None if it fails."""
        try:
            response = self.context.request.get(SITE + path, timeout=30000)
            return response.body() if response.ok else None
        except PlaywrightError:
            return None

    def upload(self, path):
        """Uploads a picture or video to RemiliaNET, for a chat message. Returns (its media id, None) or (None, why not)."""
        try:
            field = self.page.evaluate_handle("() => { const f = document.createElement('input'); f.type = 'file'; f.hidden = true; document.body.append(f); return f; }")
            field.as_element().set_input_files(str(path))
            result = self.page.evaluate(UPLOAD, field)
        except PlaywrightError as e:
            return None, f"Chrome: {str(e)[:120]}"
        if isinstance(result, dict) and isinstance(result.get("mediaId"), int):
            note_health(200)
            return result["mediaId"], None
        result = result if isinstance(result, dict) else {}
        note_health(result.get("status") or 0)
        return None, f"{result.get('error') or 'failed'}, HTTP {result.get('status')}"

    def chat_send(self, chat_id, kind, payload, expect):
        """Sends a chat message or a reaction (see CHAT_SEND). Returns {"ok", "error"}."""
        try:
            return self.page.evaluate(CHAT_SEND, [chat_id, kind, payload, expect])
        except PlaywrightError as e:  # (the page went away mid-send: it may have gone through)
            return {"ok": None, "error": f"Chrome stopped while sending ({str(e)[:100]})"}

    def save_cookies(self):
        write_json(COOKIES_FILE, {"cookies": self.context.cookies()})

    def close(self):
        self.context.close()


def first_sign_in_defaults():
    """The first time anyone signs in on this Mac (nothing saved yet): every switch on the dashboard starts off (BeetleBoy's
    claims and hunts, pokes, friend requests, profile views, junk cubes), so nothing happens on the account until you turn
    it on there.
    Someone who's been here before keeps what they set. Returns whether it did."""
    if STATE_FILE.exists() or OVERRIDES_FILE.exists() or COOKIES_FILE.exists():
        return False
    write_json(OVERRIDES_FILE, {"beetleboy": dict.fromkeys(BEETLE_SETTINGS, False), "social": dict.fromkeys(SOCIAL_SETTINGS, False),
                                "crafting": {"junk_cubes": False}})
    return True


def login():
    new_here = not (STATE_FILE.exists() or OVERRIDES_FILE.exists() or COOKIES_FILE.exists())
    with sync_playwright() as playwright:
        site = Site(playwright, headless=False)
        try:
            handle, _ = site.signed_in_handle(0)
            if not handle:
                print('Log in to RemiliaNET in the Chrome window that just opened, and tick "Remember me". '
                      "This waits until you're signed in.")
                handle, _ = site.signed_in_handle(LOGIN_TIMEOUT)
            if not handle:
                sys.exit("Timed out waiting for the sign-in.")
            if new_here:
                first_sign_in_defaults()  # before the sign-in is saved: that's what says someone's been here
            site.save_cookies()
        finally:
            site.close()

    state = read_json(STATE_FILE)
    state.pop("needs_login", None)
    write_json(STATE_FILE, state)
    print(f"Signed in as ~{handle}. Each run renews the sign-in; if it ever lapses (say, after a long sleep), "
          "you'll get a notification to run --login again.")
    if new_here:
        print("Everything starts switched OFF. Turn on what you want in RemiDex: each part has its switch there.")
