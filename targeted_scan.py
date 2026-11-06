#!/usr/bin/env python3
"""On-demand, read-only, employer-ATS job search.

Uses the existing watch.py adapters and hashed company exclusions.
Does not send notifications or edit the live watcher's state/roster.
Optional BRAVE_SEARCH_API_KEY discovers *additional* ATS boards; search hits
are never treated as verified job openings without an ATS feed check.
"""
import argparse
import collections
import json
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import watch

ROLE_GROUPS = {
    "customer": ("customer success", "client success", "account manager",
                 "renewal", "partner success", "customer engagement",
                 "account representative", "client services", "account coordinator"),
    "sales": ("account executive", "inside sales", "business development",
              "sales development", "account development", "sales representative",
              "sdr", "bdr", "commercial sales"),
}
EXCLUDE_TITLES = ("director", "vp ", "vice president", "head of", "supervisor",
                  "team lead", "sales coach", "trainer", "enablement", "implementation",
                  "onboarding", "intern", "technical support", "staff engineer")
REQUIRED_DEGREE = re.compile(
    r"(?:bachelor(?:'s)?|college degree|four.year degree)[^.]{0,55}(?:required|mandatory|must have)"
    r"|(?:required|must have)[^.]{0,55}(?:bachelor(?:'s)?|college degree|four.year degree)", re.I)
EQUIVALENT = re.compile(r"(?:or equivalent|equivalent (?:work |professional )?experience|equivalent combination)", re.I)
PEOPLE = re.compile(r"(?:direct reports|supervis(?:e|ing) (?:a |the )?team|manage a team of"
                    r"|lead a team of|people management required|hire and manage)", re.I)
WEEKEND_REQUIRED = re.compile(r"(?:tuesday\s*[-–]\s*saturday|must work (?:on )?weekends?"
                              r"|weekend shifts? required|saturday coverage required|regular weekend work)", re.I)
OUTBOUND_ONLY = re.compile(r"(?:100% outbound|exclusively outbound|pure(?:ly)? cold.calling"
                           r"|cold.calling only|entirely outbound|all pipeline self.generated)", re.I)
COMMISSION_ONLY = re.compile(r"(?:commission.only|100% commission|straight commission|1099 contractor)", re.I)
TALENT_POOL = re.compile(r"(?:not an active job opening|talent pool for future|future opportunities only"
                         r"|expression of interest only)", re.I)
TRAVEL = re.compile(r"(?:travel[^.;]{0,45}?(\d{1,2})\s*%|(\d{1,2})\s*%[^.;]{0,45}?travel)", re.I)
MONEY_SINGLE = re.compile(r"\$\s*(\d{2,3}(?:,\d{3})+|\d{2,3})\s*([kK])?")
ATS_SITES = (
    ("greenhouse", re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_.-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)")),
    ("rippling", re.compile(r"ats\.rippling\.com/([a-zA-Z0-9_-]+)")),
)

def title_ok(title, focus):
    s = " " + title.lower().strip() + " "
    if any(x in s for x in EXCLUDE_TITLES):
        return False
    patterns = ROLE_GROUPS["customer"] + ROLE_GROUPS["sales"] if focus == "all" else ROLE_GROUPS[focus]
    return any(x in s for x in patterns)

def pay_evidence(text, structured=""):
    """Return explicitly labelled annual base/OTE ranges; ambiguous ranges stay unverified."""
    ranges = {"base": [], "ote": [], "ambiguous": []}
    for source in (structured or "", text or ""):
        for match in watch.PAY_RE.finditer(source):
            lo = watch.money(match.group(1), match.group(2))
            hi = watch.money(match.group(3), match.group(4) or match.group(2))
            if not (30000 <= lo <= hi <= 400000):
                continue
            before = source[max(0, match.start() - 105):match.start()].lower()
            after = source[match.end():match.end() + 35].lower()
            context = before[-70:]
            if re.search(r"\bote\b|on.target earnings|total (?:target )?(?:compensation|earnings)", context):
                category = "ote"
            elif re.search(r"\bbase\b|annual salary|salary range|annualized salary|salary band", context):
                category = "base"
            else:
                category = "ambiguous"
            ranges[category].append([int(lo), int(hi)])
        for kind, rx in (
            ("base", r"(?:base salary|base pay|annual base|base compensation)\s*(?:is|of|:|range of)?\s*\$\s*(\d{2,3}(?:,\d{3})+|\d{2,3})\s*([kK])?"),
            ("ote", r"(?:\bOTE\b|on.target earnings)\s*(?:is|of|:|up to)?\s*\$\s*(\d{2,3}(?:,\d{3})+|\d{2,3})\s*([kK])?")
        ):
            for match in re.finditer(rx, source, re.I):
                v = int(watch.money(match.group(1), match.group(2)))
                if 30000 <= v <= 400000:
                    ranges[kind].append([v, v])
    return {k: sorted(set(tuple(v) for v in vals)) for k, vals in ranges.items()}

