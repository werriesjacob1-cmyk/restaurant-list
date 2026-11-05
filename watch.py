#!/usr/bin/env python3
"""Job-board watcher.

Polls public ATS feeds (Greenhouse, Ashby, Lever, plus experimental Workday, Workable,
SmartRecruiters, Rippling, BambooHR) and, optionally, aggregator feeds (Adzuna, Remotive,
WeWorkRemotely). Sends a push (ntfy, with optional Telegram backup) for NEW matching roles.
No personal data lives in this repo: company skip-list is stored as hashes.
"""
import hashlib, html, json, os, re, sys, time, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "config.json")
AUTO_PATH = os.path.join(HERE, "auto_roster.json")
STATE_PATH = os.path.join(HERE, "state.json")
FILTERED_LOG = os.path.join(HERE, "filtered_log.jsonl")
UA = {"User-Agent": "Mozilla/5.0 (compatible; board-watch/2.0)"}
MAX_ALERTS_PER_RUN = 10
PRUNE_DAYS = 120

def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

CFG = load_json(CFG_PATH, {})

# ---------------------------------------------------------------- http
def http(url, data=None, headers=None, timeout=25, method=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def get(url, **kw):
    return json.loads(http(url, **kw))

# ---------------------------------------------------------------- text helpers
def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()

def norm_company(name):
    name = re.sub(r"\(.*?\)", "", name or "")
    return re.sub(r"[^a-z0-9]", "", name.lower())

def chash(name):
    return hashlib.sha256(norm_company(name).encode()).hexdigest()[:12]

def parse_ts(v):
    """ISO string / epoch ms / epoch s -> epoch seconds or None."""
    if v in (None, ""):
        return None
    try:
        if isinstance(v, (int, float)) or re.fullmatch(r"\d{10,13}", str(v)):
            x = float(v)
            return x / 1000 if x > 1e11 else x
        s = str(v).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None

def age_label(ts, now=None):
    if ts is None:
        return "posting date unknown"
    now = now or time.time()
    m = max(0, (now - ts) / 60)
    if m < 90:
        return f"posted ~{int(m)} min ago"
    if m < 60 * 36:
        return f"posted ~{int(m/60)} h ago"
    return f"older posting (~{int(m/1440)} d)"

# ---------------------------------------------------------------- filters
def title_decision(title):
    """returns (ok, soft_flag_or_None, reason)"""
    tl = " " + (title or "").lower() + " "
    for x in CFG.get("exclude_title_hard", []):
        if x in tl:
            return False, None, f"excluded:{x.strip()}"
    if not any(x in tl for x in CFG.get("include_title", [])):
        return False, None, "no title match"
    for x in CFG.get("soft_title", []):
        if x in tl:
            return True, x.strip(), ""
    return True, None, ""

STATE_NAMES = ("alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho "
               "illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi "
               "missouri montana nebraska nevada new hampshire new jersey new mexico new york north carolina north dakota ohio "
               "oklahoma oregon pennsylvania rhode island south carolina south dakota texas utah vermont virginia washington "
               "wisconsin wyoming").split()
STATE_NAME_RE = re.compile(r"\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|"
    r"illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|"
    r"nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
    r"rhode island|south carolina|south dakota|texas|utah|vermont|virginia|virgina|washington|wisconsin|wyoming)\b")
STATE_ABBR_RE = re.compile(r"(?<![A-Za-z])(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|PA|RI|SC|SD|TX|UT|VT|VA|WA|WV|WI|WY|DC)(?![A-Za-z])")
GENERIC = set("us usa united states of america north americas anywhere within time zone eastern central mountain pacific only based "
              "worker work position open the or and from home wfh virtual".split())

def loc_ok(loc):
    """Pass: blank, Nashville-area, nationwide US remote. Reject: on-site posts in other cities,
    remote posts tied to another state/city, and foreign locations."""
    raw = loc or ""
    ll = raw.lower()
    if not ll.strip():
        return True
    if any(x in ll for x in CFG.get("local_location", [])) or re.search(r"(?<![a-z])tn(?![a-z])", ll):
        return True
    if any(re.search(r"\b" + re.escape(c) + r"\b", ll) for c in CFG.get("foreign", [])) and not re.search(r"united states|\busa\b|\bu\.s\.|\bus\b", ll):
        return False
    if STATE_NAME_RE.search(ll) or STATE_ABBR_RE.search(raw):
        return False
    if re.search(r"united states|\busa\b|\bu\.s\.|\bus\b", ll):
        for c in CFG.get("foreign", []):
            ll = re.sub(r"\b" + re.escape(c) + r"\b", " ", ll)
    rest = re.sub(r"remote|[^a-z ]", " ", ll)
    toks = [t for t in rest.split() if t]
    if toks and not all(t in GENERIC for t in toks):
        return False
    return bool(re.search(r"remote|anywhere|work from home|wfh|virtual|united states|\busa\b|\bus\b|u\.s\.|north america|americas", ll))

def money(tok, k):
    n = float(tok.replace(",", ""))
    if k or n < 1000:
        n *= 1000
    return n

PAY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d{2,3})\s?([kK])?\s*(?:-|–|—|to)\s*\$?\s?(\d{1,3}(?:,\d{3})+|\d{2,3})\s?([kK])?")

OTE_RE = re.compile(r"(?:OTE|on[- ]target earnings|total (?:target )?(?:compensation|earnings))[^$]{0,60}\$\s?(\d{2,3}(?:,\d{3})+)", re.I)

