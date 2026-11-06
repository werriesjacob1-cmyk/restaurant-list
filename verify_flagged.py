#!/usr/bin/env python3
"""Verify every flagged V2 job against the employer ATS again, without Brave.

The input is a frozen deep-search-v2-report JSON artifact. The output includes
one result for EVERY VERIFY entry; no silent drops and no invented Apply checks.
Only read-only, public employer ATS/official posting requests are made.
"""
import argparse
import csv
import html
import json
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import watch
import targeted_scan as target
import discovery_v2 as discovery
import deep_search_v2 as deep

STANDARD_OUTPUT = Path("verification_output")
PAID_APIS = ("Brave", "Adzuna", "OpenAI")
MAX_THREADS = 8
SOURCE_RUN = "42253368593"  # exact 357-row source, overrideable for future scans
IDENTICAL_JOB_FIELDS = ("company", "title", "location", "official_posting", "ats")

def clean(text, limit=220):
    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()[:limit]

def canonical_url(url):
    try:
        u = urlsplit(url)
        if u.scheme not in ("https", "http") or not u.hostname:
            return ""
        # gh_jid is critical on custom company careers URLs.
        params = parse_qs(u.query)
        jid = params.get("gh_jid") or params.get("gh_job_id")
        query = ("?gh_jid=" + jid[0]) if jid else ""
        return u.hostname.lower() + u.path.rstrip("/").lower() + query
    except (ValueError, TypeError):
        return ""

def ats_id(url):
    try:
        u=urlsplit(url)
        p=parse_qs(u.query)
        for key in ("gh_jid","gh_job_id","jobId"):
            v=p.get(key)
            if v: return key+":"+v[0].lower()
        parts=[x for x in u.path.split("/") if x]
        if not parts:return ""
        v=parts[-1].lower()
        # Short generic paths like /careers are not job identifiers.
        if v in ("jobs","job","careers","positions","openings"):
            return ""
        if re.fullmatch(r"[a-f0-9-]{28,40}",v) or re.fullmatch(r"\d{5,12}",v):
            return "post:"+v
        if "_r" in v and len(v)>12:return "workday:"+v
        return ""
    except (ValueError, TypeError):
        return ""

def same_title(a,b):
    return re.sub(r"[^a-z0-9]+","",str(a or "").lower()) == re.sub(r"[^a-z0-9]+","",str(b or "").lower())

def match_job(row, jobs):
    orig=canonical_url(row.get("official_posting",""))
    candidates=[j for j in jobs if canonical_url(j.get("url",""))==orig and orig]
    if len(candidates)==1:
        return candidates[0],"ATS_LISTED","Exact employer-feed posting URL matched"
    oldid=ats_id(row.get("official_posting",""))
    if oldid:
        matched=[j for j in jobs if ats_id(j.get("url",""))==oldid
                 and same_title(j.get("title"),row.get("title"))]
        if len(matched)==1:
            return matched[0],"ATS_LISTED","Employer-feed job ID and title matched"
    # Title-only evidence is not enough to certify an old posting is open.
    same=[j for j in jobs if same_title(j.get("title"),row.get("title"))]
    if len(same)==1:
        return same[0],"IDENTIFIER_CHANGED","Same role title listed, but old posting ID/URL not verified"
    return None,"NOT_LISTED","Original posting not present in fresh employer ATS feed"

def employer_boards():
    boards=watch.board_list(watch.load_json(watch.AUTO_PATH,{}))
    ext=deep.load_roster()
    boards += [(kind,e) for kind,entries in ext.items() for e in entries]
    by_company=defaultdict(list)
    for kind,e in boards:
        by_company[(watch.norm_company(e[0]),kind)].append((kind,e))
    return by_company

