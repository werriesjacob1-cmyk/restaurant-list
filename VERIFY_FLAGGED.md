# Verify 357 V2 flagged jobs — no Brave credits

This workflow rechecks all 357 VERIFY records from the exact
Deep Search V2 GitHub Actions run 42253368593 (Nov 6, 2026).

## Run
After PR merge the verification runs once automatically. To rerun:
Actions -> verify-flagged-jobs -> Run workflow.
Use source_run_id=42253368593 and expected_count=357.
For later scans specify the newer report's run ID and actual flagged count.

## Verifications
- Downloads a frozen JSON report artifact from GitHub; does not run a new scan.
- Identifies each employer's configured ATS feed and downloads only those
  public job listings. Matching exact URL or ATS ID is strong evidence that
  the listing still exists. Unique title-only matches are marked ambiguous.
- Reads full descriptions through employer ATS APIs, with official job-page
  HTML / JSON-LD fallback when accessible.
- Inspects salary base vs OTE, required years, degree-equivalent wording,
  weekend work, travel, prohibited industries and Tennessee eligibility.
- Returns EXACTLY one row for each input flagged row with the disposition:
  PRIORITY_REVIEW / NEEDS_MANUAL_CHECK / INSUFFICIENT_DATA / UNVERIFIED /
  EXCLUDE / NO_LONGER_LISTED.
- Uploads verification.md, verification.csv and verification.json containing
  all results including why any record could not be confirmed.

## Strict boundaries
- No Brave calls, Adzuna calls, or outside LLMs. No Brave credits consumed.
- Does not click Apply buttons or submit applications; every record specifies
  apply_form=NOT_TESTED.
- Does not read or publish the user's private personal application tracker.
  Review JW-### applications and skipped positions before applying.
- A listed employer ATS record is NOT definitive proof of a working Apply
  form or that the employer accepts a Tennessee applicant.
- Rule-based judgments have limitations. PRIORITY_REVIEW means a shortlisting
  candidate for human checks, not a confirmed application-ready position.
