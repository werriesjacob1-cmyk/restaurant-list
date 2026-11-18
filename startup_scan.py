import json, re, sys, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import watch
from f100_scan import INC, EXC, LOC_OK, LOC_BAD
CO = {"Anthropic":["anthropic"],"Lovable":["lovable"],"Harvey":["harvey"],"ElevenLabs":["elevenlabs"],"Legora":["legora"],"Synthesia":["synthesia"],
"Novabook":["novabook"],"Granola":["granola"],"Trumpet":["trumpet"],"Gong":["gong","gongio"],"Nooks":["nooks"],"Vanta":["vanta"],"Mercury":["mercury","mercurytechnologies"],
"Sierra":["sierra","sierraai"],"Datadog":["datadog"],"Cognition":["cognition","cognitionai"],"Snowflake":["snowflake"],"Apollo.io":["apolloio","apollo"],"Perplexity":["perplexity-ai","perplexity"]}
def titles(kind, slug):
    if kind=="gh":
        d=watch.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        return [(j["title"], j.get("location",{}).get("name",""), j.get("absolute_url","")) for j in d.get("jobs",[])]
    if kind=="ashby":
        d=watch.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        return [(j["title"], j.get("location","") or "", j.get("jobUrl","")) for j in d.get("jobs",[])]
    d=watch.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    return [(j["text"], (j.get("categories") or {}).get("location","") or "", j.get("hostedUrl","")) for j in d]
def run(item):
    name, slugs = item; out=[]; found=False
    for kind in ("gh","ashby","lever"):
        for s in slugs:
            try: ts=titles(kind,s)
            except Exception: continue
            if not ts: continue
            found=True
            out.append(f"F0|{name}|{kind}:{s}|total={len(ts)}")
            for t,l,u in ts:
                if INC.search(t) and not EXC.search(t) and (LOC_OK.search(l) or not l) and not (LOC_BAD.search(l) and not re.search("united states|remote",l,re.I)):
                    out.append(f"F1|{name}|{t}|{l[:60]}||{u}")
            break
        if found: break
    if not found: out.append(f"F0|{name}|NO_BOARD_FOUND")
    return out
with ThreadPoolExecutor(6) as ex:
    for lines in ex.map(run, CO.items()):
        for l in lines: print(l, flush=True)
