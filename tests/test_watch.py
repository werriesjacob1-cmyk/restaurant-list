import json, os, sys, tempfile, time, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import watch

GH_LIST = {"jobs": [
  {"id": 1, "title": "Customer Success Manager", "location": {"name": "Remote - US"}, "absolute_url": "https://x/1", "updated_at": "2026-11-05T14:00:00Z", "first_published": "2026-11-05T14:00:00Z"},
  {"id": 2, "title": "Senior Software Engineer", "location": {"name": "Remote"}, "absolute_url": "https://x/2", "updated_at": "2026-11-05T14:00:00Z"},
  {"id": 3, "title": "Account Manager", "location": {"name": "London, United Kingdom"}, "absolute_url": "https://x/3", "updated_at": "2026-11-05T14:00:00Z"},
  {"id": 4, "title": "Senior Customer Success Manager", "location": {"name": "Remote, US"}, "absolute_url": "https://x/4", "updated_at": "2026-11-05T14:00:00Z"},
]}
GH_DETAIL = {"content": "&lt;p&gt;Pay $70,000 - $80,000. 3+ years of experience. Some Saturday coverage. Travel up to 10%.&lt;/p&gt;"}

class FakeNet:
    def __init__(self): self.sent = []; self.board = dict(GH_LIST)
    def get(self, url, **kw):
        if "boards-api.greenhouse.io/v1/boards/acme/jobs/" in url: return GH_DETAIL
        if "boards-api.greenhouse.io/v1/boards/acme/jobs" in url: return self.board
        raise RuntimeError("404")
    def http(self, url, data=None, **kw):
        if "ntfy.sh" in url: self.sent.append(data); return "{}"
        raise RuntimeError("blocked")