def describe_job(job, description, source, days, now):
    title = job.get("title", "")
    years = watch.years_required(description)
    known_pay = pay_evidence(description, str(job.get("pay") or ""))
    base = known_pay["base"]
    ote = known_pay["ote"]
    age = round((now - job["posted"]) / 86400, 1) if job.get("posted") else None
    issues, hard = [], []
    if not description or len(description) < 200:
        issues.append("full description unreadable")
    if years > 5:
        hard.append("requires 6+ years")
    if REQUIRED_DEGREE.search(description) and not EQUIVALENT.search(description):
        hard.append("required degree without equivalent")
    if PEOPLE.search(description):
        hard.append("people-management duties")
    if WEEKEND_REQUIRED.search(description):
        hard.append("required weekend work")
    if OUTBOUND_ONLY.search(description):
        hard.append("exclusively outbound")
    if COMMISSION_ONLY.search(description):
        hard.append("commission-only/contract")
    if TALENT_POOL.search(description):
        hard.append("talent-pool posting, not active job")
    travel = [int(a or b) for a,b in TRAVEL.findall(description)]
    if travel and max(travel) > 40:
        hard.append("travel over 40%")
    elif travel and max(travel) >= 25:
        issues.append("travel %s%%: confirm" % max(travel))
    if re.search(r"\b(?:saturdays?|sundays?|weekends?|on.call)\b", description, re.I) and not WEEKEND_REQUIRED.search(description):
        issues.append("weekend/on-call wording: ask")
    if re.search(r"\b(?:cold.call|outbound|prospecting)\b", description, re.I):
        issues.append("outbound mix: check")
    if not base:
        issues.append("guaranteed base not explicitly verified")
    else:
        base_min = min(v[0] for v in base)
        base_max = max(v[1] for v in base)
        if base_max < 55000:
            hard.append("base below $55k")
        elif base_min < 55000:
            issues.append("base range extends under $55k")
        if base_min < 75000:
            if ote and max(v[1] for v in ote) < 75000:
                hard.append("total compensation below $75k")
            elif not ote:
                issues.append("OTE/total >=$75k not explicitly verified")
    if not ote and not base:
        issues.append("salary/OTE missing or ambiguous")
    if years == 0:
        issues.append("years requirement not clearly specified")
    if re.search(r"\bsaas\b", description, re.I):
        # Treat software experience as a fit question, never as experience he has.
        if re.search(r"(?:required|must have|years? of)[^.;]{0,80}\bsaas\b|\bsaas\b[^.;]{0,80}(?:required|experience)", description, re.I):
            issues.append("required SaaS experience: gap")
    if re.search(r"\b(?:insurance sales|loan officer|mortgage sales|solar sales|staffing agency)\b", title + " " + description[:900], re.I):
        hard.append("excluded industry")
    if age is None:
        issues.append("posting age unknown")
    elif age > days:
        issues.append("older than %s days" % days)
    tier = 1
    if years >= 4: tier = 2
    if any("required SaaS" in x for x in issues): tier = max(tier, 2 if years <= 2 else 3)
    if re.search(r"\b(?:senior|enterprise|strategic|mid.market)\b", title, re.I):
        tier = max(tier, 3)
    if hard:
        disposition = "REJECT"
    elif any(x in issues for x in ("full description unreadable", "guaranteed base not explicitly verified",
                                   "OTE/total >=$75k not explicitly verified", "salary/OTE missing or ambiguous")):
        disposition = "VERIFY"
    else:
        disposition = "REVIEW"
    return {
        "company": job["company"], "title": title, "location": job.get("location",""),
        "official_posting": job.get("url",""), "ats": source, "age_days": age,
        "tier_candidate": tier, "disposition": disposition, "years_requested": years,
        "base_ranges": base, "ote_ranges": ote, "ambiguous_pay_ranges": known_pay["ambiguous"],
        "hard_exclusions": hard, "questions": sorted(set(issues)),
        "date_checked_utc": datetime.fromtimestamp(now,timezone.utc).isoformat()
    }

