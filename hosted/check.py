#!/usr/bin/env python3
"""RemiDex's server check: a small, password-protected page that finds out whether RemiliaNET works from a server, before
the RemiDex website is built on one. It (1) loads RemiliaNET, (2) signs in with your account, with the same sign-in
RemiDex uses (email, password, two-factor code), and (3) opens that sign-in again in a fresh Chrome and reads your game,
the way the bot does each time. Read-only: it never pokes, crafts or changes anything on your account. Everything it
saved is wiped when it's done.

    CHECK_PASSWORD=... python3 hosted/check.py     (the page asks for that password; PORT picks the port, 10000 if not set)
"""

import base64
import hmac
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("REMIDEX_HOME", "/tmp/remidex-check")  # (before RemiDex is loaded: it's where it keeps things)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from remidex import browser  # noqa: E402
from remidex.core import APP_DIR, SITE, PlaywrightError, as_dict, sync_playwright  # noqa: E402

PASSWORD = os.environ.get("CHECK_PASSWORD", "")
PORT = int(os.environ.get("PORT") or 10000)
WRONG_PASSWORDS = 10  # wrong page passwords from one address before it's turned away for a while
WRONG_REST = 10 * 60
HEADERS = (("Cache-Control", "no-store"), ("X-Frame-Options", "DENY"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
           ("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
                                       "img-src 'self' data:; form-action 'none'; frame-ancestors 'none'; base-uri 'none'"))
_job = {"stage": "idle", "steps": [], "error": None, "at": 0.0}  # idle, running, code (waiting for one), done
_lock = threading.Lock()
_codes = queue.Queue()
_wrong = {}  # address: [count, first at]


def say(line):
    print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)


def step(name, ok, detail):
    """One line of the result: ok is True, False, or None (still going)."""
    with _lock:
        _job["steps"] = [s for s in _job["steps"] if s["name"] != name] + [{"name": name, "ok": ok, "detail": detail}]
    say(f"{name}: {'ok' if ok else 'working' if ok is None else 'FAILED'}: {detail}")


def wipe():
    shutil.rmtree(APP_DIR, ignore_errors=True)
    APP_DIR.mkdir(parents=True, exist_ok=True)


def first_line(e):
    return (str(e).strip().splitlines() or ["?"])[0][:200]


def memory():
    """How much memory this server is really using (from Linux's cgroup files, without the file cache it can let go of
    whenever it needs to), for sizing the website: ", memory 210 MB in use (510 MB with cache)". Empty where those files
    don't exist (a Mac)."""
    try:
        total = int(Path("/sys/fs/cgroup/memory.current").read_text())
        stat = dict(line.split() for line in Path("/sys/fs/cgroup/memory.stat").read_text().splitlines())
    except (OSError, ValueError):
        return ""
    cache = int(stat.get("inactive_file", 0)) + int(stat.get("active_file", 0))
    return f", memory {(total - cache) // 2 ** 20} MB in use ({total // 2 ** 20} MB with cache)"


def reach():
    """Step 1: RemiliaNET in a fresh Chrome, signed out. Returns whether it loaded properly."""
    name = "1. RemiliaNET loads from this server"
    step(name, None, "opening RemiliaNET in Chrome...")
    started = time.time()
    try:
        with sync_playwright() as playwright:
            # The bot's own browser, as it opens it (see browser.Site): Playwright's headless Chromium, drawing calmly.
            context = playwright.chromium.launch_persistent_context(APP_DIR / "reach-profile", channel=os.environ.get("REMIDEX_CHROME") or None, headless=True,
                                                                    args=["--blink-settings=imagesEnabled=false"], reduced_motion="reduce")
            context.add_init_script(browser.CALM)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                response = page.goto(SITE, wait_until="load", timeout=90000)
                page.wait_for_timeout(4000)
                title, html = page.title(), page.content()
                challenge = "just a moment" in title.lower() or "cf-chl" in html or "challenge-platform" in html
                sign_in = page.get_by_text(browser.SITE_SIGN_IN_TEXT).count() + page.get_by_role("button", name=browser.SITE_SIGN_IN).count()
                api = as_dict(page.evaluate(browser.FETCH, ["GET", "/api/profile/whoami", None])).get("status")
                agent = page.evaluate("navigator.userAgent")
                used = memory()
            finally:
                context.close()
    except PlaywrightError as e:
        step(name, False, f"Chrome couldn't load it: {first_line(e)}")
        return False
    status = response.status if response else None
    ok = status == 200 and not challenge and sign_in > 0 and api == 401
    step(name, ok, f"page HTTP {status}, title {title!r}, Cloudflare challenge: {'YES' if challenge else 'no'}, sign-in button: {'found' if sign_in else 'MISSING'}, "
                   f"API answers (401 expected while signed out): {api}, {time.time() - started:.0f}s{used}. Browser: {agent[:90]}")
    return ok


