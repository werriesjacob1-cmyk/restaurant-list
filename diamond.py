"""diamond.py - one pipeline for: pay-first scan, fit score, diamond keywords, extra sources,
stale/repost flags, suppression of rejected/applied companies, LinkedIn contact attach, odd titles,
daily diff + optional ntfy alert.
Usage: python diamond.py [--discover] [--min-pay 100000] [--state state_diamond.json]"""
import json, os, re, sys, datetime as dt, time
from concurrent.futures import ThreadPoolExecutor
import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MIN_PAY = int(ARGS[ARGS.index("--min-pay") + 1]) if "--min-pay" in ARGS else 100000
STATE = ARGS[ARGS.index("--state") + 1] if "--state" in ARGS else os.path.join(HERE, "state_diamond.json")
TODAY = dt.date.today()

def jl(p, default):
    try: return json.load(open(os.path.join(HERE, p)))
    except Exception: return default
norm = lambda s: re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()

# ---------------------------------------------------------------- 4. more sources (seed discovery)
def slug_variants(name):
    b = norm(name); first = b.split()[0] if b else ""
    v = [b.replace(" ", ""), b.replace(" ", "-"), first, b.replace(" ", "") + "hq", b.replace(" ", "") + "inc"]
    out = []; [out.append(x) for x in v if x and x not in out]
    return out
def discover():
    seeds = [l.strip() for l in open(os.path.join(HERE, "seeds.txt")) if l.strip()]
    C = jl("config.json", {}); A = jl("auto_roster.json", {})
    known = {(k, s) for k in ("greenhouse", "ashby", "lever") for src in (C.get(k, []), A.get(k, [])) for _, s in src}
    found = jl("discovered_boards.json", {"greenhouse": [], "ashby": [], "lever": []})
    for k in found: known |= {(k, s) for _, s in found[k]}
    def probe(name):
        for kind, fn in (("greenhouse", lambda s: watch.get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs").get("jobs", [])),
                         ("ashby", lambda s: watch.get(f"https://api.ashbyhq.com/posting-api/job-board/{s}").get("jobs", [])),
                         ("lever", lambda s: watch.get(f"https://api.lever.co/v0/postings/{s}?mode=json"))):
            for s in slug_variants(name):
                if (kind, s) in known: return None
                try:
                    if len(fn(s)) > 0: return (kind, name, s)
                except Exception: pass
        return None
    with ThreadPoolExecutor(10) as ex:
        for r in ex.map(probe, seeds):
            if r: found[r[0]].append([r[1], r[2]]); print(f"DISCOVERED|{r[1]}|{r[0]}:{r[2]}", flush=True)
    json.dump(found, open(os.path.join(HERE, "discovered_boards.json"), "w"), indent=0)

# ---------------------------------------------------------------- roster
def roster():
    C = jl("config.json", {}); A = jl("auto_roster.json", {}); X = jl("discovered_boards.json", {})
    extra = {"greenhouse": [["Mercury", "mercury"], ["Gong", "gongio"], ["Datadog", "datadog"], ["Apollo.io", "apolloio"], ["Anthropic", "anthropic"], ["Lovable", "lovable"]],
             "ashby": [["Vanta", "vanta"], ["Harvey", "harvey"], ["ElevenLabs", "elevenlabs"], ["Snowflake", "snowflake"], ["Sierra", "sierra"], ["Perplexity", "perplexity"], ["Legora", "legora"], ["Synthesia", "synthesia"], ["Granola", "granola"], ["Cognition", "cognition"], ["Handshake", "handshake"]],
             "lever": []}
    out, seen = [], set()
    for kind in ("greenhouse", "ashby", "lever"):
        for src in (C.get(kind, []), A.get(kind, []), X.get(kind, []), extra[kind]):
            for n, s in src:
                if (kind, s) not in seen: seen.add((kind, s)); out.append((kind, n, s))
    return out

# ---------------------------------------------------------------- 8. odd titles + base filters
INC = re.compile(r"account executive|account manager|account coordinator|customer success|client success|client partner|sales development|business development|inside sales|sales representative|sales associate|sales consultant|sales specialist|territory|implementation|onboarding|activation|adoption|renewal|dealer (?:success|partner|account|growth|solutions|onboarding|experience)|partner(?:ship)?s? (?:coordinator|associate|specialist|development rep|manager)|sales operations|revenue operations|rev ?ops|deal desk|sales enablement|enablement (?:specialist|coordinator)|customer experience|customer onboarding|project (?:manager|coordinator)|program (?:manager|coordinator)|product (?:manager|operations)|solutions (?:consultant|specialist)(?!.*engineer)|support specialist|growth (?:associate|specialist)|channel|inbound|outbound|\bsdr\b|\bbdr\b|\bam\b|\bcsm\b", re.I)
EXC = re.compile(r"senior|\bsr\b|director|\bvp\b|vice president|head of|principal|staff|chief|president|engineer|developer|scientist|analyst|recruit|talent|legal|counsel|accountant|designer|architect|intern|manager of|lead\b|\biii\b|\biv\b|\bv\b|sales engineer|field|people|hr |payroll|security|marketing", re.I)
EXC_MGR = re.compile(r"\bmanagers?\b(?!.*(?:account|project|program|product|success|partnership))", re.I)
LOC = re.compile(r"remote|united states|usa|\bus\b|u\.s\.|nashville|tennessee|, tn|anywhere|americas|north america|hybrid", re.I)
FOR = re.compile(r"india|canada|united kingdom|\buk\b|germany|france|australia|ireland|mexico|brazil|philippines|poland|spain|netherlands|singapore|japan|london|toronto|dublin|berlin|emea|latam|apac", re.I)
NUM = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d)?\s?[kK])")
def nums(t):
    out = []
    for m in NUM.finditer(t or ""):
        x = m.group(1).replace(",", "").replace(" ", ""); v = float(x[:-1]) * 1000 if x[-1] in "kK" else float(x)
        if 20000 <= v <= 700000: out.append(v)
    return out

