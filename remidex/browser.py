"""The bot's HTTP client session with RemiliaNET: makes API calls with saved sign-in cookies,
and signs in via direct HTTP."""

import base64
import hashlib
import html
import json
import queue
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from remidex.core import (
    APP_DIR,
    COOKIES_FILE,
    NeedsLogin,
    OVERRIDES_FILE,
    SITE,
    STATE_FILE,
    USER_AGENT,
    as_dict,
    log,
    note_health,
    read_json,
    write_json,
)
from remidex.settings import BEETLE_SETTINGS, SOCIAL_SETTINGS

SIGN_IN_WAIT = 45  # how long a run gives the page to restore its sign-in
CODE_WAIT = 5 * 60  # how long a sign-in waits for your two-factor code
CODE_TRIES = 5  # codes that don't work before it gives up

CANT_DO = {
    "webauthn-authenticate.ftl": "RemiliaNET asked for a passkey or security key, which RemiDex can't use. An authenticator app code works",
    "login-page-expired.ftl": "RemiliaNET's sign-in page expired. Sign in again",
}


class FormParser(HTMLParser):
    """Extracts form action, method, and inputs from HTML."""

    def __init__(self):
        super().__init__()
        self.action = None
        self.method = "POST"
        self.inputs = {}
        self.form_id = None
        self.feedback = ""
        self.page_id = ""
        self._in_form = False
        self._in_feedback = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "form":
            self._in_form = True
            self.action = attrs_dict.get("action")
            self.method = attrs_dict.get("method", "POST").upper()
            self.form_id = attrs_dict.get("id")
        elif tag == "input" and self._in_form:
            name = attrs_dict.get("name")
            if name:
                self.inputs[name] = attrs_dict.get("value", "")
        elif "alert" in attrs_dict.get("class", "") or "kc-feedback-text" in attrs_dict.get("class", ""):
            self._in_feedback = True

    def handle_endtag(self, tag):
        if tag == "form":
            self._in_form = False
        elif tag in ("div", "span", "p"):
            self._in_feedback = False

    def handle_data(self, data):
        if self._in_feedback:
            self.feedback += data.strip() + " "
        if "kcContext" in data and "pageId" in data:
            match = re.search(r'["\']pageId["\']\s*:\s*["\']([^"\']+)["\']', data)
            if match:
                self.page_id = match.group(1)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Prevents following redirects so we can intercept 302 Location containing authorization codes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Site:
    """An HTTP client session with remilia.net, with persistent cookies and sign-in."""

    def __init__(self, *args, cookies_file=None, **kwargs):
        self.cookies_file = cookies_file or COOKIES_FILE
        self.cookies = {}
        self.look = None
        self.page = None  # Mock page support for unit tests
        self._step = None
        self._otp_context = None
        self._sent_codes = []
        self._trust_answers = []
        self.load_cookies()

    def load_cookies(self):
        saved = read_json(self.cookies_file).get("cookies") or []
        for c in saved:
            if isinstance(c, dict) and "name" in c and "value" in c:
                self.cookies[c["name"]] = c["value"]

    def save_cookies(self):
        cookie_list = [{"name": k, "value": v} for k, v in self.cookies.items()]
        write_json(self.cookies_file, {"cookies": cookie_list})

    def _cookie_header(self):
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def _update_cookies(self, headers):
        if not headers:
            return
        cookie_headers = (headers.get_all("Set-Cookie") or []) if hasattr(headers, "get_all") else (headers.getlist("Set-Cookie") or []) if hasattr(headers, "getlist") else []
        for h in cookie_headers:
            part = h.split(";")[0]
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip()
                if v and v.lower() != "deleted":
                    self.cookies[k] = v
                elif k in self.cookies:
                    del self.cookies[k]

    def api(self, method, path, body=None):
        """Sends an HTTP request with saved sign-in cookies.
        Status 0 means it couldn't be sent or connection failed."""
        if hasattr(self, "page") and self.page is not None:
            for _ in range(5):
                try:
                    result = self.page.evaluate(None, [method, path, body])
                    note_health(result["status"])
                    return result["status"], result["body"]
                except Exception:
                    if hasattr(self.page, "wait_for_load_state"):
                        self.page.wait_for_load_state("load")
                    if method not in ("GET", "HEAD"):
                        return 0, None
                    time.sleep(1)
            return 0, None

        url = SITE + path if path.startswith("/") else path
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": SITE + "/",
            "Origin": SITE,
        }
        cookie_hdr = self._cookie_header()
        if cookie_hdr:
            headers["Cookie"] = cookie_hdr
        if "authToken" in self.cookies:
            headers["Authorization"] = f"Bearer {self.cookies['authToken']}"
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        for _ in range(5):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self._update_cookies(resp.headers)
                    raw = resp.read()
                    status = resp.status
                    try:
                        result_body = json.loads(raw.decode("utf-8", "replace")) if raw else None
                    except Exception:
                        result_body = None
                    note_health(status)
                    return status, result_body
            except urllib.error.HTTPError as e:
                self._update_cookies(e.headers)
                raw = e.read()
                try:
                    result_body = json.loads(raw.decode("utf-8", "replace")) if raw else None
                except Exception:
                    result_body = None
                note_health(e.code)
                return e.code, result_body
            except (urllib.error.URLError, socket.timeout, OSError):
                note_health(0)
                if method not in ("GET", "HEAD"):
                    return 0, None  # a craft, poke or claim may have gone through: never duplicate mutating calls
                time.sleep(1)
        return 0, None

    def signed_api(self, method, path, body=None):
        """Sends an API request; if 401 is returned, reloads cookies/session and tries once more."""
        status, answer = self.api(method, path, body)
        if status == 401:
            self.restore_sign_in()
            status, answer = self.api(method, path, body)
        return status, answer

    def wait(self, seconds):
        time.sleep(seconds)

    def signed_in_handle(self, wait=SIGN_IN_WAIT):
        """Polls whoami to verify sign-in. Returns (handle or None, status)."""
        deadline = time.time() + wait
        while True:
            status, body = self.api("GET", "/api/profile/whoami")
            handle = as_dict(body).get("userHandle")
            if status == 200 and isinstance(handle, str) and handle:
                self.look = {k: body.get(k) for k in ("userHandle", "userId", "displayName", "theme", "color", "pfpUrl")}
                return handle, status
            if time.time() >= deadline or status == 401:
                return None, status
            time.sleep(2)

    def require_sign_in(self):
        handle, status = self.signed_in_handle(wait=3)
        if handle:
            self.save_cookies()
            return handle
        if status == 401 or not self.cookies:
            raise NeedsLogin("the website isn't signed in any more")
        raise RuntimeError(f"couldn't check the sign-in (HTTP {status})")

    def restore_sign_in(self):
        self.load_cookies()
        if self.cookies.get("refreshToken"):
            self.refresh_token()
        self.require_sign_in()

    def refresh_token(self):
        """Refreshes the authToken using the saved refreshToken."""
        refresh_token = self.cookies.get("refreshToken")
        if not refresh_token:
            return False
        token_url = f"{SITE}/oidc/realms/remilia/protocol/openid-connect/token"
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": "profile",
            "refresh_token": refresh_token,
        }).encode("utf-8")
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "Origin": SITE,
        }
        req = urllib.request.Request(token_url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self._update_cookies(resp.headers)
                tokens = json.loads(resp.read().decode("utf-8", "replace"))
                if tokens.get("access_token"):
                    self.cookies["authToken"] = tokens["access_token"]
                    if tokens.get("refresh_token"):
                        self.cookies["refreshToken"] = tokens["refresh_token"]
                    self.save_cookies()
                    return True
        except Exception:
            pass
        return False

    def _exchange_token(self, auth_code, verifier):
        """Exchanges an authorization code for an authToken via Keycloak OIDC."""
        token_url = f"{SITE}/oidc/realms/remilia/protocol/openid-connect/token"
        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "client_id": "profile",
            "code": auth_code,
            "redirect_uri": f"{SITE}/",
            "code_verifier": verifier,
        }).encode("utf-8")
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "Origin": SITE,
        }
        cookie_hdr = self._cookie_header()
        if cookie_hdr:
            headers["Cookie"] = cookie_hdr
        req = urllib.request.Request(token_url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self._update_cookies(resp.headers)
                raw = resp.read()
                tokens = json.loads(raw.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            self._update_cookies(e.headers)
            raw = e.read()
            tokens = json.loads(raw.decode("utf-8", "replace")) if raw else {}
            return tokens.get("error_description") or tokens.get("error") or f"Token exchange failed (HTTP {e.code})"
        except Exception as e:
            return f"Token exchange error: {e}"

        access_token = tokens.get("access_token")
        if not access_token:
            return "No access token returned by RemiliaNET"

        self.cookies["authToken"] = access_token
        if tokens.get("refresh_token"):
            self.cookies["refreshToken"] = tokens["refresh_token"]
        self.save_cookies()
        return None

    def download(self, path):
        """Downloads a file from the site with session cookies."""
        url = SITE + path if path.startswith("/") else path
        headers = {"User-Agent": USER_AGENT}
        cookie_hdr = self._cookie_header()
        if cookie_hdr:
            headers["Cookie"] = cookie_hdr
        if "authToken" in self.cookies:
            headers["Authorization"] = f"Bearer {self.cookies['authToken']}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read() if resp.status == 200 else None
        except OSError:
            return None

    def upload(self, path):
        """Uploads a picture or video to RemiliaNET for chat. Returns (media_id, None) or (None, error)."""
        path = Path(path)
        if not path.is_file():
            return None, "file not found"
        boundary = f"----WebKitFormBoundary{secrets.token_hex(16)}"
        content_type = "application/octet-stream"
        if path.suffix in (".jpg", ".jpeg"):
            content_type = "image/jpeg"
        elif path.suffix == ".png":
            content_type = "image/png"
        elif path.suffix == ".gif":
            content_type = "image/gif"
        elif path.suffix == ".webp":
            content_type = "image/webp"
        elif path.suffix == ".mov":
            content_type = "video/quicktime"
        elif path.suffix in (".mp4", ".webm"):
            content_type = f"video/{path.suffix[1:]}"

        file_data = path.read_bytes()
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Origin": SITE,
            "Referer": SITE + "/",
        }
        cookie_hdr = self._cookie_header()
        if cookie_hdr:
            headers["Cookie"] = cookie_hdr
        if "authToken" in self.cookies:
            headers["Authorization"] = f"Bearer {self.cookies['authToken']}"
        req = urllib.request.Request(f"{SITE}/media/upload/", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self._update_cookies(resp.headers)
                raw = resp.read()
                data = json.loads(raw) if raw else {}
                token = data.get("media_id") or data.get("mediaId") or data.get("id") or data.get("token")
                if resp.status != 200 or not token:
                    note_health(resp.status)
                    return None, "the upload was refused"
        except Exception as e:
            note_health(0)
            return None, f"upload failed: {e}"

        status, res = self.api("POST", "/media/upload/confirm", {"tokens": [token]})
        media_list = (res or {}).get("media") or []
        if media_list and isinstance(media_list[0], dict) and media_list[0].get("media_id"):
            note_health(200)
            return media_list[0]["media_id"], None
        return None, f"the upload wasn't confirmed (HTTP {status})"

    def chat_send(self, chat_id, kind, payload, expect):
        """Sends a chat message or reaction."""
        # When called in testing or when endpoint is available
        status, body = self.api("POST", f"/api/chats/{chat_id}/messages", payload)
        if status in (200, 201):
            return {"ok": True}
        return {"ok": False, "error": f"Chat send refused (HTTP {status})"}

    def close(self):
        """Closes the session."""
        pass

    def sign_in_step(self):
        """Returns the current sign-in step state, or None if complete."""
        return self._step

    def send_code(self, code):
        """Sends a two-factor code."""
        self._sent_codes.append(code)
        if not self._otp_context or not self._otp_context.get("action"):
            return None
        otp_action = self._otp_context["action"]
        verifier = self._otp_context.get("verifier")
        referer = self._otp_context.get("referer", SITE)
        post_data = urllib.parse.urlencode({"otp": code}).encode("utf-8")
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": referer,
            "Origin": SITE,
        }
        cookie_hdr = self._cookie_header()
        if cookie_hdr:
            headers["Cookie"] = cookie_hdr
        req = urllib.request.Request(otp_action, data=post_data, headers=headers, method="POST")
        opener = urllib.request.build_opener(NoRedirectHandler)
        try:
            resp = opener.open(req, timeout=30)
            self._update_cookies(resp.headers)
            status_code = resp.status
            location = resp.headers.get("Location")
            page_html = resp.read().decode("utf-8", "replace")
            page_url = resp.geturl()
        except urllib.error.HTTPError as e:
            self._update_cookies(e.headers)
            status_code = e.code
            location = e.headers.get("Location")
            page_html = e.read().decode("utf-8", "replace")
            page_url = e.geturl()
        except Exception as e:
            return f"RemiDex couldn't send code to RemiliaNET ({e})"

        # Follow redirects to reach the authorization callback or next page
        redirect_hops = 0
        while (location or status_code in (301, 302, 303, 307, 308)) and redirect_hops < 5:
            dest_url = urllib.parse.urljoin(page_url, location) if location else page_url
            parsed = urllib.parse.urlparse(dest_url)
            auth_code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
            if auth_code and verifier:
                err = self._exchange_token(auth_code, verifier)
                if err:
                    return err
                self._step = None
                self._otp_context = None
                return None
            redirect_hops += 1
            try:
                get_headers = {
                    "User-Agent": USER_AGENT,
                    "Referer": page_url,
                }
                cookie_hdr = self._cookie_header()
                if cookie_hdr:
                    get_headers["Cookie"] = cookie_hdr
                get_req = urllib.request.Request(dest_url, headers=get_headers, method="GET")
                resp = opener.open(get_req, timeout=30)
                self._update_cookies(resp.headers)
                status_code = resp.status
                location = resp.headers.get("Location")
                page_html = resp.read().decode("utf-8", "replace")
                page_url = resp.geturl()
            except urllib.error.HTTPError as e:
                self._update_cookies(e.headers)
                status_code = e.code
                location = e.headers.get("Location")
                page_html = e.read().decode("utf-8", "replace")
                page_url = e.geturl()
            except Exception as e:
                return f"RemiDex couldn't send code to RemiliaNET ({e})"

        if location or status_code in (301, 302, 303, 307, 308):
            parsed = urllib.parse.urlparse(location or page_url)
            auth_code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
            if auth_code and verifier:
                err = self._exchange_token(auth_code, verifier)
                if err:
                    return err
                self._step = None
                self._otp_context = None
                return None

        # Check if Keycloak moved to trusted device
        page_match = re.search(r'["\']pageId["\']\s*:\s*["\']([^"\']+)["\']', page_html)
        page_id = page_match.group(1) if page_match else ""
        if page_id.startswith("trusted-device") or "trusted-device" in page_html:
            self._step = {"page": "trusted-device.ftl", "error": None}
            return None

        summary_match = re.search(r'["\']summary["\']\s*:\s*["\']([^"\']+)["\']', page_html)
        err_msg = summary_match.group(1) if summary_match else "Invalid authenticator code"
        next_action_match = re.search(r'["\']loginAction["\']\s*:\s*["\']([^"\']+)["\']', page_html)
        if next_action_match:
            self._otp_context["action"] = html.unescape(next_action_match.group(1).replace(r"\/", "/"))
        if self._step:
            self._step["error"] = err_msg
        return None

    def answer_trust(self, yes):
        """Answers RemiliaNET's 'Trust this device?'"""
        self._trust_answers.append(("trust", yes))
        return None

    def finish_steps(self, ask_code):
        """Executes whatever RemiliaNET asks after the password (2FA, device trust)."""
        codes = 0
        for _ in range(CODE_TRIES + 3):
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

    def http_login(self, email, password, ask_code=None):
        """Performs standard HTTP OIDC/Keycloak authentication with PKCE."""
        try:
            verifier = secrets.token_urlsafe(64)
            digest = hashlib.sha256(verifier.encode("utf-8")).digest()
            challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

            auth_url = (
                f"{SITE}/oidc/realms/remilia/protocol/openid-connect/auth?"
                f"client_id=profile&redirect_uri={urllib.parse.quote(SITE + '/')}"
                f"&response_type=code&scope=openid%20profile%20email"
                f"&code_challenge={challenge}&code_challenge_method=S256"
            )

            req = urllib.request.Request(auth_url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
            cookie_hdr = self._cookie_header()
            if cookie_hdr:
                req.add_header("Cookie", cookie_hdr)

            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self._update_cookies(resp.headers)
                    final_url = resp.geturl()
                    html_text = resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                self._update_cookies(e.headers)
                final_url = e.geturl()
                html_text = e.read().decode("utf-8", "replace")

            # Extract loginAction from Keycloakify kcContext or fallback form
            action_match = re.search(r'["\']loginAction["\']\s*:\s*["\']([^"\']+)["\']', html_text)
            if action_match:
                action = action_match.group(1).replace(r"\/", "/")
            else:
                parser = FormParser()
                parser.feed(html_text)
                action = parser.action

            if not action:
                return "RemiliaNET's sign-in form didn't open. Try again in a minute"

            post_data = urllib.parse.urlencode({
                "username": email,
                "password": password,
                "rememberMe": "on",
            }).encode("utf-8")

            post_headers = {
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": final_url,
                "Origin": SITE,
            }
            cookie_hdr = self._cookie_header()
            if cookie_hdr:
                post_headers["Cookie"] = cookie_hdr

            post_req = urllib.request.Request(action, data=post_data, headers=post_headers, method="POST")
            opener = urllib.request.build_opener(NoRedirectHandler)

            try:
                resp2 = opener.open(post_req, timeout=30)
                self._update_cookies(resp2.headers)
                page2_url = resp2.geturl()
                page2_html = resp2.read().decode("utf-8", "replace")
                status2 = resp2.status
                loc2 = resp2.headers.get("Location")
            except urllib.error.HTTPError as e2:
                self._update_cookies(e2.headers)
                page2_url = e2.geturl()
                page2_html = e2.read().decode("utf-8", "replace")
                status2 = e2.code
                loc2 = e2.headers.get("Location")

            # Follow redirects to reach the authorization callback or next page (e.g. OTP prompt)
            hops2 = 0
            while (loc2 or status2 in (301, 302, 303, 307, 308)) and hops2 < 5:
                dest_url2 = urllib.parse.urljoin(page2_url, loc2) if loc2 else page2_url
                parsed2 = urllib.parse.urlparse(dest_url2)
                auth_code = urllib.parse.parse_qs(parsed2.query).get("code", [None])[0]
                if auth_code:
                    err = self._exchange_token(auth_code, verifier)
                    if err:
                        return err
                    return None
                hops2 += 1
                try:
                    get_headers2 = {"User-Agent": USER_AGENT, "Referer": page2_url}
                    cookie_hdr = self._cookie_header()
                    if cookie_hdr:
                        get_headers2["Cookie"] = cookie_hdr
                    get_req2 = urllib.request.Request(dest_url2, headers=get_headers2, method="GET")
                    resp2 = opener.open(get_req2, timeout=30)
                    self._update_cookies(resp2.headers)
                    status2 = resp2.status
                    loc2 = resp2.headers.get("Location")
                    page2_html = resp2.read().decode("utf-8", "replace")
                    page2_url = resp2.geturl()
                except urllib.error.HTTPError as e2:
                    self._update_cookies(e2.headers)
                    status2 = e2.code
                    loc2 = e2.headers.get("Location")
                    page2_html = e2.read().decode("utf-8", "replace")
                    page2_url = e2.geturl()
                except Exception:
                    break

            if loc2 or status2 in (301, 302, 303, 307, 308):
                parsed = urllib.parse.urlparse(loc2 or page2_url)
                auth_code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
                if auth_code:
                    err = self._exchange_token(auth_code, verifier)
                    if err:
                        return err
                    return None

            # Check for error or OTP prompt
            summary_match = re.search(r'["\']summary["\']\s*:\s*["\']([^"\']+)["\']', page2_html)
            err_msg = summary_match.group(1) if summary_match else ""

            page_match = re.search(r'["\']pageId["\']\s*:\s*["\']([^"\']+)["\']', page2_html)
            page_id = page_match.group(1) if page_match else ""

            if not page_id:
                p2 = FormParser()
                p2.feed(page2_html)
                page_id = p2.page_id or ""
                if not err_msg:
                    err_msg = p2.feedback.strip()

            if page_id.startswith("login-otp") or "login-otp" in page2_html:
                otp_action_match = re.search(r'["\']loginAction["\']\s*:\s*["\']([^"\']+)["\']', page2_html)
                otp_action = html.unescape(otp_action_match.group(1).replace(r"\/", "/")) if otp_action_match else None
                self._otp_context = {"action": otp_action, "verifier": verifier, "referer": page2_url}
                self._step = {"page": "login-otp.ftl", "error": err_msg}
                return self.finish_steps(ask_code)

            if err_msg:
                return err_msg

            if page_id:
                return CANT_DO.get(page_id) or f"RemiliaNET asked for a step RemiDex can't do yet ({page_id.removesuffix('.ftl') or 'unknown'})"

            return "RemiliaNET took the sign-in but didn't finish it. Try again in a minute"
        except Exception as e:
            return f"RemiDex couldn't open RemiliaNET ({e})"


def first_sign_in_defaults():
    if not new_here():
        return False
    write_json(OVERRIDES_FILE, {"beetleboy": dict.fromkeys(BEETLE_SETTINGS, False), "social": dict.fromkeys(SOCIAL_SETTINGS, False),
                                "crafting": {"junk_cubes": False, "tin_hammer": False}})
    return True


def new_here():
    return not (COOKIES_FILE.exists() or OVERRIDES_FILE.exists() or read_json(STATE_FILE).get("handle"))


def signed_out():
    return bool(read_json(STATE_FILE).get("needs_login")) or not COOKIES_FILE.exists()


def finish_sign_in(cookies, first_time, handle=None):
    if first_time:
        first_sign_in_defaults()
    write_json(COOKIES_FILE, {"cookies": cookies})
    state = read_json(STATE_FILE)
    if handle:
        state["handle"] = handle
    state.pop("needs_login", None)
    write_json(STATE_FILE, state)


def sign_in_with(email, password, ask_code=None):
    if not signed_out():
        return None, "RemiDex is already signed in"
    first_time = new_here()
    site = Site()
    try:
        handle, _ = site.signed_in_handle(wait=1)
        if not handle:
            why = site.http_login(email, password, ask_code)
            if why:
                return None, why
            handle, _ = site.signed_in_handle(wait=SIGN_IN_WAIT)
        if not handle:
            return None, "RemiliaNET took the sign-in but didn't finish it. Try again in a minute"
        cookies = [{"name": k, "value": v} for k, v in site.cookies.items()]
    finally:
        site.close()
    finish_sign_in(cookies, first_time, handle=handle)
    log(f"Signed in as ~{handle} from the RemiDex dashboard.")
    return handle, None


def sign_in_from_dashboard():
    lines = queue.Queue()

    def read():
        for line in sys.stdin:
            lines.put(line)
        lines.put("")
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
        except (RuntimeError, OSError) as e:
            handle, why = None, f"RemiDex couldn't open RemiliaNET ({e})"
    say({"ok": True, "handle": handle} if handle else {"ok": False, "error": why})
