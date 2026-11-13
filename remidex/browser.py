"""The headless Chrome session: the Site wrapper that makes API calls as the signed-in page, and signing in (from the
dashboard's sign-in page)."""

import html
import json
import queue
import re
import sys
import threading
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
    log,
    note_health,
    read_json,
    sync_playwright,
    write_json,
)
from remidex.settings import BEETLE_SETTINGS, SOCIAL_SETTINGS


# The bot's browser profile. (Not chrome-profile, which your Google Chrome made when the bot used it: a newer Chrome's
# profile can't be opened by the bot's older Chromium. The installers delete it; the sign-in is in cookies.json.)
PROFILE_DIR = APP_DIR / "browser-profile"
SIGN_IN_WAIT = 45  # how long a run gives the page to restore its sign-in
FORM_WAIT = 60  # how long RemiliaNET's sign-in page gets to show its form (seconds; a slow server needs a while)
OPEN_WAIT = 45  # how long the site's Sign in gets to open RemiliaNET's sign-in page, clicked again now and then (seconds)
SITE_SIGN_IN = re.compile(r"^sign in", re.I)  # (the front page's logo, on a first visit: "Sign in or register")
SITE_SIGN_IN_TEXT = re.compile(r"^\s*sign in\s*$", re.I)  # (the "SIGN IN or register" box at the top right, otherwise)
CODE_WAIT = 5 * 60  # how long a sign-in waits for your two-factor code (about as long as RemiliaNET's sign-in page lasts)
CODE_TRIES = 5  # codes that don't work before it gives up
# RemiliaNET's sign-in pages say what they are, and any message (a wrong password or code), in window.kcContext.
SIGN_IN_PAGE = """() => {
  const k = window.kcContext;
  if (!k) return null;
  const m = k.message, fields = k.messagesPerField;
  let error = m && (m.type === "error" || m.type === "warning") ? String(m.summary || "") : "";
  try { if (!error && fields && fields.existsError("totp")) error = String(fields.get("totp") || ""); } catch {}
  return {page: String(k.pageId || ""), error};
}"""
# RemiliaNET's other sign-in pages RemiDex can't do for you, and what it says.
CANT_DO = {
    "webauthn-authenticate.ftl": "RemiliaNET asked for a passkey or security key, which RemiDex can't use. An authenticator app code works",
    "login-page-expired.ftl": "RemiliaNET's sign-in page expired. Sign in again",
}
# Nobody sees the bot's page, but RemiliaNET's front page redraws a canvas 60 times a second, which kept Chrome busy
# around the clock. This, run in the page before the site's own code, lets it draw once a second and stops CSS
# animations: a tenth of the CPU, and the site and its sign-in work the same. (Not used while signing in.)
CALM = """(() => {
  const later = window.setTimeout.bind(window), stop = window.clearTimeout.bind(window);
  window.requestAnimationFrame = draw => later(() => draw(performance.now()), 1000);
  window.cancelAnimationFrame = id => stop(id);
  const still = () => {
    const style = document.createElement("style");
    style.textContent = "*, *::before, *::after { animation: none !important; transition: none !important; }";
    (document.head || document.documentElement).append(style);
  };
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", still) : still();
})();"""
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


def open_browser(playwright, profile, calm=True):
    """The bot's browser, unseen, keeping its profile in the folder profile: Playwright's own headless Chromium, not your
    Google Chrome, which is left alone (macOS took the bot's copy for yours, so yours wouldn't open a window). Unseen, a
    page needs no animations ("reduced motion", and CALM unless calm is False) or pictures, and the site and its sign-in
    work the same. (The bot's own downloads, like item icons, don't go through the page.) Returns its context."""
    profile.mkdir(parents=True, exist_ok=True)
    # (A small disk cache: RemiliaNET's pages still come mostly from it, and a profile can't grow to gigabytes.)
    context = playwright.chromium.launch_persistent_context(profile, headless=True, args=["--blink-settings=imagesEnabled=false", "--disk-cache-size=20000000"],
                                                            reduced_motion="reduce")
    if calm:
        context.add_init_script(CALM)
    return context


