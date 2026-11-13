"""The app keeping itself in good shape: the bot's own profile views don't crowd the feed or the log, hiccups it gets over
are calm notes, the log is trimmed without counting anything twice, sure crafts go before long shots, the bot's
browser is its own (not your Google Chrome) and draws calmly, and RemiDex's logo is made from RemiliaNET's whatever
size the site draws it at (temp files, fakes; nothing real is run)."""
import tempfile
import time
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
import dashboard  # noqa: E402
import store  # noqa: E402
from remidex import loop, social  # noqa: E402

tmp = Path(tempfile.mkdtemp())
logs = []
support.patch(log=logs.append, CRAFT_STATS_FILE=tmp / "stats.json", REMOVED_FILE=tmp / "removed.json")

# ---- the feed: the bot's own profile views are left out, so the newest lines are what the page shows
day = time.strftime("%Y-%m-%d")
dashboard.LOG_FILE = tmp / "feed.log"
dashboard.LOG_FILE.write_text("".join(f"{day} 10:{n:02d}:00 Profile views from the activity feed: 1.\n" for n in range(50))
                              + f"{day} 11:00:00 ~a (friend): poked.\n{day} 11:01:00 Chrome: it was closed from outside (usually the Mac sleeping or waking); opening it again in a minute.\n")
lines = dashboard.log_lines()
ok([t for _, t in lines][:1] == ["~a (friend): poked."] and len(lines) == 2, f"profile views left out ({len(lines)} lines)")
entries = dashboard.log_entries(lines, set())
ok(entries[1]["kind"] == "problem" and entries[1].get("quiet") and not entries[0].get("quiet"), "a hiccup is in the Console, marked calm")

# ---- the log: one line an hour for the bot's own profile views
social._VIEWS.update(n=0, since=0.0)
real_time = social.time.time
clock = [1000.0]
social.time.time = lambda: clock[0]
for _ in range(30):
    social.note_views(2)
    clock[0] += 60
ok(not any("Profile views" in m for m in logs), "within the hour: nothing logged")
clock[0] += 3600
social.note_views(1)
said = [m for m in logs if "Profile views" in m]
ok(len(said) == 1 and ": 61 since" in said[0], f"after an hour: one line with them all ({said})")
social.time.time = real_time

# ---- hiccups and real errors
ok(loop.hiccup(Exception("Page.wait_for_timeout: Target page, context or browser has been closed")).startswith("Chrome:"), "Chrome closed: a calm note")
ok(loop.hiccup(RuntimeError("couldn't check the sign-in (HTTP 0)")).startswith("Network:"), "no connection: a calm note")
ok(loop.hiccup(RuntimeError("couldn't check the sign-in (HTTP 500)")) is None, "a real error stays an error")

# ---- trimming the log: nothing counted twice or missed
log = tmp / "bot.log"
store.DB_FILE = tmp / "trim.db"
log.write_text("".join(f"{day} 09:{n % 60:02d}:{n // 60:02d} Hunt: found nothing.\n" for n in range(300)))
store.ingest_log(log)
hunts = store.totals()["hunts"]
ok(store.trim_log(log, most=10_000, keep=4_000) and log.stat().st_size < 4_200, f"over the limit: trimmed to its newest part ({log.stat().st_size} bytes)")
ok(Path(str(log) + ".old").read_text().count("\n") + log.read_text().count("\n") == 300, "the rest went to .old, not a line lost")
with open(log, "a") as f:
    f.write(f"{day} 23:00:00 Hunt: found nothing.\n")
store.ingest_log(log)
ok(store.totals()["hunts"] == hunts + 1, f"after trimming, only the new line is counted ({store.totals()['hunts']} hunts)")
ok(not store.trim_log(log, most=10_000, keep=4_000), "under the limit: left alone")
with open(log, "a") as f:
    f.write("".join(f"{day} 22:{n % 60:02d}:00 Hunt: found nothing.\n" for n in range(300)))
ok(not store.trim_log(log, most=10_000, keep=4_000), "not read in yet: not cut (it waits until it's counted)")

# ---- sure crafts before long shots
book, catalog = pb.recipe_book()
cards = support.cards(catalog)
moss = "pinecone or moss or gunpowder from @beetle_4 + pollen_rare"
pb.write_json(pb.CRAFT_STATS_FILE, {moss: {"n": 40, "outs": {"moss": 1}}}); pb._STATS["checked"] = 0
lotus = pb.Bag({"inventory": {"pond": 1, "pollen_rare": 1, "nectar": 1, "ladybug": 1, "green": 9, "hammer_t1": 1}, "crafting_slot3_unlocked": True,
                "hammers": [{"hammer": "hammer_t1", "break_rate": 0, "base_break_rate": 0}]}, cards)
settings = {"junk_cubes": False, "goal": None, "locked": [], "steps": None, "hammer_off": [],
            "make": {"keys": ["mars_rhino"], "recipe": None, "set_at": 1.0, "start": 0, "paused": False},
            "hammer_order": pb.HAMMERS, "hammer_limits": {h: 95 for h in pb.HAMMERS}, "hammer_timing": False}