def find_pay(text):
    for m in PAY_RE.finditer(text or ""):
        lo, hi = money(m.group(1), m.group(2)), money(m.group(3), m.group(4) or m.group(2))
        if 30000 <= lo <= hi <= 400000:
            return lo, hi
    m = OTE_RE.search(text or "")
    if m:
        v = float(m.group(1).replace(",", ""))
        if 30000 <= v <= 400000:
            return v, v
    for m in []:
        lo, hi = money(m.group(1), m.group(2)), money(m.group(3), m.group(4) or m.group(2))
        if 30000 <= lo <= hi <= 400000:
            return lo, hi
    return None

def flags_from_text(text):
    t = text or ""
    out = []
    if re.search(r"\b(saturday|sunday|weekends?)\b", t, re.I):
        out.append("WEEKEND/SATURDAY mentioned")
    if re.search(r"on[- ]call", t, re.I):
        out.append("on-call")
    if re.search(r"cold[- ]call|outbound (?:calls?|prospecting)|prospecting|hunter|quota", t, re.I):
        out.append("outbound/quota language")
    m = re.search(r"(\d{1,2})\s?%[^.]{0,40}travel|travel[^.]{0,40}?(\d{1,2})\s?%", t, re.I)
    if m:
        out.append("travel " + (m.group(1) or m.group(2)) + "%")
    elif re.search(r"\btravel\b", t, re.I):
        out.append("travel mentioned")
    m = re.search(r"(\d{1,2})\s?\+?\s?(?:-\s?\d{1,2}\s?)?\+?\s?years?[^.]{0,60}experience|experience[^.]{0,40}?(\d{1,2})\s?\+?\s?years?", t, re.I)
    if m:
        out.append("years: " + (m.group(1) or m.group(2)) + "+")
    if re.search(r"bachelor'?s?[^.]{0,30}(required|needed)|requires? a bachelor|degree is required", t, re.I):
        out.append("DEGREE required")
    elif re.search(r"bachelor|degree", t, re.I):
        out.append("degree mentioned")
    if re.search(r"\bOTE\b|on[- ]target earnings", t):
        out.append("OTE mentioned (check base)")
    if re.search(r"dealer(ship)?s?\b", t, re.I):
        out.append("dealership mentioned")
    return out

# ---------------------------------------------------------------- adapters
def _job(**k):
    k.setdefault("desc", None)
    k.setdefault("pay", "")
    k.setdefault("posted", None)
    k.setdefault("detail", None)
    return k