class Site:
    """Chrome with remilia.net open, unseen."""

    def __init__(self, playwright, calm=True):
        self.context = open_browser(playwright, PROFILE_DIR, calm)
        # Chrome drops session-only cookies when it quits, and the sign-in may use them; put back the ones saved last run.
        saved = read_json(COOKIES_FILE).get("cookies")
        if saved:
            self.context.add_cookies(saved)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.goto(SITE, wait_until="load", timeout=90000)

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
        self.page.reload(wait_until="load", timeout=90000)
        self.require_sign_in()

    def open_sign_in(self):
        """Opens RemiliaNET's sign-in page with the site's own Sign in. Which one there is depends on the front page the
        site shows: the "SIGN IN or register" box at the top right, or (on a first visit) its logo. Tries each one it can
        see, and again for up to OPEN_WAIT (on a slow server, a click before the page is ready does nothing), until the
        sign-in page starts to open (the rest waits for its form). Returns whether it did."""
        page, tried = self.page, 0
        deadline = time.time() + OPEN_WAIT
        while time.time() < deadline:
            for kind in (page.get_by_text(SITE_SIGN_IN_TEXT), page.get_by_role("button", name=SITE_SIGN_IN), page.get_by_role("link", name=SITE_SIGN_IN)):
                for i in range(kind.count()):
                    try:
                        if not kind.nth(i).is_visible():
                            continue
                        tried += 1
                        kind.nth(i).click(force=True, timeout=5000)
                        page.wait_for_url(re.compile(r"/oidc/"), wait_until="commit", timeout=10000)
                        return True
                    except PlaywrightError:
                        if "/oidc/" in page.url:
                            return True
                        continue  # not that one (or not yet): the next
            if "/oidc/" in page.url:
                return True
            self.wait(1)
        log(f"Sign-in: RemiliaNET's sign-in page didn't open (clicked a Sign in {tried} times; the page is at {page.url[:80]}).")
        return False

    def fill_sign_in(self, email, password):
        """Signs in the way you would: the site's Sign in, SIGN IN on RemiliaNET's page, then the email and password, with
        "Remember me" ticked. Returns None once they're sent (see finish_steps for what comes next), or why not. (Its
        errors never say what was typed.)"""
        page = self.page
        try:
            if not self.open_sign_in():
                return "RemiliaNET's sign-in page didn't open. Try again in a minute"
            form, choose = page.locator("#kc-form-login"), page.get_by_role("button", name="SIGN IN", exact=True)
            deadline = time.time() + FORM_WAIT
            while not form.is_visible():  # its first page asks: SIGN IN, or NEW ACCOUNT
                if time.time() >= deadline:
                    return "RemiliaNET's sign-in form didn't open. Try again in a minute"
                if choose.count():
                    choose.first.click(force=True)
                self.wait(0.5)
            user = page.locator("#username")
            if user.count() and user.is_editable():  # (a page that already knows who you are just asks for the password)
                user.fill(email)
            page.locator("#password").fill(password)
            remember = page.locator("#rememberMe")  # so the sign-in lasts
            try:
                if remember.count() and not remember.is_checked():
                    tick = page.locator("#rememberMe + [role=checkbox]")  # the box you see: the real one is hidden under it
                    tick.click(force=True, timeout=3000) if tick.count() else remember.set_checked(True, force=True, timeout=3000)
            except PlaywrightError:
                pass  # (it still signs in, just not for as long)
            with page.expect_navigation(wait_until="load", timeout=45000):
                form.locator("[type=submit]").first.click(force=True)
        except PlaywrightError:
            return "RemiliaNET's sign-in page didn't answer. Try again in a minute"
        return None

    def sign_in_step(self):
        """Where RemiliaNET's sign-in has got to: None once it's sent the page back to the site, else {"page": the page's
        name, "error": its message, or ""}."""
        if "/oidc/" not in self.page.url:
            return None
        try:
            said = as_dict(self.page.evaluate(SIGN_IN_PAGE))
        except PlaywrightError:
            said = {}
        error = html.unescape(re.sub(r"<[^>]*>", "", str(said.get("error") or ""))).strip()[:200].rstrip(".")
        return {"page": str(said.get("page") or ""), "error": error}

    def finish_steps(self, ask_code):
        """After the password: whatever RemiliaNET asks next, until it sends the page back to the site. Its two-factor code
        is asked for with ask_code(why the last one didn't work, or None), which returns the code, or None to stop; one
        that doesn't work is asked for again (a few times). "Trust this device?" is answered no. Returns None once back on
        the site, or why not (RemiliaNET's own message where it gives one)."""
        codes = 0
        for _ in range(CODE_TRIES + 3):  # (each pass sends RemiliaNET something, or stops)
            step = self.sign_in_step()
            if step is None:
                return None
            page, error = step["page"], step["error"]
            if page.startswith("login-otp"):
                if codes >= CODE_TRIES:
                    return error or "Too many codes that didn't work. Sign in again"
                codes += 1
                code = ask_code(error or None) if ask_code else None
                if not code:
                    return "No code came in time. Sign in again"
                why = self.send_code(code)
            elif page.startswith("trusted-device"):
                why = self.answer_trust(False)
            else:
                return error or CANT_DO.get(page) or f"RemiliaNET asked for a step RemiDex can't do yet ({page.removesuffix('.ftl') or 'unknown'})"
            if why:
                return why
        return "RemiliaNET's sign-in didn't finish. Try again in a minute"

    def send_code(self, code):
        """Types a two-factor code into RemiliaNET's code page and sends it. None once sent, or why not."""
        page = self.page
        try:
            form = page.locator("#kc-otp-login-form")
            form.wait_for(state="visible", timeout=FORM_WAIT * 1000)
            cells, field = form.locator(".code-input-cell"), form.locator("[name=otp]")
            if cells.count():  # a box per digit: the whole code in the first spreads it over them, as pasting it does
                cells.first.fill(code)
                if field.count() and field.first.input_value() != code:
                    for i, digit in enumerate(code[:cells.count()]):
                        cells.nth(i).fill(digit)
            else:
                field.first.fill(code)
            with page.expect_navigation(wait_until="load", timeout=45000):
                form.locator("#kc-login, [type=submit]").first.click(force=True)
        except PlaywrightError:
            return "RemiliaNET's code page didn't answer. Try again in a minute"
        return None

    def answer_trust(self, yes):
        """Answers RemiliaNET's "Trust this device?" (no, unless yes). None once answered, or why not."""
        page = self.page
        try:
            form = page.locator("#kc-form-trusted-device")
            form.wait_for(state="visible", timeout=FORM_WAIT * 1000)
            with page.expect_navigation(wait_until="load", timeout=45000):
                form.locator(f'[name="trusted-device"][value="{"yes" if yes else "no"}"]').first.click(force=True)
        except PlaywrightError:
            return "RemiliaNET's sign-in page didn't answer. Try again in a minute"
        return None

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
    """The first time anyone signs in on this computer (see new_here): every switch on the dashboard starts off (BeetleBoy's
    claims and hunts, pokes, friend requests, profile views, junk cubes, the Tin Hammer), so nothing happens on the account until you turn
    it on there.
    Someone who's been here before keeps what they set. Returns whether it did."""
    if not new_here():
        return False
    write_json(OVERRIDES_FILE, {"beetleboy": dict.fromkeys(BEETLE_SETTINGS, False), "social": dict.fromkeys(SOCIAL_SETTINGS, False),
                                "crafting": {"junk_cubes": False, "tin_hammer": False}})
    return True


