#!/usr/bin/env python3
"""Ranked daily report. Does not modify the real-time job-alert watcher."""
import argparse, csv, hashlib, json, os, sys, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import watch, targeted_scan as target, discovery_v2 as discovery

ROOT=Path(__file__).resolve().parent
ROSTER=ROOT/"deep_search_roster.json"
SEEN=ROOT/"deep_search_seen.json"
OUT=ROOT/"deep_search_output"
INTERMEDIARIES={"jobgether","swooped","jobot","talentify"}
MORE_TITLES=("client engagement","client relationship","customer relationship",
             "merchant success","account management","customer retention",
             "partner account manager","relationship manager","inside account",
             "inbound sales","customer operations","member success","growth account")
def title_ok(title,focus):
    if target.title_ok(title,focus):return True
    s=" "+title.lower()+" "
    if any(x in s for x in ("technical account", "new graduates", "summer 2027 graduates")):return False
    return focus!="sales" and not any(t in s for t in target.EXCLUDE_TITLES) and any(t in s for t in MORE_TITLES)
def identifier(row):
    data="|".join((row.get("ats",""),row.get("official_posting","").split("?")[0],row.get("title","")))
    return hashlib.sha256(data.lower().encode()).hexdigest()[:20]
def salary(rng):
    if not rng:return "unknown"
    lo=min(v[0] for v in rng);hi=max(v[1] for v in rng)
    return "$"+format(lo,",")+("-$"+format(hi,",") if hi!=lo else "")
def safe(s):
    return str(s or "").replace("|","/").replace("\n"," ").strip()
def load_roster():
    raw=watch.load_json(str(ROSTER),{})
    if not isinstance(raw,dict):return {}
    return {k:[e for e in entries if isinstance(e,list) and len(e)==2]
            for k,entries in raw.items() if k in discovery.KINDS and isinstance(entries,list)}
def read_boards(boards,focus):
    def read(item):
        kind,entry=item
        try:return kind,entry,discovery.fetch_board(kind,entry),None
        except Exception as exc:return kind,entry,[],str(exc)[:150]
    with ThreadPoolExecutor(max_workers=8) as pool:
        batches=list(pool.map(read,boards))
    work,errors,successful,seen=[],[],[],set()
    for kind,entry,jobs,error in batches:
        if error:
            errors.append(kind+":"+entry[1]+":"+error);continue
        if watch.norm_company(entry[0]) in INTERMEDIARIES or entry[1].lower() in INTERMEDIARIES:
            continue
        if jobs:
            successful.append((kind,entry))
        for job in jobs:
            if not title_ok(job.get("title",""),focus):continue
            loc,_=discovery.location_triage(job.get("location",""))
            if loc=="REJECT" or watch.chash(job.get("company","")) in watch.CFG.get("skip_hashes",[]):continue
            sig=(job.get("company","").lower(),job.get("title","").lower(),job.get("location","").lower())
            if sig in seen:continue
            seen.add(sig)
            work.append((kind,job))
    return work,errors,successful
def qualify(item,now,days,prior,baseline):
    kind,job=item
    desc=job.get("desc") or ""
    if len(desc)<200 and job.get("detail"):
        try:desc=job["detail"]() or ""
        except Exception:desc=""
    row=target.describe_job(job,desc,kind,days,now)
    loc,reason=discovery.location_triage(job.get("location",""),desc)
    if loc=="REJECT":
        row["hard_exclusions"].append(reason);row["disposition"]="REJECT"
    elif loc=="VERIFY":
        row["questions"].append(reason)
        if row["disposition"]=="REVIEW":row["disposition"]="VERIFY"
    row["location_verdict"]=loc;row["description_read"]=len(desc)>=200
    row["id"]=identifier(row)
    row["new_to_v2"]=not baseline and row["id"] not in prior
    row["first_observed_at"]=prior.get(row["id"],datetime.fromtimestamp(now,timezone.utc).isoformat())
    age=row["age_days"];score={1:75,2:58,3:40}[row["tier_candidate"]]
    if age is not None and age<=7:score+=18
    elif age is not None and age<=30:score+=10
    elif age is not None and age>120:score-=15
    if row["new_to_v2"]:score+=12
    if row["base_ranges"] and max(v[1] for v in row["base_ranges"])>=80000:score+=8
    if row["disposition"]=="VERIFY":score-=12
    if row["disposition"]=="REJECT":score-=100
    if any("required SaaS" in x or "required B2B" in x for x in row["questions"]):score-=15
    if any("outbound" in x for x in row["questions"]):score-=7
    row["priority_score"]=score
    row["questions"]=sorted(set(row["questions"]))
    row["hard_exclusions"]=sorted(set(row["hard_exclusions"]))
    return row