def ask_code(error):
    """The sign-in's two-factor question: waits for the page to send a code (or for nothing, for a while)."""
    while not _codes.empty():
        _codes.get_nowait()
    with _lock:
        _job.update(stage="code", error=error)
    step("2. Signing in", None, "RemiliaNET wants the two-factor code" + (f" (the last one didn't work: {error})" if error else ""))
    try:
        code = _codes.get(timeout=browser.CODE_WAIT)
    except queue.Empty:
        code = None
    with _lock:
        _job.update(stage="running", error=None)
    return code


def sign_in_and_reopen(email, password):
    """Steps 2 and 3: the sign-in, then that sign-in in a fresh Chrome, reading the game (read-only)."""
    name = "2. Signing in"
    step(name, None, "signing in with the email and password (about half a minute)...")
    started = time.time()
    try:
        handle, why = browser.sign_in_with(email, password, ask_code)
    except (PlaywrightError, RuntimeError, OSError) as e:
        handle, why = None, f"Chrome: {first_line(e)}"
    if not handle:
        step(name, False, why)
        return
    step(name, True, f"signed in as ~{handle} ({time.time() - started:.0f}s)")
    name = "3. The sign-in works again later (as the bot needs)"
    step(name, None, "opening a fresh Chrome with the saved sign-in...")
    try:
        with sync_playwright() as playwright:
            site = browser.Site(playwright)
            try:
                again, status = site.signed_in_handle(browser.SIGN_IN_WAIT)
                game_status, game = site.api("GET", "/api/beetle/user") if again else (None, None)
                used = memory()
            finally:
                site.close()
    except (PlaywrightError, RuntimeError, OSError) as e:
        step(name, False, f"Chrome: {first_line(e)}")
        return
    game = as_dict(game)
    level = as_dict(game.get("user") or game).get("level")
    ok = again == handle and game_status == 200
    step(name, ok, f"signed in again as ~{again} (whoami HTTP {status}); BeetleBoy game read: HTTP {game_status}" + (f", level {level}" if level is not None else "") + used)


def run(email, password):
    try:
        wipe()
        if reach() and email:
            sign_in_and_reopen(email, password)
    except Exception as e:  # (shown on the page rather than lost)
        step("Something unexpected", False, f"{type(e).__name__}: {first_line(e)}")
    finally:
        wipe()  # nothing kept: the sign-in, Chrome's profile, all of it
        step("Cleaned up", True, "everything this check saved has been deleted")
        with _lock:
            _job.update(stage="done", at=time.time())