def new_here():
    """Nobody's signed in on this computer before: no sign-in or settings saved, and no account seen. (The bot, running
    while it waits for the first sign-in, saves a little of its own; that doesn't count.)"""
    return not (COOKIES_FILE.exists() or OVERRIDES_FILE.exists() or read_json(STATE_FILE).get("handle"))


def signed_out():
    """The bot's waiting for a new sign-in. It never opens Chrome then, so a sign-in can use it."""
    return bool(read_json(STATE_FILE).get("needs_login")) or not COOKIES_FILE.exists()


def finish_sign_in(cookies, first_time):
    """Once Chrome's closed (the bot opens it the moment this is done): the first-time switches for someone new here,
    the sign-in saved, and the pause lifted."""
    if first_time:
        first_sign_in_defaults()  # before the sign-in is saved: that's what says someone's been here
    write_json(COOKIES_FILE, {"cookies": cookies})
    state = read_json(STATE_FILE)
    state.pop("needs_login", None)
    write_json(STATE_FILE, state)


def sign_in_with(email, password, ask_code=None):
    """Signs the bot in to RemiliaNET with your email and password, in its own unseen Chrome (for the dashboard's sign-in
    page): only while it's signed out, when the bot leaves Chrome alone. The password is only typed into RemiliaNET's
    page, never saved. If RemiliaNET wants your two-factor code, ask_code gets it (see Site.finish_steps). Returns (your
    handle, None) or (None, why not)."""
    if not signed_out():
        return None, "RemiDex is already signed in"
    first_time = new_here()
    with sync_playwright() as playwright:
        site = Site(playwright, calm=False)  # (RemiliaNET's sign-in pages exactly as they are)
        try:
            handle, _ = site.signed_in_handle(3)
            if not handle:
                why = site.fill_sign_in(email, password) or site.finish_steps(ask_code)
                if why:
                    return None, why
                handle, _ = site.signed_in_handle(SIGN_IN_WAIT)
            if not handle:
                return None, "RemiliaNET took the sign-in but didn't finish it. Try again in a minute"
            cookies = site.context.cookies()
        finally:
            site.close()
    finish_sign_in(cookies, first_time)
    log(f"Signed in as ~{handle} from the RemiDex dashboard.")
    return handle, None