def render(rows,meta):
    stats=Counter(v["disposition"] for v in rows)
    fresh=sum(v["disposition"]!="REJECT" and v["age_days"] is not None and v["age_days"]<=7 for v in rows)
    active=[r for r in rows if r["disposition"]!="REJECT"]
    lines=["# Daily job discovery — "+meta["local_date"],
           "",
           "Employer feeds: **{}**; jobs matched: **{}**; review: **{}**; verify: **{}**; rejected: **{}**.".format(
               meta["boards"],len(rows),stats["REVIEW"],stats["VERIFY"],stats["REJECT"]),
           "Fresh (7d): {}. Added employer boards: {}. Errors: {}. Brave queries: {} (reserved {}).".format(
               fresh,meta["boards_new"],meta["errors"],len(meta["brave_audit"]),meta["brave_reserved"]),
           "",
           "Initial baseline: first seen does not mean newly posted." if meta["first_run"] else
           "New to V2 means absent from the previous report, not necessarily newly posted.",
           "",
           "**Only employer ATS listings are considered. Apply forms, guaranteed compensation, location eligibility, "
           "and duplicates against your personal JW tracker still require manual verification.**",
           "",
           "| Rank | Company / role | Location | Base / OTE | Screen | Timing and concerns | Posting |",
           "|---|---|---|---|---|---|---|"]
    for i,r in enumerate(active[:30],1):
        notes="; ".join(r["questions"][:2]) or "check Apply form"
        if r["new_to_v2"]:notes="new to V2; "+notes
        age="unknown" if r["age_days"] is None else str(r["age_days"])+"d"
        lines.append("| {} | {} — {} | {} | {} / {} | T{} {} ({}) | {}; {} | [ATS]({}) |".format(
            i,safe(r["company"]),safe(r["title"]),safe(r["location"]),
            salary(r["base_ranges"]),salary(r["ote_ranges"]),r["tier_candidate"],
            r["disposition"],r["priority_score"],age,safe(notes),r["official_posting"]))
    if not active:lines.append("| — | No matches | — | — | — | — | — |")
    lines+=["","## Discovery queries",""]
    for q in meta["brave_audit"]:
        lines.append("- {}: {} — {} search results, {} candidate boards".format(
            q["status"],safe(q["query"]),q["results"],q["candidate_boards"]))
    if not meta["brave_audit"]:lines.append("- No Brave API calls.")
    if meta.get("error_sample",[]):
        lines+=["","## Feed errors",""]
        for x in meta.get("error_sample",[])[:10]:lines.append("- "+safe(x))
    return "\n".join(lines)+"\n"
def run(args):
    now=time.time();dt=datetime.fromtimestamp(now,timezone.utc)
    roster=load_roster()
    base=watch.board_list(watch.load_json(watch.AUTO_PATH,{}))
    extra=[(k,e) for k,entries in roster.items() for e in entries]
    known={(k,e[1].lower()) for k,e in base+extra if len(e)>1}
    key=os.getenv("BRAVE_SEARCH_API_KEY","")
    grant=min(max(int(os.getenv("BRAVE_GRANTED_CALLS","0")),0),3) if args.discover and key else 0
    discovered,audit=discovery.discover(known,now,grant,key)
    additions=discovered[:45]
    boards=base+extra+additions
    jobs,errors,successful=read_boards(boards,args.focus)
    saved=watch.load_json(str(SEEN),{})
    previous=saved.get("jobs",{}) if isinstance(saved,dict) else {}
    if not isinstance(previous,dict):previous={}
    baseline=not previous
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows=list(pool.map(lambda item:qualify(item,now,args.days,previous,baseline),jobs))
    rows.sort(key=lambda r:(r["disposition"]=="REJECT",-r["priority_score"],
                             r["age_days"] is None,r["age_days"] if r["age_days"] is not None else 9999))
    added=0
    for k,e in successful:
        if (k,e) in additions and not any(v[1].lower()==e[1].lower() for v in roster.get(k,[])):
            roster.setdefault(k,[]).append(e);added+=1
    ROSTER.write_text(json.dumps(roster,indent=2,sort_keys=True)+"\n")
    index={k:v for k,v in previous.items() if watch.parse_ts(v) is not None and watch.parse_ts(v)>=now-180*86400}
    for row in rows:index.setdefault(row["id"],row["first_observed_at"])
    SEEN.write_text(json.dumps({"updated_at":dt.isoformat(),"jobs":index},indent=2,sort_keys=True)+"\n")
    meta={"checked_utc":dt.isoformat(),
          "local_date":dt.astimezone(ZoneInfo("America/Chicago")).strftime("%B %d, %Y"),
          "boards":len(boards),"boards_new":added,"errors":len(errors),"error_sample":errors[:20],
          "brave_reserved":grant,"brave_audit":audit,"first_run":baseline}
    OUT.mkdir(exist_ok=True)
    (OUT/"report.json").write_text(json.dumps({"summary":meta,"results":rows},indent=2)+"\n")
    (OUT/"report.md").write_text(render(rows,meta))
    columns=["company","title","location","official_posting","ats","age_days","tier_candidate",
             "disposition","priority_score","base_ranges","ote_ranges","hard_exclusions","questions",
             "new_to_v2","location_verdict","description_read"]
    with (OUT/"report.csv").open("w",newline="") as file:
        writer=csv.DictWriter(file,fieldnames=columns,extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            entry=dict(r)
            for f in ("base_ranges","ote_ranges","hard_exclusions","questions"):entry[f]=json.dumps(r.get(f,[]))
            writer.writerow(entry)
    stats=Counter(r["disposition"] for r in rows)
    print("V2-SUMMARY boards={} candidates={} review={} verify={} rejected={} added={} brave_calls={} errors={}".format(
        len(boards),len(rows),stats["REVIEW"],stats["VERIFY"],stats["REJECT"],added,len(audit),len(errors)))
    for r in rows[:20]:
        if r["disposition"]!="REJECT":
            print("V2|{}|{}|{}|{}|{}|{}".format(r["disposition"],r["priority_score"],
                  r["company"],r["title"],salary(r["base_ranges"]),r["official_posting"]))
    return 0
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--days",type=int,default=7)
    p.add_argument("--focus",choices=("all","customer","sales"),default="all")
    p.add_argument("--discover",action="store_true")
    a=p.parse_args()
    if a.days<1:p.error("days must be >=1")
    return run(a)
if __name__=="__main__":sys.exit(main())