class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for n in ("STATE_PATH", "AUTO_PATH", "FILTERED_LOG"):
            setattr(watch, n, os.path.join(self.tmp, n))
        self.net = FakeNet()
        watch.get, watch.http = self.net.get, self.net.http
        os.environ["NTFY_TOPIC"] = "t"
        for k in ("TELEGRAM_BOT_TOKEN", "ADZUNA_APP_ID"): os.environ.pop(k, None)
        watch.CFG["greenhouse"] = [["Acme", "acme"]]
        for k in ("ashby", "lever", "workday", "workable", "smartrecruiters", "rippling", "bamboohr"): watch.CFG[k] = []
        watch.CFG["skip_hashes"] = []
        self.now = time.time()
        watch.discover = lambda *a, **k: []

    def test_baseline_silent_then_new_alert(self):
        r = watch.run(now=self.now)
        self.assertEqual(r["alerts"], 0, "first run must be silent")
        self.net.board["jobs"].append({"id": 5, "title": "Renewals Specialist", "location": {"name": "Remote"}, "absolute_url": "https://x/5", "first_published": watch.datetime.fromtimestamp(self.now - 600, watch.timezone.utc).isoformat()})
        r = watch.run(now=self.now + 300)
        self.assertEqual(r["alerts"], 1)
        p = self.net.sent[-1]
        self.assertIn("NEW: Acme", p["title"]); self.assertIn("Renewals Specialist", p["message"])
        self.assertEqual(p["click"], "https://x/5"); self.assertEqual(p["priority"], 5)
        r = watch.run(now=self.now + 600)
        self.assertEqual(r["alerts"], 0, "no duplicate alert")

    def test_filters_flags_and_soft(self):
        watch.run(now=self.now)
        self.net.board["jobs"] = GH_LIST["jobs"] + [
            {"id": 10, "title": "Senior Customer Success Manager II", "location": {"name": "Remote, US"}, "absolute_url": "https://x/10", "updated_at": "2026-11-05T14:00:00Z"},
            {"id": 11, "title": "Account Manager", "location": {"name": "Berlin, Germany"}, "absolute_url": "https://x/11"},
            {"id": 12, "title": "Staff Engineer", "location": {"name": "Remote"}, "absolute_url": "https://x/12"},
            {"id": 13, "title": "Client Success Manager", "location": {"name": "Nashville, TN"}, "absolute_url": "https://x/13"}]
        r = watch.run(now=self.now + 300)
        titles = sorted(p["message"].splitlines()[0] for p in self.net.sent)
        self.assertEqual(titles, ["Client Success Manager", "Senior Customer Success Manager II"])
        soft = [p for p in self.net.sent if "Senior" in p["message"]][0]
        self.assertEqual(soft["priority"], 2); self.assertIn("Title flag", soft["message"])
        m = self.net.sent[0]["message"]
        self.assertIn("WEEKEND", m); self.assertIn("travel 10%", m); self.assertIn("years: 3+", m); self.assertIn("$70,000", m)

    def test_skip_hash_and_pay_floor(self):
        watch.CFG["skip_hashes"] = [watch.chash("Acme")]
        watch.run(now=self.now)
        self.net.board["jobs"].append({"id": 20, "title": "Account Manager", "location": {"name": "Remote"}, "absolute_url": "u"})
        self.assertEqual(watch.run(now=self.now + 300)["alerts"], 0)
        watch.CFG["skip_hashes"] = []
        global GH_DETAIL
        GH_DETAIL["content"] = "&lt;p&gt;Salary $40,000 - $52,000&lt;/p&gt;"
        self.net.board["jobs"].append({"id": 21, "title": "Account Manager II", "location": {"name": "Remote"}, "absolute_url": "u2"})
        self.assertEqual(watch.run(now=self.now + 600)["alerts"], 0)

    def test_board_failure_does_not_realert(self):
        watch.run(now=self.now)
        good = self.net.board; self.net.board = None
        self.net.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")); watch.get = self.net.get
        r = watch.run(now=self.now + 300); self.assertEqual(len(r["errors"]), 1)
        watch.get = FakeNet().get
        f = FakeNet(); f.board = good; watch.get = f.get
        self.assertEqual(watch.run(now=self.now + 600)["alerts"], 0)

    def test_new_board_baselines_silently(self):
        watch.run(now=self.now)
        watch.CFG["greenhouse"].append(["Newco", "newco"])
        orig = self.net.get
        watch.get = lambda url, **kw: GH_LIST if "newco" in url else orig(url, **kw)
        self.assertEqual(watch.run(now=self.now + 300)["alerts"], 0)

    def test_helpers(self):
        self.assertTrue(watch.loc_ok("Remote - United States")); self.assertTrue(watch.loc_ok("Franklin, TN"))
        self.assertFalse(watch.loc_ok("Remote France")); self.assertFalse(watch.loc_ok("Boston, MA"))
        self.assertTrue(watch.loc_ok("US - Remote or Canada"))
        self.assertEqual(watch.find_pay("pay $75K - $85K plus"), (75000, 85000))
        self.assertEqual(watch.find_pay("$62,000 to $70,000"), (62000, 70000))
        self.assertEqual(watch.chash("Mongoose (Lever)"), watch.chash("Mongoose"))
        self.assertEqual(watch.ats_from_url("https://job-boards.greenhouse.io/acme/jobs/1"), ("greenhouse", "acme"))
        self.assertIsNone(watch.ats_from_url("https://example.com/job"))

    def test_workday_parse(self):
        calls = []
        def g(url, data=None, **kw):
            calls.append(url)
            return {"jobPostings": [{"title": "Account Manager", "externalPath": "/job/Nashville/Account-Manager_R1", "locationsText": "Nashville, TN"}]}
        watch.get = g
        jobs = watch.fetch_workday("Co", "co", "wd1", "Careers")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], "https://co.wd1.myworkdayjobs.com/en-US/Careers/job/Nashville/Account-Manager_R1")

    def test_heartbeat_and_cap(self):
        watch.run(now=self.now)
        self.net.board["jobs"] += [{"id": 100 + i, "title": f"Account Manager {i}", "location": {"name": "Remote"}, "absolute_url": f"u{i}"} for i in range(14)]
        r = watch.run(now=self.now + 300)
        self.assertEqual(r["alerts"], 10)
        self.assertTrue(any(p["title"] == "More new jobs" for p in self.net.sent))
        noon = 1788000000
        t13 = noon - (noon % 86400) + 13 * 3600 + 120
        watch.run(now=t13)
        self.assertTrue(any(p["title"] == "Daily check-in" for p in self.net.sent))

class LocationRules(unittest.TestCase):
    def test_location_rules(self):
        for ok in ["Nashville","Remote","Remote, US","United States (Remote)","United States","Remote - TN","Franklin, TN (Hybrid Position)","Americas Remote"]:
            self.assertTrue(watch.loc_ok(ok), ok)
        for bad in ["Austin, Texas, United States","Lincoln, NE, United States","Remote - TX","Remote-USA-IL","California (Remote)","Atlanta (Remote)","Toronto, Canada (Remote)","London, United Kingdom"]:
            self.assertFalse(watch.loc_ok(bad), bad)

if __name__ == "__main__":
    unittest.main(verbosity=2)
