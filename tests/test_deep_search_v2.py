import json
import time
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

import brave_budget as budget
import discovery_v2 as d
import deep_search_v2 as report

class Budget(unittest.TestCase):
    def test_monthly_fails_closed(self):
        t=datetime(2026,11,6,tzinfo=timezone.utc).timestamp()
        l,n=budget.reserve({"month":"2026-11","reserved":99},t)
        self.assertEqual(n,1)
        self.assertEqual(l["reserved"],100)
        l,n=budget.reserve(l,t)
        self.assertEqual(n,0)
        self.assertEqual(l["reserved"],100)
    def test_month_rollover(self):
        t=datetime(2026,12,1,tzinfo=timezone.utc).timestamp()
        l,n=budget.reserve({"month":"2026-11","reserved":100},t)
        self.assertEqual(n,3)
        self.assertEqual(l["month"],"2026-12")
    def test_cannot_raise_caps(self):
        t=datetime(2026,11,6,tzinfo=timezone.utc).timestamp()
        with self.assertRaises(ValueError):budget.reserve({},t,request_count=4)
        with self.assertRaises(ValueError):budget.reserve({},t,monthly_limit=101)

class Discovery(unittest.TestCase):
    def test_rotate_searches(self):
        q=d.select_queries(date(2026,11,6))
        self.assertEqual(len(q),3)
        self.assertEqual(len(set(q)),3)
        self.assertNotEqual(q,d.select_queries(date(2026,11,7)))
    def test_parse_urls(self):
        samples={
            "https://job-boards.greenhouse.io/acme/jobs/123":("greenhouse","acme"),
            "https://jobs.ashbyhq.com/team/jobid":("ashby","team"),
            "https://jobs.lever.co/company/123":("lever","company"),
            "https://ats.rippling.com/acme/jobs/a":("rippling","acme"),
            "https://abc.breezy.hr/p/100-abc":("breezy","abc"),
            "https://abc.jobs.personio.com/job/100":("personio","abc"),
            "https://abc.recruitee.com/o/role":("recruitee","abc")
        }
        for url,expected in samples.items():self.assertEqual(d.parse_ats_url(url),expected)
        for url in ("http://jobs.lever.co/company/1","https://evil.com/abc",
                    "https://jobs.ashbyhq.com/../../etc",
                    "https://get-your-job-now.com/"):
            self.assertIsNone(d.parse_ats_url(url))
    def test_recruiter_ats_board_not_employer(self):
        self.assertIsNone(d.parse_ats_url("https://jobs.lever.co/jobgether/123"))
        self.assertIsNone(d.parse_ats_url("https://jobs.ashbyhq.com/swooped/123"))
        self.assertFalse(report.title_ok("Technical Account Manager 2", "all"))
    def test_paid_calls_never_exceed_grant(self):
        calls=[]
        def fake_get(url,headers=None,timeout=None):
            calls.append(url)
            return {"web":{"results":[{"url":"https://abc.breezy.hr/p/123-hello"}]}}
        with patch.object(d.watch,"get",side_effect=fake_get):
            out,audit=d.discover(set(),time.time(),3,"TEST_FAKE")
        self.assertEqual(len(calls),3)
        self.assertEqual(len(audit),3)
        self.assertEqual(out,[("breezy",["abc","abc"])])
        self.assertEqual(d.discover(set(),time.time(),0,"TEST_FAKE"),([],[]))
    def test_location_remote_lists(self):
        yes=["Remote - United States","United States (Remote)","Remote: AL, TN, CO",
             "Remote US excluding CA","Remote US or Canada","Nashville, TN (Remote)"]
        for s in yes:
            self.assertEqual(d.location_triage(s)[0],"PASS",s)
        no=["Remote - California","Remote - TX, CA","London, UK",
            "Memphis, TN (Onsite)","Remote US excluding Tennessee"]
        for s in no:
            self.assertEqual(d.location_triage(s)[0],"REJECT",s)
        maybe=["Franklin, TN","Nashville, TN (Hybrid)","Tennessee","Eastern Time Zone Remote"]
        for s in maybe:self.assertEqual(d.location_triage(s)[0],"VERIFY",s)
    def test_workday_paginates_past_40(self):
        calls=[]
        def fake_get(url,data=None):
            calls.append(data["offset"])
            offset=data["offset"]
            n=20 if offset<60 else 3
            return {"jobPostings":[{"title":"Account Manager","externalPath":"/job/id_"+str(offset+i),
                    "locationsText":"Remote - US"} for i in range(n)]}
        with patch.object(d.watch,"get",side_effect=fake_get):
            found=d.fetch_workday_deep("Example","ex","wd1","Careers",page_cap=6,searches=["account manager"])
        self.assertEqual(len(found),63)
        self.assertEqual(calls,[0,20,40,60])
    def test_personio_parser(self):
        xml='<workzag-jobs><position><id>42</id><name>Customer Success Manager - Remote US</name><office>USA</office><jobDescriptions><jobDescription><name>Role</name><value><![CDATA[<p>Work with accounts.</p>]]></value></jobDescription></jobDescriptions></position></workzag-jobs>'
        with patch.object(d.watch,"http",return_value=xml):
            jobs=d.personio("Example","example")
        self.assertEqual(len(jobs),1)
        self.assertEqual(jobs[0]["url"],"https://example.jobs.personio.com/job/42?language=en")
        self.assertIn("Work with accounts.",jobs[0]["desc"])
    def test_breezy_parser(self):
        data=[{"id":"123","name":"Account Manager","slug":"account-manager",
               "location":{"city":"Nashville","region":"TN","country":"US"},
               "remote":True}]
        with patch.object(d.watch,"get",return_value=data):
            jobs=d.breezy("Example","example")
        self.assertEqual(len(jobs),1)
        self.assertTrue(jobs[0]["url"].startswith("https://example.breezy.hr/p/123-"))

class Report(unittest.TestCase):
    def test_report_markdown(self):
        meta={"local_date":"November 06, 2026","boards":300,"boards_new":2,"errors":0,
              "brave_audit":[],"brave_reserved":0,"first_run":True}
        s=report.render([],meta)
        self.assertIn("300",s)
        self.assertIn("No Brave API calls",s)
        self.assertIn("Initial baseline",s)
    def test_relationship_roles_rank_above_sdr(self):
        self.assertGreater(report.role_priority("Customer Success Manager"),
                           report.role_priority("Sales Development Representative"))
        self.assertGreater(report.role_priority("Account Manager"),
                           report.role_priority("Business Development Representative"))

    def test_title_excludes_training(self):
        self.assertTrue(report.title_ok("Client Engagement Manager","all"))
        self.assertFalse(report.title_ok("Director, Client Engagement","all"))

if __name__=="__main__":unittest.main()
