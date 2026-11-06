"""Pull every SDR/BDR/CSM/AM/renewals-type posting from all monitored boards, posted within N days, remote US or Nashville.
No pay/years/degree filters; pay printed when readable."""
import sys, re, time, json
from concurrent.futures import ThreadPoolExecutor
import watch
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
T = re.compile(r"sales development|business development|\bsdr\b|\bbdr\b|\bsdr/bdr|customer success|client success|customer success associate|account manager|account management|renewal|customer onboarding|onboarding specialist|inside sales|sales representative|customer experience|implementation (specialist|consultant|manager)|solutions consultant|customer support|account coordinator|sales operations|revenue operations|sales enablement|crm", re.I)
X = re.compile(r"\b(vp|vice president|director|head of|principal|staff|chief|intern|engineer|developer|designer|attorney|counsel|recruiter|enterprise|strategic|senior manager|sr\. manager|manager,? (customer success|account)|team lead|lead,|supervisor)\b", re.I)
US = re.compile(r"remote|united states|\busa\b|\bu\.s\.|\bus\b|anywhere|nashville|\btn\b|tennessee|north america|americas", re.I)
FOR = re.compile(r"\b(uk|london|emea|apac|india|canada|toronto|australia|sydney|melbourne|ireland|dublin|germany|berlin|france|paris|singapore|brazil|mexico|israel|tel aviv|latam|netherlands|amsterdam|spain|madrid|poland|philippines|japan|tokyo)\b", re.I)
def loc_pass(l):
    l = l or ""
    if not l.strip(): return True
    if re.search(r"nashville|\btn\b|tennessee", l, re.I): return True
    if FOR.search(l) and not re.search(r"united states|\busa\b|\bu\.s\.|\bus\b", l, re.I): return False
    return bool(US.search(l))
auto = watch.load_json(watch.AUTO_PATH, {})
boards = [b for b in watch.board_list(auto) if b[0] != "workday"]
seen_b = set(); uniq = []
for k, e in boards:
    key = (k, str(e[1:] if isinstance(e, list) else e))
    if key in seen_b: continue
    seen_b.add(key); uniq.append((k, e))
now = time.time()
def work(b):
    k, e = b
    fn = watch.ADAPTERS[k][0]
    try:
        args = e if isinstance(e, (list, tuple)) else [e, e]
        jobs = fn(*args)
    except Exception as ex:
        return [("ERR", k, str(e)[:40])]
    out = []
    for j in jobs:
        if not T.search(j["title"]) or X.search(j["title"]): continue
        if not loc_pass(j["location"]): continue
        p = j.get("posted")
        if p is not None and (now - p) / 86400 > DAYS: continue
        pay = j.get("pay") or ""
        if not pay:
            try:
                txt = j["desc"] or (j["detail"]() if j.get("detail") else "")
                fp = watch.find_pay(txt or "")
                pay = str(fp) if fp else ""
            except Exception:
                pass
        age = "?" if p is None else f"{int((now-p)/86400)}d"
        out.append(("JOB", j["company"], j["title"], j["location"][:45], age, pay[:60], j["url"]))
    return out
errs = 0; n = 0
with ThreadPoolExecutor(12) as ex:
    for res in ex.map(work, uniq):
        for r in res:
            if r[0] == "ERR": errs += 1; continue
            n += 1; print("FRESH|" + "|".join(r[1:]), flush=True)
print(f"FRESH-TOTAL boards={len(uniq)} errors={errs} jobs={n}")