def extra_boards_from_brave(known, max_queries=4):
    key = os.getenv("BRAVE_SEARCH_API_KEY","")
    if not key:
        print("DISCOVERY: no BRAVE_SEARCH_API_KEY; searching existing ATS roster only")
        return []
    searches = [
        'site:jobs.ashbyhq.com "customer success" "remote"',
        'site:job-boards.greenhouse.io "account manager" "remote"',
        'site:jobs.lever.co "renewals" "remote"',
        'site:ats.rippling.com "account executive" "remote"',
    ][:max_queries]
    extra = []
    for q in searches:
        try:
            params = urllib.parse.urlencode({"q": q, "count": 20, "freshness": "pw", "country": "US"})
            data = watch.get("https://api.search.brave.com/res/v1/web/search?" + params,
                             headers={"X-Subscription-Token":key, "Accept":"application/json"})
        except Exception as ex:
            print("DISCOVERY-ERROR:", str(ex)[:120]); continue
        for hit in (data.get("web") or {}).get("results", []):
            url = hit.get("url","")
            for kind, pattern in ATS_SITES:
                match = pattern.search(url)
                if match:
                    slug = match.group(1)
                    unique = (kind, slug.lower())
                    if unique not in known:
                        known.add(unique)
                        extra.append((kind, [slug, slug]))
                    break
    print("DISCOVERY: %s candidate boards from employer-ATS search results (unverified until fetched)" % len(extra))
    return extra[:35]

def search(args):
    now = time.time()
    auto = watch.load_json(watch.AUTO_PATH, {})
    boards = watch.board_list(auto)
    known = {(k, str(e[1]).lower()) for k,e in boards if len(e)>1}
    if args.discover:
        boards += extra_boards_from_brave(known)
    blocked = set(watch.CFG.get("skip_hashes",[]))
    results, failures = [], []
    def get_board(item):
        kind,e = item
        if args.company and args.company.lower() not in e[0].lower():
            return kind,e,[],None
        try:
            return kind,e,watch.ADAPTERS[kind][0](e[0],*e[1:]),None
        except Exception as ex:
            return kind,e,[],str(ex)[:160]
    with ThreadPoolExecutor(max_workers=8) as pool:
        board_results = list(pool.map(get_board,boards))
    seen = set()
    candidates = []
    for kind, e, jobs, err in board_results:
        if err:
            failures.append("%s (%s): %s"%(e[0],kind,err))
            continue
        for j in jobs:
            if not title_ok(j.get("title",""),args.focus):
                continue
            if not watch.loc_ok(j.get("location","")):
                continue
            if watch.chash(j["company"]) in blocked:
                continue
            key=(j["company"].lower(),j["title"].lower().strip(),j.get("location","").lower().strip())
            if key in seen:
                continue
            seen.add(key)
            candidates.append((kind,j))
    def evaluate(item):
        kind,j=item
        desc=j.get("desc") or ""
        if len(desc)<200 and j.get("detail"):
            try: desc=j["detail"]() or ""
            except Exception: pass
        # Never infer posting validity from aggregator snippets or a rendered page.
        return describe_job(j,desc,kind,args.days,now)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(evaluate,candidates))
    counts=collections.Counter(x["disposition"] for x in results)
    order={"REVIEW":0,"VERIFY":1,"REJECT":2}
    results.sort(key=lambda x:(order[x["disposition"]],x["tier_candidate"],
        x["age_days"] is None or x["age_days"]>args.days,x["age_days"] if x["age_days"] is not None else 9999,
        x["company"].lower()))
    # Export all, including rejects, so no candidate silently disappears.
    output={"checked_utc":datetime.fromtimestamp(now,timezone.utc).isoformat(),
            "boards_considered":len(boards),"candidates":len(results),"counts":dict(counts),
            "board_errors":failures,"note":"Automated tier suggestions require human review; employers' ATS is authoritative.",
            "results":results}
    Path(args.output).write_text(json.dumps(output,indent=2)+"\n",encoding="utf-8")
    print("TARGETED-SUMMARY boards=%s candidates=%s review=%s verify=%s reject=%s errors=%s"
          %(len(boards),len(results),counts["REVIEW"],counts["VERIFY"],counts["REJECT"],len(failures)))
    shown=0
    for r in results:
        if r["disposition"]=="REJECT": continue
        print("TARGET|%s|Tier%s|%s|%s|base=%s|ote=%s|age=%s|%s|%s"
              %(r["disposition"],r["tier_candidate"],r["company"],r["title"],r["base_ranges"],
                r["ote_ranges"],r["age_days"],"; ".join(r["questions"][:4]),r["official_posting"]))
        shown+=1
        if shown>=args.limit:break
    print("TARGETED-REPORT %s (full results include all exclusions)"%args.output)
    return 0

def main():
    p=argparse.ArgumentParser(description="Targeted, on-demand search across official employer ATS feeds")
    p.add_argument("--days",type=int,default=7,help="rank postings in this age window first; do not discard older postings")
    p.add_argument("--focus",choices=("all","customer","sales"),default="all")
    p.add_argument("--company",default="",help="optional targeted employer name/substring")
    p.add_argument("--discover",action="store_true",help="discover additional employer ATS boards using optional Brave Search API")
    p.add_argument("--limit",type=int,default=30)
    p.add_argument("--output",default="targeted_results.json")
    args=p.parse_args()
    if args.days<1 or args.limit<1: p.error("--days and --limit must be >= 1")
    return search(args)

if __name__=="__main__":
    raise SystemExit(main())