def select_board(row,index):
    k=(watch.norm_company(row["company"]),row.get("ats",""))
    choices=index.get(k,[])
    if len(choices)==1:return choices[0],None
    if len(choices)>1:
        # Try to match a specific Greenhouse/Ashby/Lever slug.
        url=row.get("official_posting","")
        slug=discovery.parse_ats_url(url)
        if slug:
            found=[x for x in choices if str(x[1][1]).lower()==slug[1]]
            if len(found)==1:return found[0],None
        return None,"Multiple employer ATS feeds; source cannot be identified uniquely"
    return None,"No matching configured employer feed; cannot establish current status"

def verified_html_fallback(url):
    """Only visit employer-provided official URLs, never search-result URLs."""
    try:
        u=urlsplit(url)
        if u.scheme!="https" or not u.hostname:return "", "unsafe posting URL"
        raw=watch.http(url,timeout=11)
    except Exception as exc:
        return "", "Official posting page unavailable: "+clean(str(exc),100)
    snippets=[]
    # Extract accessible JSON-LD JobPosting descriptions when present.
    for block in re.findall(r"<script[^>]+type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
                            raw,re.I|re.S)[:5]:
        try:
            doc=json.loads(html.unescape(block))
            nodes=doc if isinstance(doc,list) else doc.get("@graph",[doc]) if isinstance(doc,dict) else []
            for node in nodes:
                if isinstance(node,dict) and "JobPosting" in str(node.get("@type","")):
                    desc=node.get("description","")
                    if isinstance(desc,str):snippets.append(watch.strip_html(desc))
                    base=node.get("baseSalary",{})
                    if base:snippets.append("Employer JSON-LD baseSalary metadata: "+str(base)[:450])
        except (ValueError,TypeError):
            pass
    if snippets and len(" ".join(snippets))>=200:
        return " ".join(snippets),"official JSON-LD job description"
    # Raw HTML may be JS-only or an expired job. Avoid representing generic
    # company boilerplate as a complete job description.
    body=re.search(r"<(?:main|article)[^>]*>(.*?)</(?:main|article)>",raw,re.I|re.S)
    cleaned=watch.strip_html(body.group(1)) if body else ""
    if len(cleaned)>=300 and re.search(r"\b(qualifications|responsibilities|requirements|what you.ll do)\b",cleaned,re.I):
        return cleaned[:22000],"official page article/main text"
    return "","Official page did not expose a readable full job description"

def evidence(text):
    fragments={}
    patterns={
        "salary":r"\$[\d,]+\s*[kK]?(?:\s*[-–to]{1,4}\s*\$?[\d,]+\s*[kK]?)?",
        "experience":r"\b\d{1,2}\+?(?:[-–]\d{1,2})?\s*years?(?:\s+of)?[^.;\n]{0,90}",
        "degree":r"\b(?:bachelor.s?|degree required|college degree|equivalent experience)[^.;\n]{0,100}",
        "travel":r"\b(?:travel[^.;\n]{0,55}|\d{1,2}\s*%[^.;\n]{0,25}travel)",
        "schedule":r"\b(?:weekends?|saturdays?|sundays?|on.call)[^.;\n]{0,65}"
    }
    for key,pattern in patterns.items():
        m=re.search(pattern,text or "",re.I)
        if m:fragments[key]=clean((text or "")[max(0,m.start()-65):m.end()+55],210)
    return fragments

