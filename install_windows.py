"""Installs RemiDex on this Windows PC: the bot (poke_browser.py), the activity-feed watcher and the dashboard at
http://remidex.localhost:8642 (dashboard.py), kept running by a small keeper that starts when you sign in to Windows (from your
Startup folder). The first time, it asks before installing, then opens Chrome for you to sign in.

    py install_windows.py                           install, or update after you've downloaded new files
    py install_windows.py --check                   only check that this PC is ready; changes nothing
    py install_windows.py --uninstall               stop and remove RemiDex (your sign-in and history stay)
    py install_windows.py --uninstall --everything  ...and delete your sign-in and everything RemiDex saved
    add --yes to skip the questions

(install_windows.bat runs this for you: double-click it.) Everything RemiDex saves lives in %LOCALAPPDATA%\\RemiDex.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from remidex.places import APP_DIR, LOG_DIR, VENV_PYTHON  # noqa: E402  (the Windows places, when it runs on Windows)

PLAYWRIGHT_VERSION = "1.60.0"  # the same as on a Mac
PORT = 8642
FILES = ["poke_browser.py", "store.py", "dashboard.py", "dashboard.html", "signin.html", "catalog.json", "recipes.json", "quests.py"]  # what gets copied to APP_DIR
STARTUP = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Microsoft/Windows/Start Menu/Programs/Startup/RemiDex.cmd"
JOBS_FILE = APP_DIR / "jobs.json"  # the keeper's, with its process id (see remidex/keeper.py)
CHROME = [Path(os.environ.get(v) or "-") / "Google/Chrome/Application/chrome.exe" for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ON_ITS_OWN = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
UTF8 = {**os.environ, "PYTHONUTF8": "1"}  # (the log has emoji and people's names in it)
ABOUT = """
RemiDex acts on your own RemiliaNET account, from this PC. Everything starts OFF:
you turn on what you want in RemiDex, where each part has its switch.
  - BeetleBoy: claim your free beetle, daily cheese and the Cheeseman's junk, and spend cheese on hunts
  - Pokes and friend requests: poke people, send friend requests and accept the ones you get
  - BeetleCraft: craft only what you pick on the dashboard
  - Profiles: open the profiles of people who show up in the activity feed (RemiliaNET tells them you looked)
Your sign-in stays on this PC and is only ever used with RemiliaNET. py install_windows.py --uninstall removes it all.
"""


def ask(question):
    try:
        return input(question).strip()
    except EOFError:
        return ""


def ours_on_port():
    """None if the dashboard's port is free; True if RemiDex is on it; False if something else is."""
    with socket.socket() as s:
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", PORT)) != 0:
            return None
    try:
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/", headers={"Host": f"127.0.0.1:{PORT}"}), timeout=5) as r:
            return b"RemiDex" in r.read(4000)
    except OSError:
        return False


def preflight():
    """Is this PC ready? Prints a tick or a cross for each thing; returns how many crosses."""
    problems = 0

    def check(good, fine, wrong):
        nonlocal problems
        print(f"  {'✓' if good else '✗'} {fine if good else wrong}")
        problems += not good
    print("Checking this PC...")
    check(sys.platform == "win32", "Windows", "This installer is for Windows. On a Mac, run ./install_browser.sh instead.")
    check(sys.version_info >= (3, 9), f"Python {sys.version.split()[0]}", "Python 3.9 or newer is needed: get it from python.org, then run this again.")
    check(any(p.exists() for p in CHROME), "Google Chrome", "Google Chrome isn't installed. Install it from google.com/chrome, then run this again.")
    owner = ours_on_port()
    whose = "free" if owner is None else "RemiDex's own (it's already installed)"
    check(owner is not False, f"port {PORT} is {whose}", f"Something else is using port {PORT}. Close it, then run this again.")
    try:
        urllib.request.urlopen(urllib.request.Request("https://www.remilia.net", method="HEAD", headers={"User-Agent": "RemiDex installer"}), timeout=8).close()
        reachable = True
    except OSError:
        reachable = False
    check(reachable, "can reach RemiliaNET", "Can't reach https://www.remilia.net. Are you online?")
    return problems