def pay_of(kind, j):
    if j.get("pay"): return j["pay"]
    if kind == "greenhouse":
        try:
            m = re.search(r"(?:gh_jid=|jobs/)(\d+)", j["url"]); d = watch.get(f"https://boards-api.greenhouse.io/v1/boards/{j['_slug']}/jobs/{m.group(1)}?pay_transparency=true")
            pr = d.get("pay_input_ranges") or []
            j["_desc"] = watch.strip_html(d.get("content", ""))
            if pr: return "; ".join(f"${r['min_cents']//100:,}-${r['max_cents']//100:,}" for r in pr)
            return j["_desc"][:6000]
        except Exception: return ""
    return ""

# ---------------------------------------------------------------- 2+3. fit score + diamond words
DIAMOND = [(r"dealership|automotive|auto retail|car sales|vehicle sales|fleet", "AUTO"), (r"no degree|degree (?:is )?not required|or equivalent (?:work )?experience|equivalent experience|non-?traditional|career chang", "NO-DEGREE-OK"),
           (r"retail sales|b2c|consultative sales|face-to-face|relationship[- ]based|customer-facing|quota[- ]carrying", "TRANSFERS"), (r"promot(?:e|ion) from within|career path|path to (?:ae|account executive)|ladder", "PATH")]
def years_asked(desc):
    ys = [int(m.group(1)) for m in re.finditer(r"(\d{1,2})\+?\s*(?:-\s*\d+\s*)?(?:or more )?years?[^.]{0,60}(?:experience|sales|selling|customer|account|saas|software)", desc[:9000], re.I)]
    ys = [y for y in ys if y <= 20]
    return min(ys) if ys else None
def score(j, pay_txt, desc):
    s, why = 30, []; t = j["title"]; t = t.replace("|", "/"); l = j.get("location") or ""
    ns = nums(pay_txt); hi = max(ns) if ns else 0
    if hi >= 100000: s += 20; why.append("pay≥100K")
    elif hi >= 75000: s += 15; why.append("pay≥75K")
    elif not ns: s -= 5; why.append("pay not posted")
    y = years_asked(desc); j["_years"] = y
    if y is None: s += 8
    elif y <= 2: s += 12
    elif y == 3: s += 5
    else: s -= 25; why.append(f"asks {y}+ yrs")
    if re.search(r"bachelor.{0,40}(?:required|must)|(?:required|must).{0,40}bachelor|degree required", desc, re.I) and not re.search(r"or equivalent|degree (?:is )?not required|no degree", desc, re.I): s -= 30; why.append("DEGREE REQUIRED")
    elif re.search(r"bachelor|degree", desc, re.I): s -= 3
    if re.search(r"nashville|tennessee|, tn\b", l, re.I): s += 15; why.append("TN")
    elif re.search(r"remote|anywhere", l, re.I): s += 8
    if re.search(r"enterprise|strategic|major accounts|named account", t, re.I): s -= 15; why.append("enterprise-level")
    if re.search(r"smb|commercial|associate|junior|\bi\b|level 1|inbound|renewal|onboarding|implementation|development rep|sdr|bdr", t, re.I): s += 8
    tags = []
    for rx, tag in DIAMOND:
        if re.search(rx, desc, re.I): tags.append(tag); s += (8 if tag in ("AUTO", "NO-DEGREE-OK") else 2)
    if re.search(r"(?:saas|software)[^.]{0,40}(?:required|must)|(?:required|must)[^.]{0,60}(?:saas|software sales)", desc, re.I): s -= 10; why.append("SaaS required")
    return max(0, min(100, s)), why, tags

