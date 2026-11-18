#!/usr/bin/env python3
"""Read-only: for each listed role, find it on the employer ATS board and print pay/years/degree/weekend/travel evidence."""
import json, re, sys
from concurrent.futures import ThreadPoolExecutor
import watch, targeted_scan as target

FETCH = {"greenhouse": watch.fetch_greenhouse, "ashby": watch.fetch_ashby, "lever": watch.fetch_lever,
         "workable": watch.fetch_workable, "rippling": watch.fetch_rippling,
         "smartrecruiters": watch.fetch_smartrecruiters, "bamboohr": watch.fetch_bamboohr}

def norm(s): return re.sub(r"[^a-z0-9]", "", str(s).lower())

def candidate_boards(row):
    out = []
    if row.get("ats") and row.get("slug"):
        out.append((row["ats"], row["slug"]))
    n = norm(row["company"])
    for kind in FETCH:
        for e in watch.CFG.get(kind, []):
            if norm(e[0]) == n or (len(n) > 4 and (n in norm(e[0]) or norm(e[0]) in n)):
                out.append((kind, e[1]))
    for p in watch.CFG.get("probe", []):
        if norm(p["company"].split("|")[0]) == n:
            for t in p["tries"]:
                if t[0] in FETCH: out.append((t[0], t[1]))
    seen, res = set(), []
    for x in out:
        if x not in seen: seen.add(x); res.append(x)
    return res[:8]

def desc_of(job):
    d = job.get("desc") or ""
    if len(d) < 200 and job.get("detail"):
        try: d = job["detail"]() or ""
        except Exception: pass
    return d

def years(text):
    m = re.findall(r"(\d{1,2})\s*\+?\s*(?:[-–to]+\s*\d{1,2}\s*)?\+?\s*years?", text or "", re.I)
    return max([int(x) for x in m], default=None) if m else None

def check(row):
    boards = candidate_boards(row)
    if not boards:
        return "VT2|%s|NO_BOARD|%s|no readable employer board in repo config" % (row["company"], row["title"])
    job = None; errs = []
    for kind, slug in boards:
        try:
            if kind == "workday":
                watch.CFG["workday_queries"] = [re.sub(r"\(.*", "", row["title"]).strip()[:40] or "sales"]
                jobs = watch.fetch_workday(row["company"], slug, row["wd"], row["site"])
            else:
                jobs = FETCH[kind](row["company"], slug)
        except Exception as e:
            errs.append("%s:%s ERR" % (kind, slug)); continue
        jid = str(row.get("jobid") or "").lower()
        m = [j for j in jobs if jid and jid in (str(j.get("id", "")) + " " + str(j.get("url", ""))).lower()]
        if not m:
            m = [j for j in jobs if norm(j["title"]) == norm(row["title"])]
        if not m:
            kw = norm(row["title"])[:14]
            m = [j for j in jobs if kw and kw in norm(j["title"])]
        if m:
            job = m[0]; break
        errs.append("%s:%s total=%d no match" % (kind, slug, len(jobs)))
    if not job:
        return "VT2|%s|NOT_ON_BOARD|%s|%s" % (row["company"], row["title"], "; ".join(errs)[:200])
    text = desc_of(job)
    ev = target.pay_evidence(text, job.get("pay", ""))
    def rng(k): return ",".join("$%s-$%s" % (format(a, ","), format(b, ",")) for a, b in ev.get(k, [])) or "-"
    flags = []
    if target.REQUIRED_DEGREE.search(text) and not target.EQUIVALENT.search(text): flags.append("DEGREE_REQUIRED")
    elif target.EQUIVALENT.search(text): flags.append("degree-or-equiv")
    if target.WEEKEND_REQUIRED.search(text): flags.append("WEEKEND_REQUIRED")
    elif re.search(r"saturday|weekend", text, re.I):
        m = re.search(r".{0,50}(?:saturday|weekend).{0,60}", text, re.I); flags.append("weekend-mention:" + m.group(0).replace("\n", " ").strip())
    t = target.TRAVEL.search(text)
    if t: flags.append("travel:%s%%" % (t.group(1) or t.group(2)))
    if target.OUTBOUND_ONLY.search(text): flags.append("OUTBOUND_ONLY")
    if re.search(r"cold.call", text, re.I): flags.append("cold-calling")
    if target.PEOPLE.search(text): flags.append("PEOPLE_MGMT")
    if "--excerpts" in sys.argv:
        for m in re.finditer(r"(bachelor|degree|saturday|weekend|\$\s?\d|salary|base pay|commission|on.target|travel|cold.call|salesforce|outreach|hybrid|in.office|office)", text, re.I):
            s0 = max(0, m.start() - 80)
            print("VX|%s|%s" % (row["company"], text[s0:m.end() + 110].replace("\n", " ")))
    return "VT2|%s|OPEN|%s|%s|base=%s|ote=%s|ad=%s|yrs=%s|chars=%d|%s|%s" % (
        row["company"], job["title"], job.get("location", "")[:40], rng("base"), rng("ote"), rng("ambiguous"),
        years(text), len(text), "; ".join(flags)[:240], job.get("url", ""))


JR = re.compile(r"sales development|business development|\bsdr\b|\bbdr\b|associate|coordinator|specialist|representative|\brep\b|junior|\bjr\b|\bi\b$|entry|account manager|customer success|client success|renewal|inside sales|account executive|support", re.I)
SR = re.compile(r"senior|\bsr\b|director|\bvp\b|head of|principal|staff|lead\b|enterprise|engineer|developer|manager,? (?:sales|customer success) |strategic|executive director|president|architect|analyst|counsel|recruit|marketing|designer|scientist|finance|accountant", re.I)
def scan(row):
    boards = candidate_boards(row)
    if not boards:
        return ["VS|%s|NO_BOARD" % row["company"]]
    out = []; errs = []
    for kind, slug in boards:
        try:
            jobs = FETCH[kind](row["company"], slug)
        except Exception:
            errs.append("%s:%s ERR" % (kind, slug)); continue
        hits = [j for j in jobs if JR.search(j["title"]) and not SR.search(j["title"])]
        out.append("VS|%s|BOARD|%s:%s|total=%d|hits=%d" % (row["company"], kind, slug, len(jobs), len(hits)))
        for j in hits[:25]:
            out.append("VS|%s|JOB|%s|%s|%s" % (row["company"], j["title"], (j.get("location") or "")[:40], j.get("url", "")))
        if jobs: break
    if not out: out = ["VS|%s|ERR|%s" % (row["company"], "; ".join(errs)[:150])]
    return out

def main():
    if len(sys.argv) > 2 and sys.argv[2] != "--excerpts": pass
    rows = json.load(open(sys.argv[1]))
    rows = [r for r in rows]
    with ThreadPoolExecutor(6) as ex:
        if rows and rows[0].get("scan"):
            for lines in ex.map(scan, rows):
                for line in lines: print(line, flush=True)
        else:
            for line in ex.map(check, rows): print(line, flush=True)
if __name__ == "__main__": main()