order = [c[0] for c in pb.craft_candidates(lotus, True, settings, fallback=False)]
ok(moss in order and all(pb.long_shot(pb.recipe_named(label)) for label in order[order.index(moss):]), f"the 1-in-40 Moss smash after every sure step ({order})")
ok(pb.next_craft(lotus, True, settings, {})[0] != moss, "so the Green Beetles go to a sure step first")

# ---- the bot's browser: Playwright's own headless Chromium (not your Google Chrome), drawing calmly
from remidex import browser  # noqa: E402
launched, scripts = [], []


class Page:
    def goto(self, *a, **k):
        pass


class Context:
    pages = []

    def add_init_script(self, script):
        scripts.append(script)

    def add_cookies(self, cookies):
        pass

    def new_page(self):
        return Page()


fake = type("Playwright", (), {"chromium": type("Chromium", (), {"launch_persistent_context": staticmethod(lambda folder, **k: (launched.append((folder, k)), Context())[1])})()})()
support.patch(PROFILE_DIR=tmp / "profile", COOKIES_FILE=tmp / "cookies.json")
browser.Site(fake)
folder, how = launched[-1]
ok("channel" not in how and how.get("headless") is True, f"Playwright's own headless Chromium, not your Google Chrome ({sorted(how)})")
ok(folder == tmp / "profile" and 'PROFILE_DIR = APP_DIR / "browser-profile"' in Path(browser.__file__).read_text(), "in its own profile folder, not the one your Google Chrome made")
ok(scripts == [browser.CALM] and "requestAnimationFrame" in browser.CALM, "the page draws once a second, not 60 times")
browser.Site(fake, calm=False)
ok(scripts == [browser.CALM], "signing in leaves RemiliaNET's pages as they are")
mac = (support.ROOT / "install_browser.sh").read_text()
ok("Google Chrome.app" not in mac and "playwright install --only-shell chromium" in mac, "the Mac installer gets that Chromium, and doesn't need Google Chrome")
ok(mac.index('"$LAUNCHCTL" enable') < mac.index('"$LAUNCHCTL" bootstrap'), "the installer switches its jobs back on before starting them")
ok(all(f'"RemiDex {n}"' in mac for n in ("bot", "feed watcher", "dashboard")), "each job has a clear name in Login Items, not \"python\"")

# ---- RemiDex's logo: the flower from RemiliaNET's own logo, whatever size the site draws that at (made-up logos)
import xml.dom.minidom  # noqa: E402
small = (b'<svg width="150" height="40" viewBox="0 0 150 40" xmlns="http://www.w3.org/2000/svg"><path d="M10 5L30 5L20 35Z" fill="#1C2788"/>'
         b'<path d="M41 20L38 22Z" fill="#FFF"/><path d="M50 5L60 35Z" fill="#111111"/><path d="M130 5L140 35Z" fill="#999999"/></svg>')
large = (b'<?xml version="1.0"?><svg viewBox="0 0 2624.56 550.18" xmlns="http://www.w3.org/2000/svg"><defs><style>\n .a, .b {\n fill: #2c3382;\n }\n .c { fill: #bebebe; }\n'
         b' .d { stroke: #b0b0b0; fill: #fff; }</style></defs><path class="a" d="M747.43,65.82c26.97,0,48.87,10.48Z"/><path class="c" d="M2543.25,444.49c1,1,2,2Z"/>'
         b'<path class="b" d="M264.78,443.55c26.69-8.72Z"/><path class="d" d="M554.03,206.94l3,3Z"/></svg>')
for what, site_logo, box, blue, silver in (("drawn small, colours on its shapes", small, b'viewBox="0 0 42 40"', b"#111111", b"#999999"),
                                           ("drawn large, colours in a style sheet", large, b'viewBox="0 0 577.689 550.18"', b"#2c3382", b"#bebebe")):
    dashboard._logo.clear()
    dashboard.public_asset = lambda name, data=site_logo: data
    logo, icon = dashboard.remidex_logo(), dashboard.remidex_icon()
    xml.dom.minidom.parseString(logo), xml.dom.minidom.parseString(icon)  # (well-formed, or this raises)
    ok(logo.count(b"<path") == 2 and icon.count(b"<path") == 2 and box in logo and b"M747" not in logo and b"M50 " not in logo, f"the site's logo {what}: its flower is kept, its letters aren't")
    ok(b'fill="' + blue + b'">REMI' in logo and b'fill="' + silver + b'">DEX' in logo and (b"<style>" in logo) == (b"<style>" in site_logo), "and REMIDEX is written in that logo's own colours")
dashboard._logo.clear()
dashboard.public_asset = lambda name: None
ok(dashboard.remidex_logo() is None and dashboard.remidex_icon() is None, "no logo to be had (offline): none of ours either, not a broken one")
ok.done()
