import json, re, sys
from concurrent.futures import ThreadPoolExecutor
import watch
C = json.load(open("config.json")); A = json.load(open("auto_roster.json"))
extra = {"greenhouse": [["Mercury","mercury"],["Gong","gongio"],["Datadog","datadog"],["Apollo.io","apolloio"],["Anthropic","anthropic"],["Lovable","lovable"]],
         "ashby": [["Vanta","vanta"],["Harvey","harvey"],["ElevenLabs","elevenlabs"],["Snowflake","snowflake"],["Sierra","sierra"],["Perplexity","perplexity"],["Legora","legora"],["Synthesia","synthesia"],["Granola","granola"],["Cognition","cognition"]],
         "lever": []}
rost = []
seen = set()
for kind in ("greenhouse", "ashby", "lever"):
    for src in (C.get(kind, []), A.get(kind, []), extra[kind]):
        for n, s in src:
            if (kind, s) in seen: continue
            seen.add((kind, s)); rost.append((kind, n, s))
INC = re.compile(r"sales development|business development (?:rep|associate|specialist|executive)|\bsdr\b|\bbdr\b|development representative|account development|lead development|market development|go[- ]to[- ]market (?:associate|rep|specialist)|\bgtm\b (?:associate|rep|specialist)|growth associate|revenue associate|(?:inbound|outbound|inside|smb|new business) sales|sales associate|sales specialist|partner development|partnerships associate", re.I)
EXC = re.compile(r"senior|\bsr\b|director|\bvp\b|vice president|head of|principal|staff|chief|president|engineer|developer|scientist|analyst|recruit|legal|counsel|accountant|designer|architect|intern|manager of|\bmanagers?\b(?!.*(?:account|project|program|product|success))|\blead\b(?! (?:development|generation|gen))|\biii\b|\biv\b|\bii\b(?=.*senior)|solutions (?:architect|engineer|consultant)|sales engineer|field", re.I)
LOC = re.compile(r"remote|united states|usa|\bus\b|u\.s\.|nashville|tennessee|, tn|anywhere|americas|north america", re.I)
FOR = re.compile(r"india|canada|united kingdom|\buk\b|germany|france|australia|ireland|mexico|brazil|philippines|poland|spain|netherlands|singapore|japan|london|toronto|dublin|berlin", re.I)
NUM = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?\s?[kK])")
def nums(t):
    out = []
    for m in NUM.finditer(t or ""):
        x = m.group(1).replace(",", "").replace(" ", "")
        v = float(x[:-1]) * 1000 if x[-1] in "kK" else float(x)
        if 20000 <= v <= 600000: out.append(v)
    return out
def pay_of(kind, j):
    p = j.get("pay") or ""
    if p: return p
    if kind == "greenhouse":
        try:
            u = j["url"]; m = re.search(r"(?:gh_jid=|jobs/)(\d+)", u)
            slug = j["_slug"]
            d = watch.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{m.group(1)}?pay_transparency=true")
            pr = d.get("pay_input_ranges") or []
            if pr: return "; ".join(f"${r['min_cents']//100:,}-${r['max_cents']//100:,} {r.get('title','')[:30]}" for r in pr)
            return watch.strip_html(d.get("content", ""))[:6000]
        except Exception: return ""
    return ""
def run(item):
    kind, name, slug = item; out = []
    try: jobs = watch.ADAPTERS[kind][0](name, slug)
    except Exception: return out
    for j in jobs:
        t = j["title"]; l = j.get("location") or ""
        if not INC.search(t) or EXC.search(t): continue
        if l and not LOC.search(l): continue
        if FOR.search(l) and not re.search("united states|remote|\\bus\\b|usa", l, re.I): continue
        j["_slug"] = slug
        p = pay_of(kind, j)
        ns = nums(p)
        if not ns or max(ns) < 70000: continue
        desc = j.get("desc") or ""
        if not desc:
            try: desc = (j["detail"]() if j.get("detail") else "")
            except Exception: desc = ""
        yrs = re.findall(r"(\d+)\+?\s*(?:-\s*\d+\s*)?years?", desc[:8000], re.I)
        deg = "BACHELOR REQ" if re.search(r"bachelor.{0,40}(?:required|must)|(?:required|must).{0,40}bachelor", desc, re.I) else ("degree pref" if re.search("bachelor|degree", desc, re.I) else "")
        out.append(f"S1|{name}|{t}|{l[:50]}|{min(ns):,.0f}-{max(ns):,.0f}|yrs:{','.join(sorted(set(yrs))[:3])}|{deg}|{j['url']}")
    return out
with ThreadPoolExecutor(12) as ex:
    for lines in ex.map(run, rost):
        for l in lines: print(l, flush=True)
print("DONE", len(rost), flush=True)
