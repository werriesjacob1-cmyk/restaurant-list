import unittest
import time

import targeted_scan as target


class TargetedSearchTests(unittest.TestCase):
    def test_job_family(self):
        self.assertTrue(target.title_ok("Account Manager, SMB", "customer"))
        self.assertTrue(target.title_ok("Sales Development Representative", "sales"))
        self.assertFalse(target.title_ok("Sales Development Representative", "customer"))
        self.assertFalse(target.title_ok("Sales Enablement Manager", "sales"))
        self.assertFalse(target.title_ok("Director, Customer Success", "customer"))

    def test_pay_labels_not_assumed(self):
        p = target.pay_evidence(
            "Annual base salary range: $70,000-$90,000; OTE: $110,000-$140,000")
        self.assertIn((70000, 90000), p["base"])
        self.assertIn((110000, 140000), p["ote"])
        p = target.pay_evidence("Compensation: $80,000-$110,000")
        self.assertFalse(p["base"])
        self.assertTrue(p["ambiguous"])

    def test_explicit_hard_requirements(self):
        j = {"company":"Example","title":"Customer Success Manager","location":"Remote USA",
             "url":"https://example.com/job","posted":time.time(),"pay":""}
        desc = ("Work with customers to drive renewals. Bachelor's degree required. "
                "Requires 6+ years of customer success. Tuesday-Saturday required. "
                "Annual base salary range: $90,000-$110,000. ") * 2
        r=target.describe_job(j,desc,"greenhouse",7,time.time())
        self.assertEqual("REJECT",r["disposition"])
        self.assertIn("requires 6+ years",r["hard_exclusions"])
        self.assertIn("required weekend work",r["hard_exclusions"])

    def test_equivalent_degree_is_allowed(self):
        j = {"company":"Example","title":"Account Manager","location":"Remote USA",
             "url":"https://example.com/job","posted":time.time(),"pay":""}
        desc = ("Bachelor's degree required OR equivalent professional experience. "
                "Base salary: $80,000-$90,000. OTE: $120,000-$140,000. "
                "Existing customers, renewals, and service.") * 2
        r=target.describe_job(j,desc,"lever",7,time.time())
        self.assertNotIn("required degree without equivalent",r["hard_exclusions"])

    def test_talent_pool_not_a_live_opening(self):
        j={"company":"Test","title":"Account Executive","location":"Remote US",
           "url":"https://example.com","posted":None,"pay":""}
        txt=("This is not an active job opening. Join our talent pool for future opportunities. "
             "Annual base salary: $80,000-$90,000.")*2
        self.assertEqual("REJECT",target.describe_job(j,txt,"ashby",7,time.time())["disposition"])


    def test_low_ote_is_rejected_even_without_base(self):
        j={"company":"Test","title":"Inside Sales Representative","location":"Remote US",
           "url":"https://example.com","posted":time.time(),"pay":""}
        txt=("On-target earnings: $60,000-$65,000. "
             "Some customers and renewals, no guaranteed base stated.")*3
        r=target.describe_job(j,txt,"lever",7,time.time())
        self.assertEqual("REJECT",r["disposition"])
        self.assertIn("OTE below $75k",r["hard_exclusions"])

    def test_required_b2b_account_portfolio_not_tier_one(self):
        j={"company":"Test","title":"Account Manager (Corporate)","location":"Remote US",
           "url":"https://example.com","posted":time.time(),"pay":""}
        txt=("3-5+ years of B2B commercial account management experience required. "
             "Annual base salary: $80,000-$100,000. Manage renewals for accounts.")*3
        r=target.describe_job(j,txt,"greenhouse",7,time.time())
        self.assertEqual(3,r["tier_candidate"])
        self.assertIn("required B2B commercial experience: gap",r["questions"])

if __name__=="__main__":
    unittest.main()

class SdrFocusTests(unittest.TestCase):
    def test_sdr_titles(self):
        self.assertTrue(target.title_ok("Sales Development Representative", "sdr"))
        self.assertFalse(target.title_ok("SDR Manager", "sdr"))
        self.assertFalse(target.title_ok("Senior Business Development Representative", "sdr"))

    def test_sdr_degree_and_outbound_not_rejected(self):
        job = {"company": "X", "title": "SDR", "location": "Nashville, TN", "url": "u", "posted": None}
        text = ("Bachelor's degree required. 100% outbound cold calling. Base $55,000 and OTE $85,000. " * 4)
        r = target.describe_job(job, text, "greenhouse", 7, 0, "sdr")
        self.assertNotIn("required degree without equivalent", r["hard_exclusions"])
        self.assertNotIn("exclusively outbound", r["hard_exclusions"])
        self.assertIn("NASHVILLE", r["questions"])
        r2 = target.describe_job(job, text, "greenhouse", 7, 0, "")
        self.assertIn("required degree without equivalent", r2["hard_exclusions"])