# ---------------------------------------------------------------- run
def run(item, state_ids):
    kind, name, slug = item; out = []
    try: jobs = watch.ADAPTERS[kind][0](name, slug)
    except Exception: return out
    for j in jobs:
        t = j["title"]; l = j.get("location") or ""
        if not INC.search(t) or EXC.search(t) or EXC_MGR.search(t): continue
        if l and not LOC.search(l): continue
        if FOR.search(l) and not re.search(r"united states|remote - us|usa|\bus\b", l, re.I): continue
        j["_slug"] = slug
        pay_txt = pay_of(kind, j)
        desc = j.get("desc") or j.get("_desc") or ""
        if not desc:
            try: desc = j["detail"]() if j.get("detail") else ""
            except Exception: desc = ""
        ns = nums(pay_txt)
        if MIN_PAY and (not ns or max(ns) < MIN_PAY) and "--any-pay" not in ARGS: continue
        sc, why, tags = score(j, pay_txt, desc)
        age = None
        if j.get("posted"):
            try: age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromtimestamp(float(j["posted"]), dt.timezone.utc)).days
            except Exception: age = None
        flags = []
        if age is not None:
            if age > 180: flags.append(f"GHOST? {age}d"); sc -= 25
            elif age > 60: flags.append(f"STALE {age}d"); sc -= 12
            elif age > 30: flags.append(f"AGING {age}d")
            elif age <= 7: flags.append("FRESH")
        out.append(dict(company=name, title=t.replace("|", "/"), loc=l[:60], pay=(f"{min(ns):,.0f}-{max(ns):,.0f}" if ns else ""), years=j.get("_years"),
                        score=sc, why=why, tags=tags, flags=flags, url=j["url"], id=f"{kind}:{slug}:{j['id']}", kind=kind, age=age))
    return out

def main():
    if "--discover" in ARGS: discover()
    sup = jl("suppress.json", {}); con = jl("contacts.json", {}); st = jl(os.path.basename(STATE), {}) if STATE == os.path.join(HERE, "state_diamond.json") else (json.load(open(STATE)) if os.path.exists(STATE) else {})
    seen_ids = st.get("seen", {}); ros = roster(); res = []
    with ThreadPoolExecutor(14) as ex:
        for r in ex.map(lambda it: run(it, seen_ids), ros): res += r
    now = TODAY.isoformat(); seen_titles = st.get("titles", {})
    for r in res:
        k = norm(r["company"]); r["new"] = r["id"] not in seen_ids
        tk = f"{k}|{norm(r['title'])}"
        if tk in seen_titles and r["id"] not in seen_ids and seen_titles[tk] != r["id"]: r["flags"].append("REPOST")
        sv = sup.get(k)
        r["suppress"] = None
        if sv:
            hold = sv.get("hold_until"); ok = False
            if hold:
                try: ok = dt.datetime.strptime(f"{hold} {TODAY.year}", "%b %d %Y").date() <= TODAY
                except Exception: pass
            r["suppress"] = "REAPPLY OK" if ok else sv["state"].upper() + (f" (hold until {hold})" if hold else "")
        cs = con.get(k, [])
        r["contacts"] = [{"name": c["name"], "title": c["title"], "url": c["url"], "status": c["status"]} for c in cs[:2]]
    res.sort(key=lambda r: (r["suppress"] not in (None, "REAPPLY OK"), -r["score"]))
    live = [r for r in res if r["suppress"] in (None, "REAPPLY OK")]
    json.dump(res, open(os.path.join(HERE, "diamond_report.json"), "w"), indent=1)
    with open(os.path.join(HERE, "diamond_report.md"), "w") as f:
        f.write(f"# Diamond report {now}\nRoster {len(ros)} boards. {len(res)} roles (min pay {MIN_PAY}); {len(live)} not suppressed. NEW since last run: {sum(1 for r in live if r['new'])}\n\n")
        for r in live[:150]:
            f.write(f"- [{r['score']}] {r['company']} | {r['title']} | {r['loc']} | ${r['pay']} | yrs:{r['years']} | {' '.join(r['flags'])} {'NEW' if r['new'] else ''} | {' '.join(r['tags'])} | {'; '.join(r['why'])} | {r['suppress'] or ''} | {r['url']}\n")
            for c in r["contacts"]: f.write(f"    contact: {c['name']} ({c['title']}) {c['url']} [{c['status']}]\n")
        f.write("\n## Suppressed (rejected/applied)\n")
        for r in res:
            if r["suppress"] not in (None, "REAPPLY OK"): f.write(f"- {r['company']} | {r['title']} | {r['suppress']} | {r['url']}\n")
    for r in live[:60]: print(f"D1|{r['score']}|{r['company']}|{r['title']}|{r['loc']}|{r['pay']}|{r['years']}|{' '.join(r['flags'])}|{' '.join(r['tags'])}|{r['suppress'] or ''}|{r['url']}", flush=True)
    st["seen"] = {**seen_ids, **{r["id"]: now for r in res}}; st["titles"] = {**seen_titles, **{f"{norm(r['company'])}|{norm(r['title'])}": r["id"] for r in res}}
    json.dump(st, open(STATE, "w"))
    hot = [r for r in live if r["new"] and r["score"] >= 75]
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if hot and topic:
        msg = "\n".join(f"{r['score']} {r['company']}: {r['title']} ${r['pay']}" for r in hot[:8])
        try: watch.send_ntfy(topic, f"{len(hot)} new strong-fit roles", msg, hot[0]["url"])
        except Exception as e: print("ntfy fail", e)
    print("DONE", len(ros), len(res), len(live), flush=True)
main()
