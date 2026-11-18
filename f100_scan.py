#!/usr/bin/env python3
"""Read-only Fortune 100 scan. Finds each company's Workday board (probing tenant/host/site), pulls entry-level sales,
account, customer and business-development style titles in the US, and prints F1 lines. Companies without a readable
public Workday board are listed on F0 lines so they can be searched by hand. Amazon is read from its public JSON."""
import json, re, sys, urllib.request, urllib.error, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import watch

WDS = ["wd1", "wd5", "wd3", "wd12", "wd501", "wd2", "wd103", "wd10", "wd4"]
# tenant guesses (Workday subdomain). The script finds host and site itself.
TENANTS = {
 "Walmart": ["walmart"], "UnitedHealth Group": ["uhg"], "CVS Health": ["cvshealth"], "McKesson": ["mckesson"],
 "Cencora": ["cencora"], "Cigna Group": ["cigna"], "Cardinal Health": ["cardinalhealth"], "Nvidia": ["nvidia"],
 "Elevance Health": ["elevancehealth"], "Centene": ["centene"], "Citigroup": ["citi"], "Wells Fargo": ["wf"],
 "Kroger": ["kroger", "krogerco"], "Humana": ["humana"], "Comcast": ["comcast"], "Morgan Stanley": ["ms"],
 "Dell Technologies": ["dell"], "Target": ["target"], "Boeing": ["boeing"], "RTX": ["globalhr", "rtx"],
 "Lowe's": ["lowes"], "Sysco": ["sysco"], "Albertsons": ["albertsons"], "MetLife": ["metlife"],
 "HCA Healthcare": ["hcahealthcare", "hca"], "Capital One Financial": ["capitalone"], "Allstate": ["allstate"],
 "Eli Lilly": ["lilly"], "Merck": ["msd", "merck"], "Nationwide": ["nationwide"], "Broadcom": ["broadcom"],
 "Pfizer": ["pfizer"], "TD Synnex": ["synnex", "tdsynnex"], "TJX": ["tjx"], "Cisco Systems": ["cisco"], "HP": ["hp"],
 "Charter Communications": ["charter"], "Intel": ["intel"], "Ingram Micro Holding": ["ingrammicro"],
 "TIAA": ["tiaa"], "Liberty Mutual Insurance Group": ["libertymutual"], "Travelers": ["travelers"],
 "Bristol-Myers Squibb": ["bristolmyerssquibb", "bms"], "Coca-Cola": ["coke", "cocacola"], "Nike": ["nike"],
 "PepsiCo": ["pepsico"], "Verizon Communications": ["verizon"], "AT&T": ["att"], "Tesla": ["tesla"],
 "Johnson & Johnson": ["jnj"], "UPS": ["ups"], "FedEx": ["fedex"], "Caterpillar": ["cat"], "Ford Motor": ["ford"],
 "General Motors": ["generalmotors", "gm"], "Home Depot": ["homedepot"], "Bank of America": ["bofa", "ghr"],
 "Chevron": ["chevron"], "Exxon Mobil": ["exxonmobil"], "Costco Wholesale": ["costco"], "Meta Platforms": ["meta"],
 "Microsoft": ["microsoft"], "Fannie Mae": ["fanniemae"], "Freddie Mac": ["freddiemac"], "Progressive": ["progressive"],
 "American Express": ["aexp", "americanexpress"], "Procter & Gamble": ["pg"], "Lockheed Martin": ["lockheedmartin"],
 "Delta Air Lines": ["delta"], "United Airlines Holdings": ["united"], "American Airlines Group": ["aa", "americanairlines"],
 "Tyson Foods": ["tysonfoods"], "AbbVie": ["abbvie"], "Prudential Financial": ["prudential"], "General Dynamics": ["gd", "gdms"],
 "Uber Technologies": ["uber"], "USAA": ["usaa"], "Massachusetts Mutual Life": ["massmutual", "mm"],
 "New York Life Insurance": ["newyorklife"], "Walt Disney": ["disney"], "Oracle": ["oracle"], "IBM": ["ibm"],
 "Performance Food Group": ["pfgc", "performancefoodgroup"], "Publix Super Markets": ["publix"], "State Farm Insurance": ["statefarm"],
 "Goldman Sachs Group": ["goldmansachs"], "JPMorgan Chase": ["jpmc"], "Valero Energy": ["valero"], "Phillips 66": ["phillips66"],
 "Marathon Petroleum": ["marathonpetroleum", "mpc"], "ConocoPhillips": ["conocophillips"], "Archer Daniels Midland": ["adm"],
 "Energy Transfer": ["energytransfer"], "Enterprise Products Partners": ["enterpriseproducts"], "StoneX Group": ["stonex"],
 "Galaxy Digital": ["galaxy"], "Apple": ["apple"], "Alphabet": ["google"], "Berkshire Hathaway": ["berkshire"], "Amazon": [],
}
QUERIES = ["sales development", "business development representative", "inside sales", "account manager", "account executive",
           "customer success", "client success", "customer service", "sales representative", "relationship", "sales associate corporate",
           "partner", "renewals", "sales support", "territory", "specialist sales", "associate account", "customer engagement"]
