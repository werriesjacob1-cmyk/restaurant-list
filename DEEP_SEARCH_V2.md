# Deep Search V2: employer discovery + ranked daily report

This is an independent workflow. Existing board-watch polling, alerts, state and
configuration are not changed.

## Features
- Daily run at 14:15 UTC (8:15 AM CST or 9:15 AM CDT); manual GitHub Actions
  dispatch available with focus and 7-day recency preference.
- Scans current ~380 employer ATS boards and paginates Workday more deeply.
- Rotates 30 targeted Brave search queries over ATS domains rather than repeating
  four identical searches daily.
- Reads employer-hosted Greenhouse, Ashby, Lever, Rippling, Workday, Workable,
  SmartRecruiters, BambooHR plus best-effort free Breezy HR public JSON, Personio
  public XML and Recruitee public XML. Teamtailor's official API requires the
  employer's own key, so we do not use it.
- Flags ambiguous remote-state eligibility and Nashville-area commuting rather
  than automatically treating all Tennessee listings as local.
- Produces deep-search-v2-report artifact with ranked Markdown, CSV and JSON,
  including rejected jobs and explanations. The top 30 appear in the Actions
  run summary.
- Tracks previously observed jobs via hashed IDs and candidate employer feeds
  via public-only roster. Initial run is a baseline, not proof that jobs are new.

## Strict Brave budget

Brave Search API costs $5 per 1,000 Search requests and comes with $5 monthly
credit. We reserve at most 3 searches per run and 100 per UTC month; this is
roughly $0.50 in published-price equivalent. A reservation is persisted in a
Git commit on main BEFORE the key is given to the search process. Failed
reservation or Git push means NO Brave searches; unused reservations count.

The budget only accounts for THIS workflow, not any other code or services
using the same Brave account. Keep auto-reload OFF and prepaid spending $0 in
the Brave dashboard. Monitor the provider's actual usage there.

## Running

1. Ensure repository Actions secret BRAVE_SEARCH_API_KEY exists.
2. Open GitHub Actions > deep-search-v2 > Run workflow (branch main).
3. Leave discover=true, focus=all and days=7, or customize those choices.
4. Read the job summary or download the deep-search-v2-report artifact.
5. Before applying, cross-reference the user's JW-### application tracker and
   skipped-job list, which are NOT stored in the public GitHub repo.

The search does not click Apply, submit applications, email recruiters or send
phone notifications. The current board-watch workflow continues independently.

## Limitations

Some ATS feeds can be blocked, stale or change shape; errors are reported.
Workday scanning is capped at 120 listings per query, but more than the prior
40. Onsite and hybrid commuting is always checked manually. Machine-scored
salary, SaaS requirements and hiring eligibility are suggestions, not proof.
Brave search hits are never considered open until the employer's own ATS feed
contains the job; even then the Apply button must be separately verified.

## Offline tests

    python -m unittest discover -s tests -p 'test_deep_search_v2.py' -v
    python -m unittest discover -s tests -p 'test_targeted_scan.py' -v