def stop():
    """Stops a running RemiDex: the keeper, and with it the bot, the feed watcher, the dashboard and their Chrome."""
    try:
        jobs = json.loads(JOBS_FILE.read_text())
    except (OSError, ValueError):
        return
    for name in ("keeper", "bot", "feed", "dashboard"):
        if isinstance(jobs.get(name), int):
            subprocess.run(["taskkill", "/PID", str(jobs[name]), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    JOBS_FILE.unlink(missing_ok=True)


def startup_script(app_dir=APP_DIR, python=VENV_PYTHON):
    """What goes in your Startup folder, to start RemiDex's keeper when you sign in to Windows."""
    pythonw = python.with_name("pythonw.exe")
    return ("@echo off\r\n"
            "rem Starts RemiDex when you sign in to Windows. Made by install_windows.py: to remove it, run py install_windows.py --uninstall\r\n"
            "set PYTHONUTF8=1\r\n"
            f'start "" "{pythonw}" "{app_dir / "poke_browser.py"}" --keep-running\r\n')


def copy_files(source=HERE, dest=APP_DIR):
    """The app's files, into dest (the parts in remidex\\ replaced as a whole, so none from an older version is left)."""
    dest.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copy2(source / name, dest / name)
    shutil.rmtree(dest / "remidex", ignore_errors=True)
    (dest / "remidex").mkdir()
    for part in (source / "remidex").glob("*.py"):
        shutil.copy2(part, dest / "remidex" / part.name)


def signed_out():
    """Whether RemiDex has been signed out of RemiliaNET (it's paused until you sign in again)."""
    try:
        return json.loads((APP_DIR / "state.json").read_text()).get("needs_login") is True
    except (OSError, ValueError, AttributeError):
        return False


def uninstall(everything, yes):
    if everything and not yes:
        print(f"This deletes your RemiliaNET sign-in and everything RemiDex saved on this PC:\n  {APP_DIR}")
        if ask("Type yes to delete it all: ") != "yes":
            print("Nothing was changed.")
            return 0
    stop()
    STARTUP.unlink(missing_ok=True)
    if everything:
        shutil.rmtree(APP_DIR, ignore_errors=True)
        print("Removed RemiDex and everything it saved on this PC.")
    else:
        for name in FILES:
            (APP_DIR / name).unlink(missing_ok=True)
        for folder in ("remidex", "venv"):
            shutil.rmtree(APP_DIR / folder, ignore_errors=True)
        print(f"Removed RemiDex. Your sign-in, browser profile and history are still in {APP_DIR} (run this again with --uninstall --everything to delete them).")
    return 0


def install(yes):
    fresh = not (APP_DIR / "cookies.json").exists()
    if fresh and not yes:
        print(ABOUT)
        if not ask("Install RemiDex? [y/N] ").lower().startswith("y"):
            print("Nothing was installed.")
            return 0
    # Set everything up first, then restart it, so a failed update never leaves you with nothing running.
    APP_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not VENV_PYTHON.exists():
        print("Setting up Playwright (one time, about a minute)...")
        subprocess.run([sys.executable, "-m", "venv", str(APP_DIR / "venv")], check=True)
    subprocess.run([str(VENV_PYTHON), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", f"playwright=={PLAYWRIGHT_VERSION}"], check=True)
    stop()
    copy_files()
    sign_in = fresh or signed_out()  # (signing in happens on RemiDex's own sign-in page, opened at the end)
    STARTUP.parent.mkdir(parents=True, exist_ok=True)
    STARTUP.write_bytes(startup_script().encode())
    subprocess.Popen([str(VENV_PYTHON.with_name("pythonw.exe")), str(APP_DIR / "poke_browser.py"), "--keep-running"], cwd=str(APP_DIR), env=UTF8,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=ON_ITS_OWN | NO_WINDOW, close_fds=True)
    for _ in range(30):
        if ours_on_port():
            break
        time.sleep(1)
    else:
        print(f"RemiDex didn't start yet. If it doesn't within a minute, look at {LOG_DIR}", file=sys.stderr)
    if fresh:
        print(f"\nDone. RemiDex is running: http://remidex.localhost:{PORT}")
        print("Sign in there with your RemiliaNET account. Everything is OFF until you switch it on.")
        print("RemiDex starts by itself when you sign in to Windows. To stop and remove it: py install_windows.py --uninstall")
    elif sign_in:
        print(f"Updated. Sign in to RemiDex: http://remidex.localhost:{PORT}")
    else:
        print(f"Updated. RemiDex: http://remidex.localhost:{PORT}   (logs: {LOG_DIR})")
    if sign_in:
        webbrowser.open(f"http://remidex.localhost:{PORT}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Install, check or remove RemiDex on this Windows PC.")
    parser.add_argument("--check", action="store_true", help="only check that this PC is ready; changes nothing")
    parser.add_argument("--uninstall", action="store_true", help="stop and remove RemiDex (your sign-in and history stay)")
    parser.add_argument("--everything", action="store_true", help="with --uninstall: delete your sign-in and everything RemiDex saved too")
    parser.add_argument("--yes", "-y", action="store_true", help="skip the questions")
    args = parser.parse_args()
    if args.everything and not args.uninstall:
        sys.exit("--everything goes with --uninstall.")
    if args.uninstall:
        return uninstall(args.everything, args.yes)
    problems = preflight()
    if args.check:
        print("All good: this PC is ready for RemiDex." if not problems else "Fix the ✗ above, then run this again.")
        return 1 if problems else 0
    if problems:
        print("Fix the ✗ above, then run py install_windows.py again.", file=sys.stderr)
        return 1
    return install(args.yes)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")  # (the ✓ and ✗ in an old console)
    sys.exit(main())