INC = re.compile(r"sales|account (?:manager|executive|representative|coordinator|specialist|associate|management)|business development|customer success|client success|customer (?:service|support|experience|engagement|care|advocate|relationship)|client (?:service|relationship|engagement|associate|support)|relationship (?:manager|associate|specialist)|renewal|partner (?:success|manager|development)|representative|\bsdr\b|\bbdr\b|specialist|coordinator|associate|advisor|consultant|inside", re.I)
EXC = re.compile(r"senior|\bsr\b|director|\bvp\b|vice president|head of|principal|staff|lead\b|chief|president|engineer|developer|scientist|analyst|nurse|pharmac|physician|clinical|driver|warehouse|cashier|store (?:associate|team)|technician|mechanic|attorney|counsel|legal|recruit|marketing|designer|architect|accountant|audit|tax|intern|co-?op|apprentice|\bmanager,|manager\b(?!.*account)|supervisor|trainer|nursing|therapist|pilot|flight attendant|crew|fulfillment|plant|operator|janitor|security officer|cook|chef|barista|merchandis|stocker|loss prevention|laborer|delivery station|fulfillment center|warehouse", re.I)
LOC_OK = re.compile(r"remote|united states|usa|\bus\b|nashville|tennessee|\btn\b|franklin|brentwood|murfreesboro|locations|hybrid", re.I)
LOC_BAD = re.compile(r"india|china|mexico|canada|united kingdom|\buk\b|germany|france|brazil|philippines|poland|ireland|singapore|japan|australia|spain|italy|netherlands|romania|costa rica|colombia|hungary|israel|korea|malaysia|vietnam|argentina|chile|peru", re.I)
HDR = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

def host_alive(tenant, wd):
    import socket
    try:
        socket.gethostbyname(f"{tenant}.{wd}.myworkdayjobs.com"); return True
    except Exception:
        return False

def site_from_root(tenant, wd):
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/"
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=8)
        final = r.geturl()
    except urllib.error.HTTPError as e:
        final = getattr(e, "url", "") or ""
    except Exception:
        return None
    m = re.search(r"myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", final or "")
    site = m.group(1) if m else None
    return None if site in (None, "login", "wday", "en-US", "") else site