def verify_rules(row,text,posting_status):
    """Evidence-based, conservative requirements checks. Unknown stays unknown."""
    hard,questions=[],[]
    if posting_status=="NOT_LISTED":
        return "NO_LONGER_LISTED",["Original ATS job ID not found"],{},[],[]
    if posting_status=="UNVERIFIED":
        return "UNVERIFIED",["Employer ATS could not be checked"],{},[],[]
    if posting_status=="IDENTIFIER_CHANGED":
        questions.append("Old ATS posting URL/ID not confirmed; same title exists")
    if len(text)<200:
        return "INSUFFICIENT_DATA",questions+["Full job description inaccessible"],{},[],[]
    assessed=target.describe_job({
        "company":row["company"],"title":row["title"],"location":row.get("location",""),
        "url":row.get("official_posting",""),"posted":None,"pay":""
    },text,row.get("ats",""),7,time.time())
    hard.extend(assessed["hard_exclusions"])
    questions.extend(q for q in assessed["questions"] if q not in ("posting age unknown","years requirement not clearly specified"))
    loc,loc_reason=discovery.location_triage(row.get("location",""),text)
    if loc=="REJECT":
        hard.append(loc_reason)
    elif loc=="VERIFY":
        questions.append(loc_reason)
    if re.search(r"\b(?:life insurance|health insurance sales|insurance agent|insurance producer|"
                 r"mortgage loan|loan officer|loan origination|automotive lending|solar sales|staffing recruiter)\b",text,re.I):
        hard.append("Role explicitly falls in an excluded industry")
    if re.search(r"\b(?:insurance|mortgage|lending|loan officer|solar|staffing)\b",row.get("title",""),re.I):
        hard.append("Excluded industry explicitly named in job title")
    if re.search(r"\b(?:future opportunity|future opening|talent pool|talent community|expression of interest)\b",
                 row.get("title",""),re.I):
        hard.append("Future talent-pool posting, not an active specific opening")
    if re.search(r"\b(?:people lead|team lead|head of customer success)\b",row.get("title",""),re.I):
        hard.append("Title indicates people-management responsibilities")
    if (discovery.FOREIGN.search(row.get("title","")) and
        not discovery.US.search(row.get("location","")) and
        not discovery.TN.search(row.get("location",""))):
        hard.append("Position title is specific to a foreign region")
    if re.search(r"(?:ability|willingness|must(?: be able)?)\s+to\s+work\b[^.\n]{0,160}"
                 r"\b(?:weekends?|saturdays?|sundays?)\b[^.\n]{0,120}\b(?:required|mandatory|must)\b",
                 text,re.I):
        hard.append("Explicit weekend-work requirement")
    if re.search(r"\b(?:insurance|mortgage|lending|solar|staffing)\b",row["company"],re.I):
        questions.append("Employer may be in an excluded industry; check company")
    # Technical and location-specific roles can be completely incompatible
    # without stating that in the description.
    if re.search(r"\b(?:Boston|Denver|San Francisco|New York|Seattle|EMEA|APAC)\b",
                 row.get("title",""),re.I) and loc!="PASS":
        questions.append("Title has another city/region; confirm TN eligibility")
    base=assessed["base_ranges"];ote=assessed["ote_ranges"]
    if not base:
        questions.append("Explicit guaranteed base of at least $55k not confirmed")
    if base:
        min_base=min(v[0] for v in base)
        max_base=max(v[1] for v in base)
        if max_base<55000:
            hard.append("Posted base entirely below $55k")
        elif min_base<55000:
            questions.append("Posted base band begins below $55k")
        if min_base<75000 and not ote:
            questions.append("Total compensation of at least $75k not explicitly confirmed")
    if not ote and not base:
        questions.append("No explicit salary/OTE data")
    if posting_status=="IDENTIFIER_CHANGED":
        questions.append("Apply form for original posting not verified")
    hard=sorted(set(hard))
    questions=sorted(set(questions))
    # Always reserve "application ready" for a human/browser Apply form check.
    if hard:return "EXCLUDE",questions,assessed,hard,base
    if (base and min(x[0] for x in base)>=55000 and
        (min(x[0] for x in base)>=75000 or (ote and max(x[1] for x in ote)>=75000))
        and loc=="PASS" and posting_status=="ATS_LISTED"
        and not any("SaaS" in q or "B2B" in q for q in questions)):
        return "PRIORITY_REVIEW",questions,assessed,hard,base
    return "NEEDS_MANUAL_CHECK",questions,assessed,hard,base

