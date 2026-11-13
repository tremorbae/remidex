"""The trophy case on the dashboard: read from your public profile, its pictures fetched only from the site's public trophy
paths, and never anything that isn't yours (made-up profile, no network)."""
import json
import tempfile
import time
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
import dashboard  # noqa: E402

tmp = Path(tempfile.mkdtemp())
(tmp / "state.json").write_text(json.dumps({"handle": "me"}))
support.patch(STATE_FILE=tmp / "state.json")
PATH = dashboard.TROPHY_PATH
user = {"displayName": "Me", "trophyShelves": [202, 166, 999, 0, 0] + [0] * 5 + [159] + [0] * 4, "trophyCase": {"texture": "Graphite_Slate!", "displayName": "Graphite Slate", "category": "stone", "offsetX": 0.5, "offsetY": 0.25},
        "allTrophies": [{"id": 202, "beetle": "trophy_a", "beetle_name": "A Trophy", "icon": PATH + "me/images/a.webp"}, {"id": 166, "beetle": "trophy_b", "beetle_name": "B Trophy", "icon": "https://evil.example/b.png"},
                        {"id": 159, "beetle": "trophy_c", "beetle_name": "C Trophy", "icon": PATH + "me/../../secret"}]}
dashboard._live["me"] = (time.time(), user)

case = dashboard.trophy_case()
ok(case["owner"] == "Me" and case["total"] == 3, "who it's for, and how many you have")
ok([len(row) for row in case["shelves"]] == [5, 5, 5], "three shelves of five places")
row = case["shelves"][0]
ok(row[0] == {"id": 202, "key": "trophy_a", "name": "A Trophy", "img": "/trophy/202"}, f"a trophy in its place ({row[0]})")
ok(row[3] is None and row[4] is None and case["shelves"][1] == [None] * 5, "empty places are empty")
ok(row[2] is None, "an id that isn't one of your trophies shows nothing")
ok(row[1]["img"] is None, "a picture from anywhere but the site's trophy pictures isn't used")
ok(case["shelves"][2][0]["id"] == 159, "a trophy on the third shelf")
ok(case["case"] == {"name": "Graphite Slate", "category": "stone", "texture": "graphite_slate", "x": 30.0, "y": 15.0, "image": "/trophy-shelf/graphite_slate"}, f"the shelf's look, cleaned up ({case['case']})")
dashboard._live["me"] = (time.time(), None)
ok(dashboard.trophy_case() is None, "no profile (the site can't be reached): nothing")
dashboard._live["me"] = (time.time(), {"displayName": "Me"})
ok(dashboard.trophy_case() is None, "a profile with no trophy case: nothing")
dashboard._live["me"] = (time.time(), user)

# ---- the pictures
fetched = []


def fake_fetch(url, local):
    fetched.append((url, local.name))
    return b"RIFF\0\0\0\0WEBPdata"


dashboard.fetch_once = fake_fetch
ok(dashboard.trophy_picture(202) is not None and fetched[-1][0] == dashboard.bot.SITE + PATH + "me/images/a.webp", f"your trophy's picture is fetched from the site's trophy pictures ({fetched[-1:]})")
before = len(fetched)
ok(dashboard.trophy_picture(1) is None and dashboard.trophy_picture(166) is None and dashboard.trophy_picture(159) is None and len(fetched) == before, "someone else's, another site's and a sneaky path: refused, and nothing is fetched")
ok(dashboard.shelf_texture("graphite_slate") is not None and fetched[-1][0] == f"{dashboard.bot.SITE}{PATH}me/shelf", f"the shelf picture ({fetched[-1][0]})")
before = len(fetched)
ok(dashboard.shelf_texture("marble") is None and dashboard.shelf_texture("../x") is None and len(fetched) == before, "a shelf you don't have: refused")

# ---- what the page may ask to change
(tmp / "ach.json").write_text(json.dumps({"all": [{"id": n} for n in (130, 138, 140, 141, 142, 143, 144, 145)]}))
support.patch(MY_ACHIEVEMENTS_FILE=tmp / "ach.json")
dashboard.bot.MY_ACHIEVEMENTS_FILE = tmp / "ach.json"
dashboard._live["me"] = (time.time(), user)
request = dashboard.profile_request
mine = [202, 0, 159] + [0] * 12
ok(request({"shelves": mine}) and request({"shelves": mine})["shelves"] == mine, "a shelf with your own trophies is accepted")
ok(request({"shelves": [0] * 15}) is not None, "an empty shelf too")
ok(request({"shelves": [202, 202] + [0] * 13}) is None, "the same trophy twice is refused")
ok(request({"shelves": [999] + [0] * 14}) is None and request({"shelves": [1] + [0] * 14}) is None, "a trophy you don't have is refused")
ok(request({"shelves": [202] * 1}) is None and request({"shelves": [202] + [0] * 15}) is None, "so is a shelf that isn't 15 places")
ok(request({"shelves": ["202"] + [0] * 14}) is None and request({"shelves": [True] + [0] * 14}) is None, "and ids that aren't numbers")
dashboard._live["me"] = (time.time(), None)
ok(request({"shelves": mine}) is None and request({"shelves": [0] * 15}) is not None, "with the site out of reach, it can't tell what you own: only an empty shelf is let through")
dashboard._live["me"] = (time.time(), user)
ok(request({"achievementsDisplayed": [138, 130, 140]})["achievementsDisplayed"] == [138, 130, 140], "achievements shown: yours, in the order picked")
ok(request({"achievementsDisplayed": [130, 138, 140, 141, 142, 143, 144, 145]}) is None, "more than 7 is refused")
ok(request({"achievementsDisplayed": [130, 130]}) is None and request({"achievementsDisplayed": [1]}) is None, "so are repeats and ones you don't have")
ok(request({"achievementsDisplayed": [], "evil": 1}) is not None and "evil" not in request({"achievementsDisplayed": [], "evil": 1}), "nothing else gets through")
ok(request({}) is None and request({"shelves": mine, "bio": "x" * 500}) is None, "an empty request, or one with a part that's wrong, is refused")

# ---- over HTTP, the way the page asks
ask = support.serve(dashboard)


def get(path):
    status, headers, body = ask(path)
    return status, headers.get("Content-Type"), body


status, kind, body = get("/api/trophies")
ok(status == 200 and kind == "application/json" and json.loads(body)["total"] == 3, "the page can ask for the case")
ok(get("/trophy/202")[:2] == (200, "image/webp") and get("/trophy-shelf/graphite_slate")[:2] == (200, "image/webp"), "and for its pictures")
ok(get("/trophy/1")[0] == 404 and get("/trophy/166")[0] == 404 and get("/trophy-shelf/marble")[0] == 404 and get("/trophy/abc")[0] == 404, "but not for anything else")
dashboard._live["me"] = (time.time(), None)
ok(json.loads(get("/api/trophies")[2]) is None, "offline: it says there's nothing, so the page hides the section")
ok.done()