class Handler(BaseHTTPRequestHandler):
    def allowed(self):
        """The page's password (HTTP Basic); too many wrong ones from one address: turned away for a while."""
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()  # (the host's proxy says who it's for)
        who, now = self.headers.get("CF-Connecting-IP") or forwarded or self.client_address[0], time.time()
        count, since = _wrong.get(who, [0, now])
        if count >= WRONG_PASSWORDS and now - since < WRONG_REST:
            self.answer(429, b"Too many wrong passwords. Try again later.", "text/plain")
            return False
        given = self.headers.get("Authorization") or ""
        try:
            _, _, typed = base64.b64decode(given.removeprefix("Basic ")).decode().partition(":")
        except ValueError:
            typed = ""
        if PASSWORD and hmac.compare_digest(typed.encode(), PASSWORD.encode()):
            _wrong.pop(who, None)
            return True
        if given:  # (a wrong one: counted, afresh once the last run of them is old)
            if now - since >= WRONG_REST:
                count, since = 0, now
            _wrong[who] = [count + 1, since]
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="RemiDex server check", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def answer(self, code, body, kind):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in HEADERS:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":  # (for the host's own check: no password, nothing in it)
            self.answer(200, b"ok", "text/plain")
            return
        if not self.allowed():
            return
        if self.path == "/":
            self.answer(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/status":
            with _lock:
                self.answer(200, json.dumps({k: _job[k] for k in ("stage", "steps", "error")}).encode(), "application/json")
        else:
            self.answer(404, b"Not found", "text/plain")

    def do_POST(self):
        if not self.allowed():
            return
        origin, host = self.headers.get("Origin"), self.headers.get("Host")
        if self.headers.get("Content-Type") != "application/json" or (origin and origin.split("//", 1)[-1] != host):
            self.answer(403, b"Forbidden", "text/plain")
            return
        try:
            asked = as_dict(json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4000)) or b"{}"))
        except ValueError:
            asked = {}
        if self.path == "/run":
            email, password = str(asked.get("email") or "").strip(), str(asked.get("password") or "")
            with _lock:
                if _job["stage"] in ("running", "code"):
                    self.answer(409, b"A check is already running", "text/plain")
                    return
                _job.update(stage="running", steps=[], error=None, at=time.time())
            threading.Thread(target=run, args=(email if email and password else "", password), daemon=True).start()
            self.answer(200, b'{"started": true}', "application/json")
        elif self.path == "/code":
            code = re.sub(r"[\s-]", "", str(asked.get("code") or ""))
            if not re.fullmatch(r"\d{4,10}", code):
                say("Two-factor code: one came in, but it isn't digits")
                self.answer(400, b"Type the code's digits", "text/plain")
                return
            with _lock:
                waiting = _job["stage"] == "code"
                if waiting:
                    _job["stage"] = "running"
                    _codes.put(code)
            say("Two-factor code: came in, sending it to RemiliaNET" if waiting else "Two-factor code: came in, but nothing was waiting for one")
            self.answer(200 if waiting else 409, b'{"ok": true}' if waiting else b"Not waiting for a code", "application/json" if waiting else "text/plain")
        else:
            self.answer(404, b"Not found", "text/plain")

    def log_message(self, *args):
        pass  # (no request lines in the log)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow"><title>RemiDex server check</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; background: #1a1a1a; color: #d0d0d0; font: 14px/1.45 ui-monospace, Menlo, monospace; }
  main { width: min(560px, 100%); background: #303030; border: 1.5px solid #555; border-radius: 8px; padding: 22px; box-shadow: 0 10px 3px #0003; }
  h1 { margin: 0 0 6px; font-size: 18px; } p { margin: 0 0 14px; color: #a3a3a3; font-size: 12px; }
  label { display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; font-size: 12px; color: #a3a3a3; }
  input { height: 40px; padding: 0 12px; border-radius: 4px; border: 1px solid #4a4a4a; background: #262626; color: #eee; font: inherit; }
  button { height: 42px; border: 0; border-radius: 4px; padding: 0 16px; background: #3b82c4; color: #fff; font: inherit; font-weight: bold; cursor: pointer; }
  button:disabled { opacity: .55; cursor: default; } .row { display: flex; gap: 10px; flex-wrap: wrap; }
  button.plain { background: #444; }
  ol { list-style: none; padding: 0; margin: 18px 0 0; display: grid; gap: 10px; }
  li { padding: 10px 12px; border-radius: 6px; background: #262626; border-left: 4px solid #777; font-size: 12px; overflow-wrap: anywhere; }
  li.ok { border-color: #3aa84d; } li.bad { border-color: #d6372b; } li b { display: block; font-size: 13px; margin-bottom: 3px; color: #eee; }
  [hidden] { display: none !important; }
  #codeForm { border: 2px solid #e0b030; background: #3a3424; border-radius: 8px; padding: 14px; }
  #codeForm strong { display: block; margin-bottom: 10px; color: #ffd866; font-size: 15px; }
  #codeForm input { height: 52px; font-size: 24px; letter-spacing: 6px; text-align: center; }
</style></head><body><main>
  <h1>RemiDex server check</h1>
  <p>Finds out whether RemiliaNET works from this server: it loads RemiliaNET, signs in with your account, then opens that
     sign-in again and reads your game, the way the bot would. Read-only, and everything is deleted when it's done.</p>
  <form id="form">
    <label>RemiliaNET email<input id="email" type="text" autocomplete="username" spellcheck="false" autocapitalize="off"></label>
    <label>Password<input id="password" type="password" autocomplete="current-password"></label>
    <div class="row"><button id="go" type="submit">Run the check</button><button id="only" class="plain" type="button">Only check RemiliaNET loads</button></div>
  </form>
  <form id="codeForm" hidden>
    <strong>RemiliaNET wants your two-factor code. Type it here (you have 5 minutes):</strong>
    <label>Code from your authenticator app<input id="code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="12"></label>
    <button id="verify" type="submit">Send the code</button>
  </form>
  <ol id="steps"></ol>
</main>
<script>
const $ = id => document.getElementById(id);
let timer = 0, running = false;
async function post(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (!r.ok) throw new Error(await r.text() || ("HTTP " + r.status));
}
function show(s) {
  running = s.stage === "running" || s.stage === "code";
  $("go").disabled = $("only").disabled = running;
  const asking = s.stage === "code", wasAsking = !$("codeForm").hidden;
  $("codeForm").hidden = !asking;
  $("form").hidden = asking;
  document.title = asking ? "⚠ Type your code: RemiDex server check" : "RemiDex server check";
  if (asking && !wasAsking) { $("code").focus(); $("codeForm").scrollIntoView({block: "center"}); }
  $("steps").replaceChildren(...s.steps.map(x => {
    const li = document.createElement("li"), b = document.createElement("b");
    li.className = x.ok === true ? "ok" : x.ok === false ? "bad" : "";
    b.textContent = (x.ok === true ? "✓ " : x.ok === false ? "✗ " : "… ") + x.name;
    li.append(b, document.createTextNode(x.detail));
    return li;
  }));
}
async function check() {
  clearTimeout(timer);
  try { show(await (await fetch("/status", {cache: "no-store"})).json()); } catch {}
  timer = setTimeout(check, running ? 1500 : 6000);
}
async function start(withAccount) {
  const email = $("email").value.trim(), password = $("password").value;
  if (withAccount && (!email || !password)) { alert("Type your RemiliaNET email and password (or use: Only check RemiliaNET loads)."); return; }
  try { await post("/run", withAccount ? {email, password} : {}); $("password").value = ""; } catch (e) { alert(e.message); }
  check();
}
$("form").onsubmit = e => { e.preventDefault(); start(true); };
$("only").onclick = () => start(false);
$("codeForm").onsubmit = async e => {
  e.preventDefault();
  try { await post("/code", {code: $("code").value}); $("code").value = ""; } catch (err) { alert("The code didn't go through: " + err.message); }
  check();
};
check();
</script></body></html>"""


def main():
    if not PASSWORD:
        sys.exit("Set CHECK_PASSWORD first: this page signs in to RemiliaNET, so it must never be open to everyone.")
    wipe()
    say(f"RemiDex server check on port {PORT} (keeping things in {APP_DIR}, wiped after each check)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
