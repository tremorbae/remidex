"""Windows' stand-in for launchd: keeps the bot, the activity-feed watcher and the dashboard running while you're signed in
to Windows. The installer starts it at sign-in (from your Startup folder) as `pythonw poke_browser.py --keep-running`.
Each part's output goes to its log; one that stops is started again RESTART_AFTER seconds later, like launchd does."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from remidex.places import APP_DIR, DASH_LOG, LOG_FILE, VENV_PYTHON, WINDOWS

JOBS_FILE = APP_DIR / "jobs.json"  # {part: its process id}, for the dashboard and the installer
LOCK_FILE = APP_DIR / "keeper.lock"  # held while a keeper runs, so there's only ever one
RESTART_AFTER = 10
LOOK_EVERY = 5


def commands(python=None):
    """The three parts: the bot, the feed watcher and the dashboard, each with the log its output goes to."""
    python, here = str(python or VENV_PYTHON), Path(__file__).resolve().parent.parent
    return {"bot": ([python, str(here / "poke_browser.py"), "--scheduled"], LOG_FILE),
            "feed": ([python, str(here / "poke_browser.py"), "--watch-feed"], LOG_FILE),
            "dashboard": ([python, str(here / "dashboard.py"), "--serve"], DASH_LOG)}


def only_one():
    """The lock that says a keeper is running (an open file, held until this one stops), or None if one already is."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "a+b")
    try:
        if WINDOWS:
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    return lock


def start(command, log_file):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "ab") as out:  # (the part keeps its own handle on it)
        return subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=str(APP_DIR),
                                env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},  # (UTF-8: the log has emoji and names in it)
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def keep_running(jobs=None, rounds=None):
    """Starts each part and starts it again when it stops, until Windows signs you out (rounds: how many looks, for a
    test). Leaves at once if another keeper is already running."""
    lock = only_one()
    if lock is None:
        return False
    jobs, procs, stopped_at = jobs or commands(), {}, {}
    try:
        while rounds is None or rounds > 0:
            for name, (command, log_file) in jobs.items():
                proc = procs.get(name)
                if proc is not None and proc.poll() is None:
                    continue
                if proc is not None and name not in stopped_at:
                    stopped_at[name] = time.time()
                if time.time() - stopped_at.get(name, 0) >= RESTART_AFTER or proc is None:
                    procs[name], _ = start(command, log_file), stopped_at.pop(name, None)
            JOBS_FILE.write_text(json.dumps({"keeper": os.getpid(), **{name: p.pid for name, p in procs.items() if p.poll() is None}}))
            if rounds is not None:
                rounds -= 1
            time.sleep(LOOK_EVERY if rounds is None else .2)
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.terminate()
        lock.close()
    return True


def alive(pid):
    """Whether a process is still running. (On Windows by asking for its exit code: os.kill there would end it.)"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if WINDOWS:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def running():
    """Which parts the keeper has running now: {"keeper", "bot", "feed", "dashboard": True or False}."""
    try:
        jobs = json.loads(JOBS_FILE.read_text())
    except (OSError, ValueError):
        jobs = {}
    jobs = jobs if isinstance(jobs, dict) else {}
    return {name: alive(jobs.get(name)) for name in ("keeper", "bot", "feed", "dashboard")}


if __name__ == "__main__":
    keep_running() or sys.exit("RemiDex is already running.")
