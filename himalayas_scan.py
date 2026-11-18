import json, sys, urllib.parse
import watch
QS = ["sales development representative", "business development representative", "account executive", "account manager", "customer success", "implementation specialist", "project coordinator", "program coordinator", "inside sales"]
seen = set()
for q in QS:
    for base in ("https://himalayas.app/jobs/api/search?", ):
        try:
            d = watch.get(base + urllib.parse.urlencode({"q": q, "country": "US", "sort": "recent"}), timeout=25)
        except Exception as e:
            print(f"H0|{q}|ERR {str(e)[:120]}", flush=True); continue
        jobs = d.get("jobs") or d.get("data") or []
        print(f"H0|{q}|keys={list(d.keys())[:8]}|n={len(jobs)}", flush=True)
        for j in jobs:
            t = j.get("title", ""); co = j.get("companyName") or j.get("company", ""); u = j.get("applicationLink") or j.get("guid") or j.get("url", "")
            if u in seen: continue
            seen.add(u)
            lo, hi = j.get("minSalary"), j.get("maxSalary")
            print(f"H1|{co}|{t}|{','.join(j.get('locationRestrictions') or [])[:40]}|{lo}-{hi}|{j.get('seniority')}|{u}", flush=True)
