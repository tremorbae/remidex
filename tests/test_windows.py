"""The Windows parts, as far as a Mac can check them: where things live, the keeper (starts the parts, starts one that
stops again, only ever one keeper), and the installer's Startup entry, copying and uninstall (temp folders; nothing real
is started, stopped or removed)."""
import importlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
from remidex import keeper, places  # noqa: E402

tmp = Path(tempfile.mkdtemp())

# ---- where things live on Windows
real_platform, real_local = sys.platform, os.environ.get("LOCALAPPDATA")
sys.platform, os.environ["LOCALAPPDATA"] = "win32", str(tmp / "Local")
importlib.reload(places)
win = (places.WINDOWS, places.APP_DIR, places.VENV_PYTHON, places.LOG_FILE)
sys.platform = real_platform
if real_local is None:
    del os.environ["LOCALAPPDATA"]
else:
    os.environ["LOCALAPPDATA"] = real_local
importlib.reload(places)
ok(win[0] and win[1] == tmp / "Local" / "RemiDex" and win[2] == win[1] / "venv" / "Scripts" / "python.exe" and win[3] == win[1] / "logs" / "remilianet-poke-browser.log",
   f"on Windows: %LOCALAPPDATA%\\RemiDex, its own Python, the logs inside it ({win[1:]})")
ok(not places.WINDOWS and "Library" in str(places.APP_DIR), "and on a Mac, as before")

# ---- the keeper
keeper.APP_DIR, keeper.JOBS_FILE, keeper.LOCK_FILE, keeper.RESTART_AFTER = tmp, tmp / "jobs.json", tmp / "keeper.lock", 0.5
log = tmp / "logs" / "test.log"
quick = [sys.executable, "-c", "print('up', flush=True)"]  # stops at once
slow = [sys.executable, "-c", "import time; print('up', flush=True); time.sleep(30)"]
result = {}
run = threading.Thread(target=lambda: result.update(done=keeper.keep_running({"quick": (quick, log), "slow": (slow, log)}, rounds=12)))
run.start()
time.sleep(1.2)
ok(keeper.only_one() is None, "a second keeper can't start while one runs")
jobs = json.loads(keeper.JOBS_FILE.read_text())
run.join(30)
ups = log.read_text().count("up")
ok(result.get("done") is True and jobs.get("keeper") == os.getpid() and "slow" in jobs, f"it notes what it runs ({jobs})")
ok(ups >= 3, f"a part that stops is started again ({ups - 1} starts of the quick one)")
ok(log.read_text().count("up") == ups, "and when the keeper stops, so do its parts")
lock = keeper.only_one()
ok(lock is not None, "once it's stopped, a keeper can start again")
lock.close()
keeper.JOBS_FILE.write_text(json.dumps({"keeper": os.getpid(), "bot": 999999, "dashboard": "x"}))
ok(keeper.running() == {"keeper": True, "bot": False, "feed": False, "dashboard": False}, f"which parts are running ({keeper.running()})")

# ---- the installer
sys.path.insert(0, str(support.ROOT))
import install_windows as setup  # noqa: E402
app = tmp / "App"
script = setup.startup_script(app, app / "venv" / "Scripts" / "python.exe")
ok(script.endswith("\r\n") and f'"{app / "venv" / "Scripts" / "pythonw.exe"}"' in script and f'"{app / "poke_browser.py"}" --keep-running' in script and "PYTHONUTF8=1" in script,
   "the Startup entry starts the keeper, without a window, in UTF-8")
(app / "remidex").mkdir(parents=True)
(app / "remidex" / "gone.py").write_text("an old part")
setup.copy_files(support.ROOT, app)
ok(all((app / f).exists() for f in setup.FILES) and (app / "remidex" / "keeper.py").exists() and not (app / "remidex" / "gone.py").exists(),
   "the files are copied, and an old part is cleared out")
(app / "cookies.json").write_text("{}")
(app / "venv").mkdir()
startup = tmp / "Startup" / "RemiDex.cmd"
startup.parent.mkdir()
startup.write_text(script)
setup.APP_DIR, setup.STARTUP, setup.JOBS_FILE, setup.stop = app, startup, tmp / "no-jobs.json", lambda: None
setup.uninstall(False, True)
ok(not startup.exists() and not (app / "poke_browser.py").exists() and not (app / "venv").exists() and (app / "cookies.json").exists(),
   "uninstall: gone from Startup and the app removed, your sign-in kept")
setup.uninstall(True, True)
ok(not app.exists(), "--everything: all of it gone")
ok.done()
