"""Saving a profile change on RemiliaNET the way the site's own forms do (fake site, a profile with your real shape, temp files)."""
import tempfile
from pathlib import Path
import support
pb = support.load()
ok = support.Checks()
tmp = Path(tempfile.mkdtemp())
support.patch(PROFILE_EDIT_FILE=tmp / "edit.json", ACHIEVEMENT_LIST_FILE=tmp / "list.json", STATE_FILE=tmp / "state.json", log=lambda m: None)
user = {"username": "me", "displayName": "Me", "bio": "old bio", "location": "somewhere", "pfp": {"project": "Remilio", "id": "1"},
        "achievementsDisplayed": [{"id": 130}, {"id": 138}], "friendsDisplayed": [{"userHandle": "a"}], "allAchievements": []}


class Site:
    def __init__(self, status=200):
        self.posts, self.status = [], status

    def api(self, method, path, body=None):
        return (200, {"user": user}) if method == "GET" else (self.status, {"message": "nope", "field": "pfp"} if self.status >= 400 else {})

    def signed_api(self, method, path, body=None):
        self.posts.append((path, body))
        return self.api(method, path, body)


pb.write_json(pb.PROFILE_EDIT_FILE, {"displayName": "Me", "bio": "new bio", "friendsDisplayed": ["a", "b"], "asked_at": 1})
state, site = {"handle": "me"}, Site()
ok(pb.profile_edit_waiting(), "an edit is waiting")
pb.apply_profile_edit(site, state)
path, sent = site.posts[0]
ok(path == "/api/profile/updateProfile", path)
ok(sent["bio"] == "new bio" and sent["friendsDisplayed"] == ["a", "b"] and sent["displayName"] == "Me", "the edited parts")
ok(sent["pfp"] == user["pfp"] and sent["location"] == "somewhere" and sent["userHandle"] == "me", "picture, location and handle kept as they are")
ok(sent["achievementsDisplayed"] == [130, 138], "achievements shown kept")
ok(sorted(sent) == sorted(["pfp", "bio", "displayName", "userHandle", "achievementsDisplayed", "friendsDisplayed", "location"]), "the same fields the site sends")
r = pb.read_json(pb.PROFILE_EDIT_FILE)
ok(r["ok"] and r["done_at"] and not pb.profile_edit_waiting(), "noted as done")
pb.apply_profile_edit(site, state)
ok(len(site.posts) == 1, "not sent twice")
pb.write_json(pb.PROFILE_EDIT_FILE, {"displayName": "x", "bio": "", "friendsDisplayed": [], "asked_at": 2})
pb.apply_profile_edit(Site(status=400), state)
r = pb.read_json(pb.PROFILE_EDIT_FILE)
ok(r["done_at"] and r["ok"] is False and r["field"] == "pfp" and r["error"] == "nope", f"a refusal is noted with its reason: {r['error']}")
pb.write_json(pb.PROFILE_EDIT_FILE, {"displayName": "x", "bio": "", "friendsDisplayed": [], "asked_at": 3})
pb.apply_profile_edit(Site(), {})
ok(pb.read_json(pb.PROFILE_EDIT_FILE)["ok"] is False, "without your handle it's noted as failed, not left waiting")

# the trophy shelf has its own request, and doesn't touch the rest of the profile
places = [202, 0, 166] + [0] * 12
pb.write_json(pb.PROFILE_EDIT_FILE, {"shelves": places, "asked_at": 4})
site = Site()
pb.apply_profile_edit(site, state)
ok(site.posts == [("/api/profile/updateProfile/shelf", {"shelves": places})], f"a shelf change is sent on its own: {site.posts}")
ok(pb.read_json(pb.PROFILE_EDIT_FILE)["ok"] is True, "and noted as done")
logs = []
support.patch(log=logs.append)
pb.write_json(pb.PROFILE_EDIT_FILE, {"shelves": places, "asked_at": 5})
pb.apply_profile_edit(Site(status=400), state)
r = pb.read_json(pb.PROFILE_EDIT_FILE)
ok(r["ok"] is False and r["error"] == "nope" and logs and logs[-1].startswith("Shelf: couldn't save"), f"a refused shelf is noted with its reason ({logs[-1:]})")
pb.write_json(pb.PROFILE_EDIT_FILE, {"achievementsDisplayed": [138, 130], "asked_at": 6})
site = Site()
pb.apply_profile_edit(site, state)
ok(len(site.posts) == 1 and site.posts[0][0] == "/api/profile/updateProfile" and site.posts[0][1]["achievementsDisplayed"] == [138, 130], "achievements go in the profile, in the order picked")
ok.done()
