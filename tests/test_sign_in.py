"""Signing in on RemiDex's own sign-in page: it's the page you get while the bot's signed out; a sign-in only starts then,
one at a time, only from the dashboard's own page; the password and two-factor code go to the bot on its input (never its
command line or a file); a page only shows its own sign-in's errors; and a first sign-in starts with every switch off
(temp files, a stand-in Chrome and a local test server: nothing reaches RemiliaNET)."""
import contextlib
import http.client
import http.server
import io
import json
import subprocess
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path
import support

pb = support.load()
import dashboard  # noqa: E402
from remidex import browser  # noqa: E402
from remidex.core import LOGIN_HELP  # noqa: E402

ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
support.patch(STATE_FILE=tmp / "state.json", COOKIES_FILE=tmp / "cookies.json", OVERRIDES_FILE=tmp / "overrides.json")
dashboard.LOG_FILE = tmp / "bot.log"

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


def send(body, path="/api/sign-in", origin=None):
    request = urllib.request.Request(f"http://127.0.0.1:{server.server_address[1]}{path}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Host": f"127.0.0.1:{dashboard.PORT}", "Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def get(path, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    connection.request("GET", path, headers={"Host": host or f"127.0.0.1:{dashboard.PORT}"})
    answer = connection.getresponse()
    body = answer.read()
    connection.close()
    return answer.status, answer.getheader("Location"), body


def until(test):
    for _ in range(100):
        if test():
            return True
        time.sleep(0.05)
    return False


started, release = [], threading.Event()


def fake_sign_in(email, password):  # (instead of Chrome) holds on until told, so a second try finds it still going
    started.append((email, password))
    release.wait(10)
    with dashboard._sign_in_lock:
        dashboard._sign_in.update(running=False, error=None, at=time.time())


real_sign_in, dashboard.run_sign_in = dashboard.run_sign_in, fake_sign_in
creds = {"email": " me@example.com ", "password": "hunter2"}

# ---- starting one: only while signed out, one at a time, only from the dashboard's own page
pb.write_json(tmp / "cookies.json", {"cookies": []})
pb.write_json(tmp / "state.json", {"handle": "me"})
ok(send(creds)[0] == 409 and not started, "signed in already: refused")
pb.write_json(tmp / "state.json", {"handle": "me", "needs_login": True})
ok(send({"email": "me@example.com"})[0] == 400 and send({"email": "", "password": "x"})[0] == 400 and send({"email": "a", "password": 5})[0] == 400,
   "an email and a password, both needed")
ok(send({"email": "a" * 300, "password": "x"})[0] == 400, "an email that long isn't one")
ok(send(creds, origin="http://evil.example")[0] == 403 and not started, "another site can't send one")
status, answer = send(creds)
ok(status == 200 and answer["started"] is True and isinstance(answer["attempt"], int), "signed out: it starts, and says which sign-in it is")
ok(until(lambda: started) and started == [("me@example.com", "hunter2")], "with the email (trimmed) and the password as typed")
ok(dashboard._sign_in["running"] and send(creds)[0] == 409, "one at a time")
view = json.loads(get("/api/sign-in")[2])
ok(view["running"] is True and view["attempt"] == answer["attempt"] and view["step"] is None, "the page can tell it's its own sign-in that's going")
release.set()
ok(until(lambda: not dashboard._sign_in["running"]), "and it says when it's done")

# ---- the two-factor code and starting over (a stand-in for the running sign-in)
told = []
fake_proc = types.SimpleNamespace(stdin=types.SimpleNamespace(write=lambda text: told.append(json.loads(text)), flush=lambda: None))
ok(send({"code": "123456"}, "/api/sign-in/code")[0] == 409, "a code when none's been asked for: refused")
dashboard._sign_in.update(running=True, step="code", proc=fake_proc, stopped=False)
ok(send({"code": "12ab56"}, "/api/sign-in/code")[0] == 400 and send({}, "/api/sign-in/code")[0] == 400 and not told, "a code is digits")
ok(send({"code": "123 456"}, "/api/sign-in/code") == (200, {"ok": True}) and told == [{"code": "123456"}], "the code goes to the sign-in (spaces dropped)")
ok(dashboard._sign_in["step"] == "checking" and send({"code": "123456"}, "/api/sign-in/code")[0] == 409, "and it's checked before another can go")
ok(send({"code": "123456"}, "/api/sign-in/code", origin="http://evil.example")[0] == 403, "another site can't send one")
ok(send({}, "/api/sign-in/stop") == (200, {"ok": True}) and told[-1] == {"stop": True} and dashboard._sign_in["stopped"], "start over stops it")
dashboard._sign_in.update(running=False, step=None, proc=None, stopped=False)

# ---- the pages: signed out, the sign-in page; signed in, the dashboard
pb.write_json(tmp / "state.json", {"handle": "me", "needs_login": True})
ok(get("/")[:2] == (303, "/sign-in") and get("/index.html")[:2] == (303, "/sign-in"), "signed out: the dashboard sends you to the sign-in page")
status, _, body = get("/sign-in")
ok(status == 200 and b'id="form"' in body and b'type="password"' in body and b'id="codeForm"' in body, "which is a page of its own, with the boxes")
view = json.loads(get("/api/sign-in")[2])
ok(view["signed_in"] is False and view["running"] is False and isinstance(view["hue"], (int, float)), "it can ask how it's going")
ok(get("/sign-in", host="evil.example")[0] == 403, "only at the dashboard's own names")
pb.write_json(tmp / "state.json", {"handle": "me"})
ok(get("/sign-in")[:2] == (303, "/") and get("/")[0] == 200, "signed in: the sign-in page sends you to the dashboard")
ok(json.loads(get("/api/sign-in")[2])["signed_in"] is True, "and says you're signed in")
(tmp / "cookies.json").unlink()
ok(get("/")[:2] == (303, "/sign-in"), "never signed in here: the sign-in page too")

# ---- run_sign_in: the bot's --sign-in, given the password (and later the code) on its input


class FakeBot:
    """The bot's --sign-in, as the dashboard sees it: what it's given, and what it says back (lines, or "wait for a code")."""

    def __init__(self, script):
        self.script, self.given, self.got_code = script, [], threading.Event()
        self.stdin = types.SimpleNamespace(write=self.write, flush=lambda: None)
        self.stdout = self.lines()

    def write(self, text):
        self.given.append(json.loads(text))
        if "code" in self.given[-1] or "stop" in self.given[-1]:
            self.got_code.set()

    def lines(self):
        for line in self.script:
            if line == "WAIT":
                self.got_code.wait(10)
                self.got_code.clear()
            else:
                yield line + "\n"

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


bots = []


def fake_popen(args, **kw):
    bots.append(FakeBot(script))
    bots[-1].args = args
    return bots[-1]


dashboard.subprocess = types.SimpleNamespace(Popen=fake_popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired)  # (no real bot)
script = ["2026-11-05 09:30:00 Signed in as ~me from the RemiDex dashboard.", '{"ok": true, "handle": "me"}']
real_sign_in("me@example.com", "hunter2")
ok(bots[-1].args[-1] == "--sign-in" and not any("hunter2" in str(a) for a in bots[-1].args), "the password isn't on the command line")
ok(bots[-1].given == [{"email": "me@example.com", "password": "hunter2"}], "it's on the bot's input")
ok(not dashboard._sign_in["running"] and dashboard._sign_in["error"] is None, "signed in: no error")
ok("Signed in as ~me" in (tmp / "bot.log").read_text(), "and the bot's own words go to its log (the activity feed)")
script = ['{"ok": false, "error": "Invalid username or password"}']
real_sign_in("me@example.com", "wrong")
ok(dashboard._sign_in["error"] == "Invalid username or password", "RemiliaNET's own reason is passed on")
script = []
real_sign_in("me@example.com", "x")
ok(dashboard._sign_in["error"].startswith("RemiDex couldn't finish"), "no answer at all: a plain error")

# with two-factor: it asks, the page sends the code, a wrong one is asked for again
script = ['{"step": "code", "error": null}', "WAIT", '{"step": "code", "error": "Invalid authenticator code"}', "WAIT", '{"ok": true, "handle": "me"}']
pb.write_json(tmp / "state.json", {"handle": "me", "needs_login": True})
dashboard._sign_in.update(running=True, stopped=False)
going = threading.Thread(target=real_sign_in, args=("me@example.com", "hunter2"))
going.start()
ok(until(lambda: dashboard._sign_in["step"] == "code"), "RemiliaNET wants a code: the page is told")
ok(send({"code": "111111"}, "/api/sign-in/code")[0] == 200, "the page sends it")
ok(until(lambda: dashboard._sign_in["step"] == "code" and dashboard._sign_in["error"] == "Invalid authenticator code"), "a wrong one: asked again, with why")
ok(send({"code": "222222"}, "/api/sign-in/code")[0] == 200, "and the next one goes too")
going.join(10)
ok(bots[-1].given[1:] == [{"code": "111111"}, {"code": "222222"}], "each code reaches the sign-in")
ok(not dashboard._sign_in["running"] and dashboard._sign_in["error"] is None and dashboard._sign_in["step"] is None, "then it's signed in")
# starting over at the code: no error shown for it
script = ['{"step": "code", "error": null}', "WAIT", '{"ok": false, "error": "No code came in time. Sign in again"}']
dashboard._sign_in.update(running=True, stopped=False)
going = threading.Thread(target=real_sign_in, args=("me@example.com", "hunter2"))
going.start()
ok(until(lambda: dashboard._sign_in["step"] == "code"), "asked for a code again")
send({}, "/api/sign-in/stop")
going.join(10)
ok(bots[-1].given[-1] == {"stop": True} and not dashboard._sign_in["running"] and dashboard._sign_in["error"] is None, "start over: it stops, with no error to show")
dashboard.subprocess = subprocess

# ---- --sign-in (the bot's side): JSON lines in and out


def from_dashboard(text, sign_in=None):
    out, real_in, real_with = io.StringIO(), browser.sys.stdin, browser.sign_in_with
    browser.sys.stdin = io.StringIO(text)
    if sign_in:
        browser.sign_in_with = sign_in
    try:
        with contextlib.redirect_stdout(out):
            browser.sign_in_from_dashboard()
    finally:
        browser.sys.stdin, browser.sign_in_with = real_in, real_with
    return [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]


ok(from_dashboard("") == [{"ok": False, "error": "Type your RemiliaNET email and password"}], "nothing sent: asks for them")
ok(from_dashboard("not json")[-1]["ok"] is False, "nonsense: refused")
pb.write_json(tmp / "cookies.json", {"cookies": []})
pb.write_json(tmp / "state.json", {"handle": "me"})
ok(from_dashboard(json.dumps(creds)) == [{"ok": False, "error": "RemiDex is already signed in"}], "signed in already: Chrome isn't touched (the bot may be using it)")
codes = []


def two_factor(email, password, ask_code):  # (instead of Chrome) RemiliaNET asks for a code twice: the first doesn't work
    codes.append(ask_code(None))
    codes.append(ask_code("Invalid authenticator code"))
    return ("me", None) if codes[-1] else (None, "No code came in time. Sign in again")


said = from_dashboard("\n".join([json.dumps(creds), '{"code": "111111"}', '{"code": "222222"}']) + "\n", two_factor)
ok(said == [{"step": "code", "error": None}, {"step": "code", "error": "Invalid authenticator code"}, {"ok": True, "handle": "me"}] and codes == ["111111", "222222"],
   "it asks for each code, and takes it from its input")
codes.clear()
said = from_dashboard("\n".join([json.dumps(creds), '{"code": "111111"}', '{"stop": true}']) + "\n", two_factor)
ok(codes == ["111111", None] and said[-1] == {"ok": False, "error": "No code came in time. Sign in again"}, "start over (or nothing more): it stops")

# ---- after the password: what RemiliaNET asks next (a stand-in Chrome)


def walk(pages, codes_given):
    """finish_steps over these sign-in pages ({"page", "error"}, or None: back on the site), with these codes to give."""
    site, asked, sent = object.__new__(browser.Site), [], []
    pages, codes_given = list(pages), list(codes_given)
    site.sign_in_step = lambda: pages.pop(0)
    site.send_code = lambda code: sent.append(code)
    site.answer_trust = lambda yes: sent.append(("trust", yes))

    def ask_code(error):
        asked.append(error)
        return codes_given.pop(0) if codes_given else None
    return site.finish_steps(ask_code), asked, sent


otp = {"page": "login-otp.ftl", "error": ""}
result, asked, sent = walk([otp, {"page": "login-otp.ftl", "error": "Invalid authenticator code"}, {"page": "trusted-device-register.ftl", "error": ""}, None],
                           ["111111", "222222"])
ok(result is None and asked == [None, "Invalid authenticator code"] and sent == ["111111", "222222", ("trust", False)],
   "a code, a wrong one asked for again, \"trust this device?\" answered no, then back on the site")
ok(walk([{"page": "login.ftl", "error": "Invalid username or password"}], [])[0] == "Invalid username or password", "a wrong password: RemiliaNET's own words")
ok(walk([None], [])[0] is None, "no two-factor: straight back on the site")
ok(walk([otp], [])[0] == "No code came in time. Sign in again", "no code given: it stops")
ok(walk([otp] * 9, ["1"] * 9)[0] == "Too many codes that didn't work. Sign in again", "only a few codes are tried")
ok("passkey" in walk([{"page": "webauthn-authenticate.ftl", "error": ""}], [])[0], "a passkey: says it can't use one")
ok(walk([{"page": "terms.ftl", "error": ""}], [])[0] == "RemiliaNET asked for a step RemiDex can't do yet (terms)", "anything else: says which step")
ok("Chrome window" not in walk([{"page": "terms.ftl", "error": ""}], [])[0], "and never sends you off to sign in somewhere else")

# ---- what a sign-in saves
for f in ("state.json", "cookies.json", "overrides.json"):
    (tmp / f).unlink(missing_ok=True)
ok(browser.new_here(), "nothing saved yet: someone new")
pb.write_json(tmp / "state.json", {"craft_check_at": 0, "views_check_at": 0})
ok(browser.new_here(), "still someone new after the bot starts up and saves a little of its own")
browser.finish_sign_in([{"name": "x", "value": "y"}], True)
overrides = pb.read_json(tmp / "overrides.json")
ok(overrides.get("social") and not any(overrides["social"].values()) and not any(overrides["beetleboy"].values()), "a first sign-in starts with the switches off")
ok(pb.read_json(tmp / "cookies.json") == {"cookies": [{"name": "x", "value": "y"}]}, "the sign-in is saved")
pb.write_json(tmp / "overrides.json", {"social": {"pokes": True}})
pb.write_json(tmp / "state.json", {"handle": "me", "needs_login": True, "next_slot_at": 5})
(tmp / "cookies.json").unlink()
ok(not browser.new_here(), "signed out, but been here before: not new")
browser.finish_sign_in([], False)
ok(pb.read_json(tmp / "state.json") == {"handle": "me", "next_slot_at": 5}, "signing in again lifts the pause and keeps the rest")
ok(pb.read_json(tmp / "overrides.json") == {"social": {"pokes": True}}, "and keeps your switches")
ok("RemiDex" in LOGIN_HELP and "install line" not in LOGIN_HELP, "the signed-out notice points at RemiDex")
ok(not hasattr(browser, "login") and "--login" not in (support.ROOT / "install_browser.sh").read_text()
   and "--login" not in (support.ROOT / "install_windows.py").read_text(), "there's no other way to sign in: no Chrome window")
server.shutdown()
ok.done()