def cxs_ok(tenant, wd, site):
    try:
        d = watch.get(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
                      data={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, timeout=10, headers=HDR)
        return "jobPostings" in d
    except Exception:
        return False

def site_candidates(tenant, company):
    base = re.sub(r"[^A-Za-z0-9]", "", company)
    c = [tenant, tenant.capitalize(), tenant.upper(), tenant + "careers", tenant + "Careers", tenant + "_careers", tenant + "_Careers",
         tenant + "_External", tenant + "External", tenant + "ExternalCareerSite", "External", "ExternalSite", "external", "Careers", "careers",
         "External_Career_Site", "ExternalCareerSite", "Careers_External", "Search", "Jobs", "Global", "US", "en-US", base, base + "Careers",
         base + "_Careers", base + "External", "External_Careers", "CareerSite", "Career", "careersite", "jobs", "wd"]
    seen = []; [seen.append(x) for x in c if x not in seen]
    return seen

def find_board(company):
    for t in TENANTS.get(company, []):
        for wd in WDS:
            if not host_alive(t, wd): continue
            s = site_from_root(t, wd)
            if s and cxs_ok(t, wd, s):
                return t, wd, s
            for s in site_candidates(t, company):
                if cxs_ok(t, wd, s): return t, wd, s
            print(f"D|{company}|host alive {t}.{wd} but no site matched (root site={s})", flush=True)
    return None

def cxs_search(t, wd, site, q, off):
    base = f"https://{t}.{wd}.myworkdayjobs.com"
    return watch.get(f"{base}/wday/cxs/{t}/{site}/jobs", data={"appliedFacets": {}, "limit": 20, "offset": off, "searchText": q}, timeout=20)

def scan(row):
    c = row["company"]; out = []
    b = find_board(c)
    if not b: return [f"F0|{c}|rank={row['rank']}|NO_WORKDAY_BOARD_FOUND"]
    t, wd, site = b; seen = set(); n = 0; total = 0
    for q in QUERIES:
        for off in (0, 20, 40):
            try: d = cxs_search(t, wd, site, q, off)
            except Exception: break
            ps = d.get("jobPostings", [])
            for p in ps:
                path = p.get("externalPath", "")
                if not path or path in seen: continue
                seen.add(path); total += 1
                title = p.get("title", ""); loc = p.get("locationsText", "") or ""
                if not INC.search(title) or EXC.search(title): continue
                if LOC_BAD.search(loc) and not re.search(r"united states|remote|\btn\b|tennessee", loc, re.I): continue
                if not LOC_OK.search(loc): continue
                n += 1
                out.append(f"F1|{c}|{title}|{loc[:60]}|{p.get('postedOn','')}|https://{t}.{wd}.myworkdayjobs.com/en-US/{site}{path}")
            if len(ps) < 20: break
    out.insert(0, f"F0|{c}|rank={row['rank']}|WORKDAY {t}.{wd}/{site}|seen={total}|kept={n}")
    return out

def amazon():
    out = []; seen = set()
    for q in ["sales development", "account manager", "account executive", "customer success", "business development", "inside sales", "customer service associate", "relationship manager"]:
        try:
            u = "https://www.amazon.jobs/en/search.json?" + urllib.parse.urlencode({"base_query": q, "country": "USA", "result_limit": 50, "sort": "recent"})
            d = watch.get(u, timeout=25)
        except Exception as e:
            out.append(f"F0|Amazon|rank=1|JSON_ERR {str(e)[:60]}"); break
        for j in d.get("jobs", []):
            title = j.get("title", ""); jid = j.get("id_icims") or j.get("id")
            if jid in seen or not INC.search(title) or EXC.search(title): continue
            seen.add(jid)
            out.append(f"F1|Amazon|{title}|{j.get('normalized_location','')[:60]}|{j.get('posted_date','')}|https://www.amazon.jobs{j.get('job_path','')}")
    out.insert(0, f"F0|Amazon|rank=1|AMAZON_JSON|kept={len(out)}")
    return out

def main():
    rows = json.load(open(sys.argv[1]))
    with ThreadPoolExecutor(8) as ex:
        for lines in ex.map(scan, [r for r in rows if r["company"] != "Amazon"]):
            for l in lines: print(l, flush=True)
    for l in amazon(): print(l, flush=True)

if __name__ == "__main__": main()