def verify_one(row,found,err):
    identity={k:row.get(k) for k in IDENTICAL_JOB_FIELDS}
    identity.update({"id":row.get("id",""),"previous_tier":row.get("tier_candidate"),
                     "previous_score":row.get("priority_score")})
    if err:
        identity.update({"status":"UNVERIFIED","feed_evidence":err,"requirements":"UNVERIFIED",
                         "apply_form":"NOT_TESTED","questions":[err],
                         "hard_exclusions":[],"evidence":{},"new_base":[],"new_ote":[]})
        return identity
    job,posting_status,why=found
    description=""
    source=""
    if job:
        description=job.get("desc") or ""
        source="ATS feed text"
        if len(description)<200 and job.get("detail"):
            try:
                description=job["detail"]() or ""
                source="ATS job-detail endpoint"
            except Exception:
                description=""
        if len(description)<200 and job.get("url"):
            description,source=verified_html_fallback(job["url"])
    if posting_status=="NOT_LISTED":
        result="NO_LONGER_LISTED";questions=[why];assessed={};hard=[]
    else:
        result,questions,assessed,hard,_=verify_rules(row,description,posting_status)
    identity.update({"status":result,"feed_evidence":why,"requirements":result,
                     "listing_status":posting_status,
                     "apply_form":"NOT_TESTED",
                     "description_source":source or "no readable description",
                     "description_characters":len(description),
                     "questions":questions,"hard_exclusions":hard,
                     "evidence":evidence(description),
                     "new_base":assessed.get("base_ranges",[]),
                     "new_ote":assessed.get("ote_ranges",[]),
                     "years_requirement_heuristic":assessed.get("years_requested",None),
                     "current_posting_url":job.get("url") if job else row.get("official_posting","")})
    return identity

