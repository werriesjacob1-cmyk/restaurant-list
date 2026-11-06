import unittest
import time
from unittest.mock import patch

import verify_flagged as v

class FrozenVerificationTests(unittest.TestCase):
    def setUp(self):
        self.row={"id":"a1","company":"Acme","title":"Customer Success Manager",
                  "location":"Remote - United States",
                  "official_posting":"https://job-boards.greenhouse.io/acme/jobs/123",
                  "ats":"greenhouse","disposition":"VERIFY",
                  "tier_candidate":1,"priority_score":84}
        self.job={"id":"123","company":"Acme","title":"Customer Success Manager",
                  "location":"Remote - United States",
                  "url":"https://job-boards.greenhouse.io/acme/jobs/123",
                  "desc":""}

    def test_exact_url_match(self):
        match,status,_=v.match_job(self.row,[self.job])
        self.assertIs(match,self.job)
        self.assertEqual(status,"ATS_LISTED")

    def test_gh_custom_careers_id(self):
        a="https://example.com/careers?gh_jid=98765&utm_source=test"
        b="https://example.com/careers?gh_jid=98765"
        self.assertEqual(v.canonical_url(a),v.canonical_url(b))
        self.assertEqual(v.ats_id(a),"gh_jid:98765")

    def test_changed_link_is_not_confirmed_live(self):
        newer=dict(self.job,url="https://job-boards.greenhouse.io/acme/jobs/456")
        match,status,_=v.match_job(self.row,[newer])
        self.assertIs(match,newer)
        self.assertEqual(status,"IDENTIFIER_CHANGED")

    def test_removed_listing(self):
        match,status,_=v.match_job(self.row,[])
        self.assertIsNone(match)
        self.assertEqual(status,"NOT_LISTED")
        result=v.verify_one(self.row,(match,status,"Not listed"),None)
        self.assertEqual(result["status"],"NO_LONGER_LISTED")
        self.assertEqual(result["apply_form"],"NOT_TESTED")

    def test_limited_inference_for_unknown_pay(self):
        description=("Build customer relationships and manage renewals with an "
                     "established portfolio of small business customers. "
                     "Use a CRM, coordinate adoption reviews and customer education. ")*5
        status,questions,_,hard,_=v.verify_rules(self.row,description,"ATS_LISTED")
        self.assertEqual(status,"NEEDS_MANUAL_CHECK")
        self.assertFalse(hard)
        self.assertTrue(any("base" in q for q in questions))

    def test_explicit_full_requirements(self):
        description=("Own customer renewals and client outcomes for existing accounts. "
                     "Base salary: $80,000-$95,000. No weekends required, "
                     "remote work across the United States. "
                     "On-target earnings: $115,000-$135,000. "
                     "Candidates should have 2 years experience with clients. ")*4
        with patch.object(v.discovery,"location_triage",return_value=("PASS","nationwide remote")):
            status,_,_,hard,_=v.verify_rules(self.row,description,"ATS_LISTED")
        self.assertEqual(status,"PRIORITY_REVIEW")
        self.assertEqual(hard,[])

    def test_degree_and_required_weekend_fail(self):
        description=("Bachelor's degree required. Tuesday-Saturday shifts required. "
                     "Own customer success relationships and renewals. "
                     "Base salary: $88,000-$100,000. ")*5
        status,_,_,hard,_=v.verify_rules(self.row,description,"ATS_LISTED")
        self.assertEqual(status,"EXCLUDE")
        self.assertTrue(any("degree" in h for h in hard))
        self.assertTrue(any("weekend" in h for h in hard))

    def test_source_errors_never_masquerade_as_closed(self):
        result=v.verify_one(self.row,None,"HTTP 429 from employer ATS")
        self.assertEqual(result["status"],"UNVERIFIED")
        self.assertIn("HTTP 429",result["questions"][0])

    def test_province_and_nonremote_out_of_state_rejected(self):
        import discovery_v2 as d
        for loc in ("Oshawa, Ontario (Remote)", "Calgary, Alberta (Remote)",
                    "Boston, Massachusetts, USA", "Vancouver, Washington, United States",
                    "Chicago, Illinois, United States"):
            self.assertEqual(d.location_triage(loc)[0], "REJECT", loc)

    def test_memphis_territory_remote_rejected(self):
        import discovery_v2 as d
        status,_=d.location_triage("Memphis, TN (Remote)",
                        "Must reside within or near the assigned geographic territory")
        self.assertEqual(status,"REJECT")

    def test_insurance_partner_role_excluded(self):
        job=dict(self.row,title="Client Success Specialist, Insurance Partnerships")
        desc=("Own accounts in the insurance partnership channel and support onboarding. "
              "Base salary: $70,000-$80,000. Account renewals and customer satisfaction. ")*4
        status,_,_,hard,_=v.verify_rules(job,desc,"ATS_LISTED")
        self.assertEqual(status,"EXCLUDE")
        self.assertIn("Excluded industry explicitly named in job title",hard)

    def test_weekend_shift_required_excluded(self):
        desc=("Ability to work weeknights and weekends due to varying shifts, "
              "which includes 5 days of work with 2 days off is required. "
              "Customer success support, Base salary: $80,000-$95,000. ")*4
        status,_,_,hard,_=v.verify_rules(self.row,desc,"ATS_LISTED")
        self.assertEqual(status,"EXCLUDE")
        self.assertIn("Explicit weekend-work requirement",hard)

    def test_foreign_remote_cities_are_not_us_remote(self):
        import discovery_v2 as d
        for loc in ("Toronto (Remote)", "Sydney (Remote)", "Tokyo, Japan (Remote)",
                    "UK: Milton Keynes (Remote)", "Ottawa (Remote)"):
            self.assertEqual(d.location_triage(loc)[0], "REJECT", loc)
        self.assertEqual(d.location_triage("U.S Remote")[0], "PASS")

    def test_future_opportunity_and_people_lead_excluded(self):
        desc=("Own customer relationships for a recurring-revenue client book. "
              "Base salary: $80,000-$100,000; OTE: $115,000-$130,000. ")*4
        for title in ("Future Opportunity: Account Executive",
                      "Customer Success People Lead"):
            job=dict(self.row,title=title)
            status,_,_,hard,_=v.verify_rules(job,desc,"ATS_LISTED")
            self.assertEqual(status,"EXCLUDE",title)
            self.assertTrue(hard,title)

    def test_foreign_territory_title_when_location_ambiguous(self):
        job=dict(self.row,title="Territory Account Executive, Saskatchewan / Manitoba",
                 location="Distributed")
        desc=("Build customer relationships with prospects. "
              "Base salary: $90,000-$120,000. OTE: $160,000-$200,000. ")*4
        status,_,_,hard,_=v.verify_rules(job,desc,"ATS_LISTED")
        self.assertEqual(status,"EXCLUDE")
        self.assertIn("Position title is specific to a foreign region",hard)

    def test_no_false_apply_confirmation(self):
        result=v.verify_one(self.row,(self.job,"ATS_LISTED","exact ATS record"),None)
        self.assertEqual(result["apply_form"],"NOT_TESTED")
        self.assertIn(result["status"],("INSUFFICIENT_DATA","NEEDS_MANUAL_CHECK"))

    def test_count_every_entry_in_report(self):
        rec=v.verify_one(self.row,None,"No ATS feed")
        md=v.report_md([rec,dict(rec)],"42253368593")
        self.assertIn("input flagged entries:** 2",md)
        self.assertIn("output entries:** 2",md)

if __name__=="__main__":unittest.main()
