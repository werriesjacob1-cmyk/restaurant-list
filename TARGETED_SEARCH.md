# Targeted deep job search (optional; does not change board-watch)

Your existing board-watch stays unchanged and keeps sending real-time phone notifications.
This separate read-only workflow audits full descriptions on tracked employer ATS boards.
It never applies to jobs, sends alerts, or changes state.json / auto_roster.json.

## Run it

After this change is merged:

1. Open GitHub -> Actions -> targeted-deep-search -> Run workflow.
2. Set days = 7, focus = all, leave company blank, and optionally enable discover.
3. Read TARGET lines in the run log. Download the targeted-search-results artifact for every accepted, flagged, and rejected result.
4. Ask ChatGPT to compare results with the exported JW-### board and applications. The repo's hashed company exclusions are not a complete copy of that tracker.

Local:

    python -m unittest discover -s tests -v
    python targeted_scan.py --days 7 --focus all --output targeted_results.json
    python targeted_scan.py --days 30 --focus customer --company Tekion

## Deep discovery

The normal search polls the 380+ already-configured employer boards, with full descriptions where the ATS provides them. This is not a web-wide search. The optional --discover mode calls Brave Search to find new Greenhouse, Ashby, Lever, and Rippling board slugs, then confirms job existence directly through those employer ATS feeds.

To enable web discovery, configure the repository secret BRAVE_SEARCH_API_KEY (never paste it into the repo or chat). This is an optional paid external service. Without the key, --discover still audits the existing boards. A search-engine hit by itself is not an open job.

## Verification standards

- Source must be an employer-hosted ATS feed; otherwise it does not enter the report.
- Reject clear hard walls: 6+ years, required degree without equivalent wording, direct reports, required weekend shifts, over 40% travel, exclusively outbound, commission-only, and talent-pool-only listings.
- Separate explicitly labelled guaranteed base ranges from explicitly labelled OTE/total. Any ambiguous or missing compensation is marked VERIFY, never guessed.
- Candidate tiers are screening suggestions, not claims that the candidate meets every requirement. Confirm SaaS-specific, industry-specific, state-eligibility, travel and schedule requirements before applying.
- Older listings appear after fresh results rather than vanishing.
- This scan is intentionally on demand rather than every five minutes because full-post reads and search-index discovery cost API capacity.