def sign_in_from_dashboard():
    """--sign-in: the dashboard's sign-in page. Reads JSON lines on its input (never the command line, which other programs
    can see): first {"email", "password"}, then a {"code"} each time it asks for one ({"stop": true}, or nothing for
    CODE_WAIT, stops it). Prints JSON lines: {"step": "code", "error"} when RemiliaNET wants a two-factor code (error:
    why the last one didn't work, or null), and last {"ok": true, "handle"} or {"ok": false, "error"}."""
    lines = queue.Queue()

    def read():
        for line in sys.stdin:
            lines.put(line)
        lines.put("")  # (the dashboard's gone)
    threading.Thread(target=read, daemon=True).start()

    def message(wait):
        try:
            return as_dict(json.loads(lines.get(timeout=wait) or "{}"))
        except (queue.Empty, ValueError):
            return {}

    def say(what):
        print(json.dumps(what), flush=True)

    def ask_code(error):
        say({"step": "code", "error": error})
        code = message(CODE_WAIT).get("code")
        return code if isinstance(code, str) and re.fullmatch(r"\d{4,10}", code) else None

    asked = message(30)
    email, password = asked.get("email"), asked.get("password")
    if not (isinstance(email, str) and email.strip() and isinstance(password, str) and password):
        handle, why = None, "Type your RemiliaNET email and password"
    else:
        try:
            handle, why = sign_in_with(email.strip(), password, ask_code)
        except (PlaywrightError, RuntimeError, OSError):  # (Chrome wouldn't start, or RemiliaNET wouldn't load)
            handle, why = None, "Chrome couldn't open RemiliaNET. Try again in a minute"
    say({"ok": True, "handle": handle} if handle else {"ok": False, "error": why})