def fetch_greenhouse(name, slug):
    d = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    jobs = []
    for j in d.get("jobs", []):
        jid = j["id"]
        jobs.append(_job(id=str(jid), company=name, title=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""), url=j.get("absolute_url", ""),
            posted=parse_ts(j.get("first_published") or j.get("updated_at")),
            detail=(lambda s=slug, i=jid: strip_html(get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs/{i}").get("content", "")))))
    return jobs

def fetch_ashby(name, slug):
    d = get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    jobs = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location", "") or ""
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = (loc + " (Remote)").strip()
        comp = (j.get("compensation") or {}).get("compensationTierSummary", "") or ""
        jobs.append(_job(id=str(j["id"]), company=name, title=j.get("title", ""), location=loc,
            url=j.get("jobUrl", ""), posted=parse_ts(j.get("publishedAt")), pay=comp,
            desc=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", ""))))
    return jobs

def fetch_lever(name, slug):
    d = get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    jobs = []
    for j in d if isinstance(d, list) else []:
        cats = j.get("categories") or {}
        loc = cats.get("location", "") or ""
        if j.get("workplaceType") == "remote" and "remote" not in loc.lower():
            loc = (loc + " (Remote)").strip()
        sr = j.get("salaryRange") or {}
        pay = f"${sr.get('min')}-{sr.get('max')} {sr.get('currency', '')}" if sr.get("min") else ""
        lists = " ".join(strip_html(x.get("text", "") + " " + x.get("content", "")) for x in j.get("lists", []))
        jobs.append(_job(id=str(j["id"]), company=name, title=j.get("text", ""), location=loc,
            url=j.get("hostedUrl", ""), posted=parse_ts(j.get("createdAt")), pay=pay,
            desc=(j.get("descriptionPlain", "") or "") + " " + lists + " " + (j.get("additionalPlain", "") or "")))
    return jobs

def fetch_workday(name, tenant, wd, site):
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    seen, jobs = set(), []
    for q in CFG.get("workday_queries", ["customer success", "account manager"]):
        for off in (0, 20):
            d = get(f"{base}/wday/cxs/{tenant}/{site}/jobs",
                    data={"appliedFacets": {}, "limit": 20, "offset": off, "searchText": q})
            for p in d.get("jobPostings", []):
                path = p.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                jobs.append(_job(id=path, company=name, title=p.get("title", ""),
                    location=p.get("locationsText", ""), url=f"{base}/en-US/{site}{path}",
                    posted=None,
                    detail=(lambda b=base, t=tenant, s=site, pa=path: strip_html(
                        (get(f"{b}/wday/cxs/{t}/{s}{pa}").get("jobPostingInfo") or {}).get("jobDescription", "")))))
            if len(d.get("jobPostings", [])) < 20:
                break
    return jobs

def _workable_text(slug, sc):
    d = get(f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{sc}")
    return strip_html(" ".join(str(d.get(k) or "") for k in ("description", "requirements", "benefits")))

def fetch_workable(name, slug):
    try:
        d = get(f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
                data={"query": "", "location": [], "department": [], "worktype": [], "remote": []})
        res = d.get("results", [])
        return [_job(id=j["shortcode"], company=name, title=j.get("title", ""),
            location=((j.get("location") or {}).get("city", "") or "") + (" (Remote)" if j.get("remote") else ""),
            url=f"https://apply.workable.com/{slug}/j/{j['shortcode']}/", posted=parse_ts(j.get("published")),
            detail=(lambda sl=slug, sc=j["shortcode"]: _workable_text(sl, sc))) for j in res]
    except Exception:
        d = get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
        return [_job(id=j.get("shortcode") or j.get("id"), company=name, title=j.get("title", ""),
            location=(j.get("city", "") or "") + (" (Remote)" if j.get("telecommuting") else ""),
            url=j.get("url", ""), posted=parse_ts(j.get("published_on"))) for j in d.get("jobs", [])]

def fetch_smartrecruiters(name, cid):
    d = get(f"https://api.smartrecruiters.com/v1/companies/{cid}/postings?limit=100")
    jobs = []
    for j in d.get("content", []):
        loc = j.get("location") or {}
        ls = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
        if loc.get("remote"):
            ls += " (Remote)"
        jobs.append(_job(id=str(j["id"]), company=name, title=j.get("name", ""), location=ls,
            url=f"https://jobs.smartrecruiters.com/{cid}/{j['id']}", posted=parse_ts(j.get("releasedDate"))))
    return jobs

def fetch_rippling(name, slug):
    d = get(f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs")
    items = d if isinstance(d, list) else d.get("jobs", [])
    return [_job(id=str(j.get("uuid") or j.get("id")), company=name, title=j.get("name", ""),
        location=(j.get("workLocation") or {}).get("label", "") if isinstance(j.get("workLocation"), dict) else str(j.get("workLocation", "")),
        url=j.get("url") or f"https://ats.rippling.com/{slug}/jobs/{j.get('uuid')}") for j in items]

def fetch_bamboohr(name, slug):
    d = get(f"https://{slug}.bamboohr.com/careers/list")
    out = []
    for j in d.get("result", []):
        loc = j.get("location") or {}
        ls = ", ".join(x for x in [loc.get("city"), loc.get("state")] if x)
        if j.get("isRemote") or j.get("locationType") == "1":
            ls += " (Remote)"
        out.append(_job(id=str(j["id"]), company=name, title=j.get("jobOpeningName", ""), location=ls,
            url=f"https://{slug}.bamboohr.com/careers/{j['id']}",
            detail=(lambda sl=slug, i=j["id"]: strip_html(((get(f"https://{sl}.bamboohr.com/careers/{i}/detail").get("result") or {}).get("jobOpening") or {}).get("description", "")))))
    return out

ADAPTERS = {
    "greenhouse": (fetch_greenhouse, 0), "ashby": (fetch_ashby, 0), "lever": (fetch_lever, 0),
    "workday": (fetch_workday, 14), "workable": (fetch_workable, 9), "smartrecruiters": (fetch_smartrecruiters, 9),
    "rippling": (fetch_rippling, 9), "bamboohr": (fetch_bamboohr, 9),
}

# ---------------------------------------------------------------- notify
def send_ntfy(topic, title, message, click="", priority=4, tags=None):
    payload = {"topic": topic, "title": title[:200], "message": message[:3500], "priority": priority, "tags": tags or ["briefcase"]}
    if click:
        payload["click"] = click
        payload["actions"] = [{"action": "view", "label": "Open", "url": click}]
    last = None
    for i in range(3):
        try:
            http("https://ntfy.sh/", data=payload, timeout=20)
            return True
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    print("ntfy failed:", last)
    return False

def send_telegram(token, chat, text):
    try:
        http(f"https://api.telegram.org/bot{token}/sendMessage",
             data={"chat_id": chat, "text": text[:3900]}, timeout=20)
        return True
    except Exception as e:
        print("telegram failed:", e)
        return False

class Notifier:
    def __init__(self):
        self.topic = os.environ.get("NTFY_TOPIC", "").strip()
        self.tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.failures = 0
        self.sent = 0

    def push(self, title, message, click="", priority=4, tags=None):
        ok = False
        if self.topic:
            ok = send_ntfy(self.topic, title, message, click, priority, tags)
        else:
            print("[no NTFY_TOPIC] would send:", title, "|", message.replace("\n", " / "), click)
        if not ok and self.tg_token and self.tg_chat:
            ok = send_telegram(self.tg_token, self.tg_chat, f"{title}\n{message}\n{click}")
        if self.topic or self.tg_token:
            if ok:
                self.sent += 1
            else:
                self.failures += 1
        return ok

# ---------------------------------------------------------------- alert building
def build_alert(job, soft, now):
    text = " ".join(filter(None, [job.get("desc"), job.get("pay")]))
    fl = flags_from_text(text) if text else []
    pay = job.get("pay") or ""
    rng = find_pay(text)
    below = False
    if rng:
        pay = pay or f"${int(rng[0]):,}-${int(rng[1]):,}"
        if rng[1] < CFG.get("pay_floor", 60000):
            return None, "pay below floor"
        if rng[0] < CFG.get("pay_floor", 60000):
            fl.append("range starts below $60K")
    lines = [job["title"], job.get("location") or "location not shown", age_label(job.get("posted"), now)]
    lines.append("Pay: " + (pay or "not shown, check"))
    if soft:
        lines.append(f"Title flag: {soft}")
    if fl:
        lines.append("Flags: " + "; ".join(fl))
    elif job.get("desc") is None and not job.get("detail_done"):
        lines.append("Flags: description not read, check posting")
    fresh = bool(job.get("posted")) and (now - job["posted"] < 7200)
    prio = 2 if soft else (5 if fresh else 4)
    return {"title": f"NEW: {job['company']}", "message": "\n".join(lines), "click": job.get("url", ""), "priority": prio}, ""

# ---------------------------------------------------------------- aggregator discovery
ATS_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)")),
]

def ats_from_url(url):
    for kind, rx in ATS_PATTERNS:
        m = rx.search(url or "")
        if m and m.group(1).lower() not in ("embed", "jobs"):
            return kind, m.group(1)
    return None

def resolve_final_url(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.geturl()
    except Exception:
        return url

def discover(state, notifier, now, auto, dry=False):
    """Layer 2: keyword search across aggregators. Returns list of candidate dicts."""
    cands = []
    aid, akey = os.environ.get("ADZUNA_APP_ID", ""), os.environ.get("ADZUNA_APP_KEY", "")
    if aid and akey:
        for q in CFG.get("discover_queries", []):
            for where in ("", "Nashville, TN"):
                params = {"app_id": aid, "app_key": akey, "results_per_page": 50, "sort_by": "date",
                          "what_or": q, "max_days_old": 2, "content-type": "application/json"}
                if where:
                    params["where"] = where
                    params["distance"] = 40
                else:
                    params["what_or"] = q + " remote"
                try:
                    d = get("https://api.adzuna.com/v1/api/jobs/us/search/1?" + urllib.parse.urlencode(params))
                except Exception as e:
                    print("adzuna error:", str(e)[:100])
                    continue
                for j in d.get("results", []):
                    created = parse_ts(j.get("created"))
                    if created and now - created > 3 * 86400:
                        continue
                    sm = j.get("salary_max")
                    if sm and sm < CFG.get("pay_floor", 60000):
                        continue
                    loc = (j.get("location") or {}).get("display_name", "")
                    t = j.get("title", "")
                    cands.append(_job(id="adz:" + str(j.get("id")), company=(j.get("company") or {}).get("display_name", "?"),
                        title=strip_html(t), location=loc + (" (Remote)" if re.search(r"remote", t + " " + j.get("description", ""), re.I) else ""),
                        url=j.get("redirect_url", ""), posted=created, desc=strip_html(j.get("description", "")),
                        pay=(f"${int(j['salary_min']):,}-${int(sm):,}" if j.get("salary_min") and sm else "")))
    try:
        d = get("https://remotive.com/api/remote-jobs?category=customer-support&limit=50")
        for j in d.get("jobs", []):
            cands.append(_job(id="rem:" + str(j["id"]), company=j.get("company_name", "?"), title=j.get("title", ""),
                location="Remote " + (j.get("candidate_required_location", "") or ""), url=j.get("url", ""),
                posted=parse_ts(j.get("publication_date")), pay=j.get("salary", "") or "", desc=strip_html(j.get("description", ""))))
    except Exception as e:
        print("remotive error:", str(e)[:100])
    for feed in ("https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
                 "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss"):
        try:
            root = ET.fromstring(http(feed))
            for it in root.iter("item"):
                title = it.findtext("title") or ""
                comp, _, t = title.partition(": ")
                link = it.findtext("link") or ""
                cands.append(_job(id="wwr:" + link, company=comp if t else "?", title=t or title, location="Remote " + (it.findtext("region") or ""),
                    url=link, posted=parse_ts(datetime.strptime(it.findtext("pubDate") or "", "%a, %d %b %Y %H:%M:%S %z").timestamp()) if it.findtext("pubDate") else None,
                    desc=strip_html(it.findtext("description") or "")))
        except Exception as e:
            print("wwr error:", str(e)[:100])
    return cands

def add_auto_roster(auto, kind, slug, company):
    lst = auto.setdefault(kind, [])
    if not any(s == slug for _, s in lst):
        lst.append([company, slug])
        return True
    return False

# ---------------------------------------------------------------- main run
def board_list(auto):
    boards = []
    for kind in ADAPTERS:
        for e in CFG.get(kind, []) + auto.get(kind, []):
            boards.append((kind, e))
    return boards

def board_key(kind, e):
    return kind + ":" + ":".join(str(x) for x in e[1:])

def run(now=None, dry=False):
    now = now or time.time()
    state = load_json(STATE_PATH, {"boards": {}, "meta": {}})
    state.setdefault("boards", {}); state.setdefault("meta", {})
    meta = state["meta"]
    auto = load_json(AUTO_PATH, {})
    skip = set(CFG.get("skip_hashes", []))
    notifier = Notifier()
    alerts_queue, errors, ok_boards = [], [], 0
    filtered = []

    boards = board_list(auto)
    due = []
    for kind, e in boards:
        interval = ADAPTERS[kind][1] * 60
        last = meta.get("last_run_" + kind, 0)
        due.append((kind, e, now - last >= interval - 60))
    ran_kinds = {k for k, e, d in due if d}

    def one(t):
        kind, e = t
        try:
            return kind, e, ADAPTERS[kind][0](e[0], *e[1:]), None
        except Exception as ex:
            return kind, e, [], str(ex)[:140]

    todo = [(k, e) for k, e, d in due if d]
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(one, todo))

    for kind, e, jobs, err in results:
        key = board_key(kind, e)
        b = state["boards"].setdefault(key, {"jobs": {}, "streak": 0, "name": e[0]})
        if err:
            b["streak"] = b.get("streak", 0) + 1
            errors.append(f"{e[0]} ({kind}): {err}")
            continue
        ok_boards += 1
        b["streak"] = 0
        baseline = len(b["jobs"]) == 0 and not b.get("baselined")
        for j in jobs:
            if j["id"] in b["jobs"]:
                continue
            b["jobs"][j["id"]] = now
            if baseline:
                continue
            if chash(j["company"]) in skip:
                continue
            ok, soft, why = title_decision(j["title"])
            if not ok:
                if why != "no title match":
                    filtered.append({"t": int(now), "c": j["company"], "title": j["title"], "why": why})
                continue
            if not loc_ok(j["location"]):
                filtered.append({"t": int(now), "c": j["company"], "title": j["title"], "why": "location: " + (j["location"] or "")[:40]})
                continue
            alerts_queue.append((j, soft))
        b["baselined"] = True
    for k in ran_kinds:
        meta["last_run_" + k] = now

    # Layer 2 discovery (aggregators), every ~25 minutes
    if now - meta.get("last_discover", 0) >= 24 * 60:
        meta["last_discover"] = now
        ab = state["boards"].setdefault("agg", {"jobs": {}, "streak": 0, "name": "aggregators"})
        agg_baseline = not ab.get("baselined")
        follows = 0
        for j in discover(state, notifier, now, auto, dry):
            if j["id"] in ab["jobs"]:
                continue
            ab["jobs"][j["id"]] = now
            if agg_baseline:
                continue
            ok, soft, why = title_decision(j["title"])
            if not ok or not loc_ok(j["location"]) or chash(j["company"]) in skip:
                continue
            if follows < 12 and j["url"] and not j["id"].startswith(("rem:", "wwr:")):
                follows += 1
                fin = resolve_final_url(j["url"])
                hit = ats_from_url(fin)
                if hit:
                    if add_auto_roster(auto, hit[0], hit[1], j["company"]):
                        print("auto-roster add:", j["company"], hit)
                    j["url"] = fin
            alerts_queue.append((j, soft))
        ab["baselined"] = True

    # build + send alerts
    sent = 0
    overflow = 0
    for j, soft in alerts_queue:
        if j.get("detail") and not j.get("desc"):
            try:
                j["desc"] = j["detail"]()
                j["detail_done"] = True
            except Exception:
                j["desc"] = None
        a, why = build_alert(j, soft, now)
        if a is None:
            filtered.append({"t": int(now), "c": j["company"], "title": j["title"], "why": why})
            continue
        if sent >= MAX_ALERTS_PER_RUN:
            overflow += 1
            continue
        print("ALERT:", j["company"], "|", j["title"], "|", j["location"], "|", j["url"])
        notifier.push(a["title"], a["message"], a["click"], a["priority"])
        sent += 1
    if overflow:
        notifier.push("More new jobs", f"{overflow} more new matches this run. Check the Actions log.", "", 3)
    meta["alerts_24h"] = [t for t in meta.get("alerts_24h", []) if now - t < 86400] + [int(now)] * sent

    # heartbeat once a day (~8 AM Central)
    utc = datetime.fromtimestamp(now, timezone.utc)
    today = utc.strftime("%Y-%m-%d")
    if utc.hour == 13 and meta.get("last_heartbeat") != today:
        meta["last_heartbeat"] = today
        failing = [b["name"] for b in state["boards"].values() if b.get("streak", 0) >= 30]
        total = len(boards)
        msg = (f"Watcher alive. {total} boards, {len(failing)} failing for hours"
               + (": " + ", ".join(failing[:12]) if failing else "")
               + f". Alerts last 24h: {len(meta['alerts_24h'])}. Failed sends: {meta.get('send_failures', 0)}.")
        notifier.push("Daily check-in", msg, "", 2, ["white_check_mark"])
        meta["send_failures"] = 0
    meta["send_failures"] = meta.get("send_failures", 0) + notifier.failures
    if todo and ok_boards == 0 and meta.get("all_fail_day") != today:
        meta["all_fail_day"] = today
        notifier.push("WATCHER PROBLEM", "Every board failed this run. Network or API issue.", "", 5, ["warning"])

    # prune
    cutoff = now - PRUNE_DAYS * 86400
    for b in state["boards"].values():
        b["jobs"] = {k: v for k, v in b["jobs"].items() if v >= cutoff}
    # persist
    if not dry:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, separators=(",", ":"), sort_keys=True)
        if auto:
            with open(AUTO_PATH, "w") as f:
                json.dump(auto, f, indent=1, sort_keys=True)
        if filtered:
            old = []
            if os.path.exists(FILTERED_LOG):
                old = open(FILTERED_LOG).read().splitlines()[-400:]
            with open(FILTERED_LOG, "w") as f:
                f.write("\n".join(old + [json.dumps(x) for x in filtered]) + "\n")
    print(f"boards={len(boards)} ran={len(todo)} ok={ok_boards} errors={len(errors)} alerts={sent} overflow={overflow} filtered={len(filtered)}")
    for e in errors[:25]:
        print("  board error:", e)
    return {"alerts": sent, "errors": errors, "queue": alerts_queue, "state": state}

def audit():
    """One-time coverage check: list every open job with the filter's verdict. No alerts, no state."""
    auto = load_json(AUTO_PATH, {})
    skip = set(CFG.get("skip_hashes", []))
    boards = board_list(auto)
    def one(t):
        kind, e = t
        try:
            return kind, e, ADAPTERS[kind][0](e[0], *e[1:]), None
        except Exception as ex:
            return kind, e, [], str(ex)[:100]
    with ThreadPoolExecutor(max_workers=10) as ex:
        res = list(ex.map(one, [(k, e) for k, e in boards]))
    tot = passed = 0
    for kind, e, jobs, err in res:
        if err:
            print(f"AUDIT-ERR {e[0]} ({kind}): {err}"); continue
        for j in jobs:
            tot += 1
            if chash(j["company"]) in skip:
                v = "SKIPCO"
            else:
                ok, soft, why = title_decision(j["title"])
                if not ok: v = "NO:" + why
                elif not loc_ok(j["location"]): v = "NO:location"
                else: v = "PASS" + ("(soft)" if soft else ""); passed += 1
            print(f"AUDIT|{v}|{j['company']}|{j['title']}|{(j['location'] or '')[:40]}")
    print(f"AUDIT-TOTAL boards={len(boards)} jobs={tot} pass={passed}")
    return 0

def detail_audit():
    """Read the full posting for chosen companies (config detail_companies) and print facts. No alerts, no state."""
    auto = load_json(AUTO_PATH, {})
    targets = [x.lower() for x in CFG.get("detail_companies", [])]
    allco = [x.lower() for x in CFG.get("detail_all_companies", [])]
    if CFG.get("detail_workday_queries"):
        CFG["workday_queries"] = CFG["detail_workday_queries"]
    boards = [(k, e) for k, e in board_list(auto) if any(t in e[0].lower() for t in targets + allco)]
    def one(t):
        kind, e = t
        try:
            return kind, e, ADAPTERS[kind][0](e[0], *e[1:]), None
        except Exception as ex:
            return kind, e, [], str(ex)[:100]
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(one, boards))
    work = []
    for kind, e, jobs, err in res:
        if err:
            print(f"DETAIL-ERR {e[0]} ({kind}): {err}"); continue
        seen = set()
        for j in jobs:
            ok, soft, why = title_decision(j["title"])
            extra = any(x in j["title"].lower() for x in CFG.get("detail_extra_titles", []))
            everything = any(a in j["company"].lower() for a in allco)
            if not (ok or extra or everything):
                continue
            if not everything and not loc_ok(j["location"]):
                continue
            key = (j["title"].strip().lower(), j["location"].strip().lower())
            if key in seen:
                continue
            seen.add(key); work.append(j)
    def fill(j):
        txt = j.get("desc")
        if not txt and j.get("detail"):
            try: txt = j["detail"]()
            except Exception as ex: txt = None
        if not txt and j.get("url"):
            try:
                raw = http(j["url"])
                txt = strip_html(raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw))
            except Exception:
                txt = None
        return j, (txt or "")
    with ThreadPoolExecutor(max_workers=8) as ex:
        out = list(ex.map(fill, work))
    for j, txt in out:
        pay = j.get("pay") or find_pay(txt) or "pay not found"
        flags = "; ".join(flags_from_text(txt)) or "no flags"
        posted = age_label(j["posted"]) if j.get("posted") else "posted date not available"
        print(f"DETAIL|{j['company']}|{j['title']}|{j['location'][:40]}|{pay}|{posted}|{flags}|textlen={len(txt)}|{j.get('url','')}")
        if 0 < len(txt) < 15000:
            print(f"DETAILTXT|{j['company']}|{j['title']}|" + re.sub(r"\s+", " ", txt)[:3500])
    print(f"DETAIL-TOTAL boards={len(boards)} jobs={len(out)}")
    return 0


# ---------------------------------------------------------------- deep qualifier
SPECIALTY_RE = re.compile(r"\b(healthcare|health care|hospital|pharmac\w*|insurance|insurtech|banking|fintech|cyber\s?security|legal|law firm|"
    r"accounting|tax|construction|real estate|manufacturing|logistics|energy|utilities|telecom|networking|devops|API|data science|"
    r"payments|mortgage|credit union|ITAD|supply chain|life sciences|biotech|government|higher ed|K-12)\b", re.I)

def years_required(text):
    """Largest 'N+ years' stated as a requirement. Looks at the words right around each match,
    skipping preferred/nice-to-have lines and company-history mentions."""
    best = 0
    t = text or ""
    for m in re.finditer(r"(\d{1,2})\s*\+?\s*(?:(?:to|-|\u2013|\u2014)\s*(\d{1,2})\s*\+?\s*)?years?", t, re.I):
        lo = int(m.group(1))
        if not 0 < lo <= 15:
            continue
        before = t[max(0, m.start() - 90):m.start()].lower()
        after = t[m.end():m.end() + 70].lower()
        near = before + " " + after
        if re.search(r"prefer|ideal|bonus|nice to have|a plus|asset|hope you have|desired", near):
            continue
        if re.search(r"we have|we.ve|founded|since \d{4}|history|in business|ago|for over|over $", before + " " + after[:25]):
            continue
        if re.search(r"\byears?\b[^.]{0,15}(ago|old)", after[:30]):
            continue
        best = max(best, lo)
    return best

def qualify_job(j, txt, skip):
    """Return (verdict, flags). Verdicts: PASS, NEAR, NOPAY, REJ:<reason>."""
    tl = " " + j["title"].lower() + " "
    if chash(j["company"]) in skip:
        return "REJ:company applied/skipped", []
    for x in ("implementation", "onboarding", "training", "enablement", "support specialist", "technical support", "coordinator", "intern"):
        if x in tl:
            return "REJ:onboarding/enablement/support title", []
    if re.search(r"\b(sr\.?|senior|principal|lead|head|director|vp|manager ii|manager iii|enterprise)\b", tl):
        yr = years_required(txt)
        if yr >= 5:
            return f"REJ:senior ({yr}+ yrs)", []
    yrs = years_required(txt)
    if yrs >= 5:
        return f"REJ:asks {yrs}+ yrs", []
    flags, soft = [], []
    if yrs:
        flags.append(f"asks {yrs}+ yrs")
    pay = j.get("pay") or ""
    rng = find_pay(pay if isinstance(pay, str) else "") or find_pay(txt)
    if rng:
        lo, hi = rng
        if hi < CFG.get("income_floor", 75000):
            return f"REJ:total pay ${int(hi/1000)}K under $75K", []
        flags.append(f"${int(lo/1000)}-{int(hi/1000)}K")
        if re.search(r"\bOTE\b|on[- ]target earnings|commission|variable", txt, re.I):
            soft.append("includes commission/OTE")
    nopay = rng is None
    t = txt or ""
    if re.search(r"cold[- ]call|prospecting|hunter|new logo|outbound", t, re.I):
        soft.append("outbound/prospecting language")
    if re.search(r"(?<!no )(?<!not )(?<!without )\b(saturdays?|sundays?|weekends?)\b", t, re.I):
        soft.append("WEEKEND mention")
    m = re.search(r"(\d{1,2})\s?%[^.]{0,40}travel|travel[^.]{0,40}?(\d{1,2})\s?%", t, re.I)
    if m:
        pct = int(m.group(1) or m.group(2))
        if pct > 25:
            return f"REJ:travel {pct}%", []
        if pct >= 15:
            soft.append(f"travel {pct}%")
    if re.search(r"bachelor'?s?[^.]{0,30}(required|needed)|requires? a bachelor|degree is required", t, re.I) and not re.search(r"or equivalent|equivalent (?:work )?experience", t, re.I):
        return "REJ:degree required", []
    if re.search(r"\bdealer(ship)?s?\b", t, re.I) and re.search(r"(sales consultant|dealership employee|work(?:ing)? at a dealership)", t, re.I):
        return "REJ:dealership job", []
    req_sents = " ".join(s for s in re.split(r"(?<=[.!?\n])\s+|\n", t) if re.search(r"\d\s*\+?\s*years|required|must have|requirements", s, re.I) and not re.search(r"prefer|ideal", s, re.I))
    spec = sorted({x.lower() for x in SPECIALTY_RE.findall(req_sents)})
    if spec:
        soft.append("specialty in requirements: " + ",".join(spec[:3]))
    if re.search(r"\bsaas\b", req_sents, re.I):
        soft.append("SaaS asked")
    if re.search(r"salesforce|hubspot|gainsight|totango", req_sents, re.I):
        soft.append("CRM tool named")
    if j.get("posted") and (time.time() - j["posted"]) / 86400 > 120:
        soft.append("posting over 120 days old")
    if nopay:
        return "NOPAY", flags + soft
    hard_soft = [x for x in soft if x.startswith(("specialty", "WEEKEND", "outbound"))]
    return ("NEAR" if hard_soft else "PASS"), flags + soft


def widen_boards(auto):
    """Find employer job boards behind fresh Adzuna listings (last 45 days, several pages, many titles).
    Returns list of (kind, [company, slug]) not already watched. Adds them to auto_roster."""
    aid, akey = os.environ.get("ADZUNA_APP_ID", ""), os.environ.get("ADZUNA_APP_KEY", "")
    if not (aid and akey):
        print("WIDEN: no Adzuna keys in this run")
        return []
    known = set()
    for kind, e in board_list(auto):
        known.add((kind, str(e[1]).lower()))
    queries = CFG.get("widen_queries", ["customer success manager", "account manager", "client success manager", "customer success associate",
                                        "renewal manager", "partner success manager", "client services manager", "account management"])
    urls = {}
    def adz(args):
        q, where, page = args
        params = {"app_id": aid, "app_key": akey, "results_per_page": 50, "sort_by": "date", "what_or": q,
                  "max_days_old": 45, "content-type": "application/json"}
        if where:
            params["where"] = where; params["distance"] = 40
        else:
            params["what_or"] = q + " remote"
        try:
            d = get(f"https://api.adzuna.com/v1/api/jobs/us/search/{page}?" + urllib.parse.urlencode(params))
            return [(j.get("redirect_url", ""), (j.get("company") or {}).get("display_name", "?"), j.get("salary_max")) for j in d.get("results", [])]
        except Exception as e:
            print("WIDEN adzuna error:", str(e)[:80]); return []
    jobs = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(adz, [(q, w, p) for q in queries for w in ("", "Nashville, TN") for p in (1, 2, 3)]):
            jobs += r
    comps = {}
    for u, comp, sm in jobs:
        if comp and comp != "?" and not (sm and sm < CFG.get("pay_floor", 60000)):
            comps[comp] = 1
    skip = set(CFG.get("skip_hashes", []))
    names = [c for c in comps if chash(c) not in skip]
    print(f"WIDEN: {len(jobs)} listings, {len(names)} companies to try")
    def slugs(name):
        base = re.sub(r"\b(inc|llc|ltd|corp|corporation|company|co|the|group|holdings|technologies|technology)\b\.?", " ", name.lower())
        words = re.findall(r"[a-z0-9]+", base)
        out = []
        if words:
            out += ["".join(words), "-".join(words), words[0]]
        w2 = re.findall(r"[a-z0-9]+", name.lower())
        if w2:
            out += ["".join(w2), "-".join(w2)]
        return list(dict.fromkeys(x for x in out if len(x) > 2))
    probes = {
        "greenhouse": lambda sl: len(get(f"https://boards-api.greenhouse.io/v1/boards/{sl}/jobs").get("jobs", [])),
        "lever": lambda sl: len(get(f"https://api.lever.co/v0/postings/{sl}?mode=json")),
        "ashby": lambda sl: len(get(f"https://api.ashbyhq.com/posting-api/job-board/{sl}").get("jobs", [])),
    }
    def try_company(name):
        hits = []
        for sl in slugs(name):
            for kind, fn in probes.items():
                if (kind, sl) in known:
                    continue
                try:
                    if fn(sl) > 0:
                        hits.append((kind, sl))
                except Exception:
                    pass
            if hits:
                break
        return name, hits
    found = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        for name, hits in ex.map(try_company, names[:700]):
            for kind, sl in hits[:1]:
                known.add((kind, sl.lower()))
                if add_auto_roster(auto, kind, sl, name):
                    found.append((kind, [name, sl]))
    print(f"WIDEN: new boards={len(found)}")
    for kind, e in found:
        print(f"WIDEN-NEW|{kind}|{e[0]}|{e[1]}")
    return found

def qualify():
    """Deep search: every board, every title match, full posting read, scored against the Tier 1 rule. No alerts, no state."""
    auto = load_json(AUTO_PATH, {})
    skip = set(CFG.get("skip_hashes", []))
    if CFG.get("detail_workday_queries"):
        CFG["workday_queries"] = CFG["detail_workday_queries"]
    if "--widen" in sys.argv:
        try:
            widen_boards(auto)
            json.dump(auto, open(AUTO_PATH, "w"), indent=1)
        except Exception as ex:
            print("WIDEN failed:", str(ex)[:120])
    boards = board_list(auto)
    def one(t):
        kind, e = t
        try:
            return kind, e, ADAPTERS[kind][0](e[0], *e[1:]), None
        except Exception as ex:
            return kind, e, [], str(ex)[:100]
    with ThreadPoolExecutor(max_workers=10) as ex:
        res = list(ex.map(one, boards))
    work, seen, errs, total = [], set(), 0, 0
    for kind, e, jobs, err in res:
        if err:
            errs += 1; print(f"QUAL-ERR {e[0]} ({kind}): {err}"); continue
        for j in jobs:
            total += 1
            ok, soft, why = title_decision(j["title"])
            if not ok or not loc_ok(j["location"]):
                continue
            key = (j["company"].lower(), j["title"].strip().lower(), j["location"].strip().lower())
            if key in seen:
                continue
            seen.add(key); work.append(j)
    print(f"QUAL-SCOPE boards={len(boards)} board_errors={errs} jobs_seen={total} title_and_location_matches={len(work)}")
    def fill(j):
        txt = j.get("desc")
        if not txt and j.get("detail"):
            try: txt = j["detail"]()
            except Exception: txt = None
        if not txt and j.get("url"):
            try:
                raw = http(j["url"])
                txt = strip_html(raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw))
            except Exception:
                txt = None
        return j, (txt or "")
    with ThreadPoolExecutor(max_workers=10) as ex:
        out = list(ex.map(fill, work))
    buckets = {"PASS": [], "NEAR": [], "NOPAY": []}
    rej = {}
    unread = 0
    for j, txt in out:
        if len(txt) < 200:
            unread += 1
            print(f"QUAL-UNREAD|{j['company']}|{j['title']}|{j.get('url','')}")
            continue
        v, fl = qualify_job(j, txt, skip)
        if v.startswith("REJ:"):
            rej[v[4:]] = rej.get(v[4:], 0) + 1
            continue
        buckets[v].append((j, txt, fl))
    for v in ("PASS", "NEAR", "NOPAY"):
        for j, txt, fl in sorted(buckets[v], key=lambda x: x[0]["company"].lower()):
            posted = age_label(j["posted"]) if j.get("posted") else "posted date n/a"
            print(f"QUAL|{v}|{j['company']}|{j['title']}|{j['location'][:40]}|{posted}|{'; '.join(fl) or 'no flags'}|{j.get('url','')}")
            if any(x.lower() in j["company"].lower() for x in CFG.get("qualify_show", [])):
                print(f"QUALFULL|{j['company']}|{j['title']}|" + re.sub(r"\s+", " ", txt)[:9000])
            if v == "PASS":
                print(f"QUALTXT|{j['company']}|{j['title']}|" + re.sub(r"\s+", " ", txt)[:1800])
    print("QUAL-REJECTED " + json.dumps(rej, sort_keys=True))
    print(f"QUAL-TOTAL pass={len(buckets['PASS'])} near={len(buckets['NEAR'])} nopay={len(buckets['NOPAY'])} unread={unread} rejected={sum(rej.values())}")
    return 0

def fetch_amazon(name, query, loc="Nashville"):
    d = get("https://www.amazon.jobs/en/search.json?result_limit=50&base_query=" + urllib.parse.quote(query) + "&loc_query=" + urllib.parse.quote(loc))
    return [_job(id=str(j.get("id_icims") or j.get("id")), company=name, title=j.get("title", ""),
        location=j.get("normalized_location", "") or j.get("location", ""),
        url="https://www.amazon.jobs" + j.get("job_path", ""), posted=parse_ts(j.get("posted_date"))) for j in d.get("jobs", [])]

def probe():
    """Try candidate boards for companies we could not read; print what each returns."""
    fetchers = {"greenhouse": fetch_greenhouse, "ashby": fetch_ashby, "lever": fetch_lever,
                "workable": fetch_workable, "rippling": fetch_rippling, "smartrecruiters": fetch_smartrecruiters,
                "bamboohr": fetch_bamboohr}
    for p in CFG.get("probe", []):
        kws = [k.lower() for k in p.get("match", [])]
        for t in p["tries"]:
            kind, args = t[0], t[1:]
            try:
                if kind == "workday":
                    jobs = fetch_workday(p["company"], *args)
                elif kind == "amazon":
                    jobs = fetch_amazon(p["company"], *args)
                else:
                    jobs = fetchers[kind](p["company"], *args)
            except Exception as e:
                print(f"PROBE|{p['company']}|{kind}:{'/'.join(args)}|ERR {str(e)[:60]}")
                continue
            hits = [j for j in jobs if not kws or any(k in j["title"].lower() for k in kws)]
            print(f"PROBE|{p['company']}|{kind}:{'/'.join(args)}|OK total={len(jobs)} matches={len(hits)}")
            for j in hits[:6]:
                print(f"PROBEJOB|{p['company']}|{j['title']}|{j['location'][:40]}|{age_label(j['posted']) if j.get('posted') else 'no date'}|{j.get('url','')}")
    return 0

def selftest():
    n = Notifier()
    ok = n.push("TEST: watcher connected", "If you see this, alerts reach your phone.\nSoftware Test Role\nRemote, US\nPay: $70,000-$80,000", "https://example.com", 4)
    print("selftest sent:", ok)
    return 0 if ok or not (n.topic or n.tg_token) else 1

if __name__ == "__main__":
    if "--probe" in sys.argv:
        sys.exit(probe())
    if "--qualify" in sys.argv:
        sys.exit(qualify())
    if "--detail" in sys.argv:
        sys.exit(detail_audit())
    if "--audit" in sys.argv:
        sys.exit(audit())
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    run(dry="--dry" in sys.argv)