def report_md(rows,source_run):
    counts=Counter(r["status"] for r in rows)
    ranked=sorted(rows,key=lambda r:(
        {"PRIORITY_REVIEW":0,"NEEDS_MANUAL_CHECK":1,"INSUFFICIENT_DATA":2,
         "UNVERIFIED":3,"EXCLUDE":4,"NO_LONGER_LISTED":5}.get(r["status"],6),
        -int(r.get("previous_score") or 0)))
    result=["# Employer ATS verification audit",
            "",
            f"**Source run:** {source_run}; **input flagged entries:** {len(rows)}; **output entries:** {len(rows)}.",
            f"Priority review: {counts['PRIORITY_REVIEW']}; manual checks: {counts['NEEDS_MANUAL_CHECK']}; "
            f"insufficient JD: {counts['INSUFFICIENT_DATA']}; source unverified: {counts['UNVERIFIED']}; "
            f"hard exclusions: {counts['EXCLUDE']}; no longer listed: {counts['NO_LONGER_LISTED']}.",
            "",
            "**Important:** Listed in an employer ATS does NOT confirm a functioning Apply form. "
            "No jobs were applied to, no recruiter was contacted, and no Brave credits were used.",
            "This audit uses conservative rules; pay/experience wording and employer restrictions "
            "still require human review. Existing applications/skip decisions must be cross-checked separately.",
            "",
            "## Best jobs to check manually",
            "",
            "| Rank | Company | Role | Screening | Base | OTE | Remaining questions | Employer posting |",
            "|---|---|---|---|---|---|---|---|"]
    n=0
    per_company=Counter()
    for r in ranked:
        if r["status"] not in ("PRIORITY_REVIEW","NEEDS_MANUAL_CHECK"):continue
        company=watch.norm_company(r["company"])
        if per_company[company]>=2:continue
        per_company[company]+=1;n+=1
        base=deep.salary(r["new_base"]);ote=deep.salary(r["new_ote"])
        msg=clean("; ".join(r["questions"][:2]),140)
        result.append(f"| {n} | {clean(r['company'],50)} | {clean(r['title'],85)} "
                      f"| {r['status']} | {base} | {ote} | {msg or 'Check Apply form'} "
                      f"| [ATS]({r['current_posting_url']}) |")
        if n>=35:break
    result.extend(["","## How to use this audit","",
        "1. Review PRIORITY_REVIEW first, then NEEDS_MANUAL_CHECK.",
        "2. Any missing salary/base, degree, location or SaaS requirements remain unresolved.",
        "3. Verify an active Apply form on the employer's website before applying.",
        "4. Compare against all JW-### applications and skipped companies; private tracker is not in repo.",
        "5. Full JSON and CSV preserve every input entry, status, excerpt and URL.",
        ""])
    return "\n".join(result)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",default="verification_input/report.json")
    p.add_argument("--source-run",default=SOURCE_RUN)
    p.add_argument("--expected",type=int,default=357)
    p.add_argument("--output",default=str(STANDARD_OUTPUT))
    opts=p.parse_args()
    data=json.loads(Path(opts.input).read_text())
    allrows=data.get("results",[])
    selection=[r for r in allrows if r.get("disposition")=="VERIFY"]
    if opts.expected>0 and len(selection)!=opts.expected:
        p.error(f"Expected exactly {opts.expected} VERIFY records from run {opts.source_run}, got {len(selection)}")
    if not selection:p.error("No VERIFY rows to audit")
    index=employer_boards()
    refs={}
    for r in selection:
        source,error=select_board(r,index)
        refs[(r.get("id"),r.get("official_posting"))]=(source,error)
    source_keys={json.dumps(source) for source,error in refs.values() if source}
    unique_sources={json.dumps(source):source for source,error in refs.values() if source}
    def fetch_one(key):
        kind,spec=unique_sources[key]
        try:
            return key,(discovery.fetch_board(kind,spec),None)
        except Exception as ex:
            return key,([],f"{spec[0]} feed read failed: {clean(str(ex),125)}")
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as pool:
        loaded=dict(pool.map(fetch_one,unique_sources))
    tasks=[]
    for row in selection:
        source,err=refs[(row.get("id"),row.get("official_posting"))]
        if not source:
            tasks.append((row,None,err))
            continue
        jobs,error=loaded[json.dumps(source)]
        if error:
            tasks.append((row,None,error))
            continue
        match,status,why=match_job(row,jobs)
        tasks.append((row,(match,status,why),None))
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as pool:
        checked=list(pool.map(lambda args:verify_one(*args),tasks))
    if len(checked)!=len(selection):
        raise RuntimeError("Not all jobs were processed")
    OUT=Path(opts.output);OUT.mkdir(exist_ok=True)
    results={"source_run":opts.source_run,
             "input_verify_count":len(selection),
             "checked_utc":datetime.now(timezone.utc).isoformat(),
             "summary":dict(Counter(x["status"] for x in checked)),
             "note":"ATS listing does not verify working Apply button; no Brave API used.",
             "results":checked}
    (OUT/"verification.json").write_text(json.dumps(results,indent=2)+"\n")
    cols=["company","title","location","official_posting","current_posting_url","status","listing_status",
          "feed_evidence","apply_form","new_base","new_ote","years_requirement_heuristic",
          "description_source","description_characters","questions","hard_exclusions","evidence"]
    with (OUT/"verification.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore")
        writer.writeheader()
        for x in checked:
            out=dict(x)
            for k in ("new_base","new_ote","questions","hard_exclusions","evidence"):
                out[k]=json.dumps(out.get(k,[]))
            writer.writerow(out)
    (OUT/"verification.md").write_text(report_md(checked,opts.source_run))
    print("VERIFY-COMPLETE source={} input={} output={} boards={} {}".format(
        opts.source_run,len(selection),len(checked),len(unique_sources),
        json.dumps(results["summary"],sort_keys=True)))
    for r in checked:
        if r["status"]=="PRIORITY_REVIEW":
            print(f"VERIFY-PRIORITY|{r['company']}|{r['title']}|{r['current_posting_url']}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
