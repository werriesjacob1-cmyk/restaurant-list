#!/usr/bin/env python3
"""Find job boards the repo does not monitor yet. Read-only; prints results to the log.
Tries greenhouse/ashby/lever slug variants for each company name, and a set of Workday tenants.
Usage: python discover_boards.py names_file.json [--workday]"""
import json, re, sys
from concurrent.futures import ThreadPoolExecutor
import watch, targeted_scan as ts

def known():
    s = set()
    for k in ("greenhouse", "ashby", "lever", "workable", "rippling", "smartrecruiters", "bamboohr"):
        for e in watch.CFG.get(k, []):
            s.add(re.sub(r"[^a-z0-9]", "", str(e[1]).lower()))
            s.add(re.sub(r"[^a-z0-9]", "", str(e[0]).lower()))
    return s

def variants(name):
    n = name.lower()
    a = re.sub(r"[^a-z0-9]", "", n)
    b = re.sub(r"[^a-z0-9]+", "-", n).strip("-")
    out = [a]
    if b != a: out.append(b)
    out += [a + "hq", a + "inc", a + "co"]
    return list(dict.fromkeys(out))

FET = {"greenhouse": watch.fetch_greenhouse, "ashby": watch.fetch_ashby, "lever": watch.fetch_lever}

def try_company(name):
    res = []
    for slug in variants(name):
        for kind, fn in FET.items():
            try:
                jobs = fn(name, slug)
            except Exception:
                continue
            if jobs:
                res.append((name, kind, slug, jobs))
        if res: break
    return res

def show(name, kind, slug, jobs):
    hits = [j for j in jobs if ts.title_ok(j["title"], "sales") or ts.title_ok(j["title"], "customer")]
    print(f"FOUND|{name}|{kind}|{slug}|total={len(jobs)}|matches={len(hits)}", flush=True)
    for j in hits[:8]:
        print(f"JOB|{name}|{kind}:{slug}|{j['title']}|{j['location'][:45]}|{j.get('url','')}", flush=True)

def workday_try(name, tenant, sites):
    for wd in ("wd1", "wd3", "wd5", "wd12", "wd501"):
        for site in sites:
            try:
                d = watch.get(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                              data={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
                if d.get("total") is not None:
                    return (name, tenant, wd, site, d.get("total"))
            except Exception:
                continue
    return None

if __name__ == "__main__":
    f = sys.argv[1]
    data = json.load(open(f))
    if "--workday" in sys.argv:
        def run(e):
            name, tenant = e[0], e[1]
            sites = e[2:] or [tenant, "External", "Careers", "External_Career_Site", tenant + "Careers", "Jobs"]
            return workday_try(name, tenant, sites)
        with ThreadPoolExecutor(8) as ex:
            for r in ex.map(run, data):
                if r:
                    name, tenant, wd, site, tot = r
                    print(f"WDFOUND|{name}|{tenant}|{wd}|{site}|total={tot}", flush=True)
                    try:
                        watch.CFG["workday_queries"] = ["sales development", "business development", "account manager", "customer success", "inside sales", "account executive", "client success", "renewal"]
                        jobs = watch.fetch_workday(name, tenant, wd, site)
                        for j in jobs:
                            if ts.title_ok(j["title"], "sales") or ts.title_ok(j["title"], "customer"):
                                print(f"WDJOB|{name}|{j['title']}|{j['location'][:45]}|{j['url']}", flush=True)
                    except Exception as e:
                        print(f"WDERR|{name}|{str(e)[:60]}", flush=True)
    else:
        skip = known()
        todo = [n for n in data if re.sub(r"[^a-z0-9]", "", n.lower()) not in skip]
        print(f"checking {len(todo)} of {len(data)} (skipped {len(data)-len(todo)} already monitored)", flush=True)
        with ThreadPoolExecutor(8) as ex:
            for res in ex.map(try_company, todo):
                for name, kind, slug, jobs in res:
                    show(name, kind, slug, jobs)
