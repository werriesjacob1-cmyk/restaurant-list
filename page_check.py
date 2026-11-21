#!/usr/bin/env python3
"""Read-only: open each employer job/careers page in headless Chromium and report whether the exact title is on the page, closed text, or a dead link.
Rows with "search": true have no URL, so a web search is run and the top result links are printed for a human to open."""
import json, re, sys
from playwright.sync_api import sync_playwright

def norm(s): return re.sub(r"[^a-z0-9]", "", str(s).lower())
CLOSED = re.compile(r"no longer (?:accepting|available|open)|position (?:has been|is) (?:filled|closed)|job (?:is )?(?:closed|expired|no longer)|this (?:job|position|role) (?:is )?(?:no longer|closed|expired)|page (?:you are looking for )?(?:not found|doesn.t exist)|we couldn.t find|404|not currently (?:hiring|accepting)", re.I)

def check_url(page, row, url):
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=40000)
        try: page.wait_for_load_state("networkidle", timeout=8000)
        except Exception: pass
        code = resp.status if resp else 0
        text = page.inner_text("body")[:60000]
    except Exception as e:
        return "VT3|%s|ERR|%s|%s|%s" % (row["company"], row["title"], url, str(e)[:100].replace("\n", " "))
    t = norm(text); key = norm(row["title"])[:18]
    found = bool(key) and key in t
    closed = CLOSED.search(text)
    apply_btn = bool(re.search(r"\bapply\b", text, re.I))
    verdict = "TITLE_ON_PAGE" if found else "TITLE_NOT_ON_PAGE"
    if closed: verdict += "+CLOSED_TEXT:" + closed.group(0)[:40]
    if code >= 400: verdict += "+HTTP%d" % code
    if found and apply_btn and not closed and code < 400: verdict = "LIKELY_OPEN"
    return "VT3|%s|%s|%s|%s|final=%s|chars=%d" % (row["company"], verdict, row["title"], url, page.url[:120], len(text))

def search(page, row):
    q = "%s %s careers apply" % (row["company"], row["title"])
    try:
        page.goto("https://html.duckduckgo.com/html/?q=" + re.sub(r"\s+", "+", q), timeout=30000)
        links = page.eval_on_selector_all("a.result__a", "els => els.slice(0,5).map(e => e.href)")
    except Exception as e:
        return ["VT3|%s|SEARCH_ERR|%s|%s" % (row["company"], row["title"], str(e)[:100])]
    return ["VT3|%s|SEARCH_HIT|%s|%s" % (row["company"], row["title"], l) for l in links] or ["VT3|%s|SEARCH_NONE|%s" % (row["company"], row["title"])]

def main():
    rows = json.load(open(sys.argv[1]))
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36")
        page = ctx.new_page()
        for row in rows:
            if row.get("search"):
                lines = search(page, row)
                for line in lines: print(line, flush=True)
                if row.get("follow"):
                    skip = re.compile(r"linkedin|indeed|glassdoor|ziprecruiter|simplyhired|salary|reddit|facebook|builtin|wellfound|talent\.com|jooble|learn4good|adzuna", re.I)
                    seen = 0
                    for line in lines:
                        u = line.split("|")[-1]
                        if u.startswith("http") and not skip.search(u) and seen < 3:
                            seen += 1; print(check_url(page, row, u), flush=True)
            else:
                for u in row["urls"]: print(check_url(page, row, u), flush=True)
        b.close()
if __name__ == "__main__": main()
