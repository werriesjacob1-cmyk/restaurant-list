#!/usr/bin/env python3
"""Free/public employer discovery and location triage for Deep Search V2.

Does not touch the existing board-watch process or send notifications.
The only metered calls here are Brave Web Search; GitHub workflow reserves
the budget BEFORE passing a granted call count to this module.
"""
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import watch

# Rotated over successive dates; three searches per scheduled report.
# All results must be independently confirmed on an employer ATS feed.
SEARCH_QUERIES = [
    'site:jobs.ashbyhq.com "customer success manager" "remote" "US"',
    'site:job-boards.greenhouse.io "account manager" "remote" "United States"',
    'site:jobs.lever.co "renewals" "remote" "United States"',
    'site:ats.rippling.com "customer success" "remote" "USA"',
    'site:jobs.personio.com/job/ "customer success manager" "US" "remote"',
    'site:breezy.hr/p/ "account manager" "remote" "United States"',
    'site:recruitee.com/o/ "client success" "United States"',
    'site:job-boards.greenhouse.io "inbound sales" "remote"',
    'site:jobs.ashbyhq.com "associate account manager" "remote"',
    'site:jobs.lever.co "client success" "remote" "US"',
    'site:ats.rippling.com "partner success" "remote"',
    'site:jobs.personio.com/job/ "account manager" "remote" "USA"',
    'site:breezy.hr/p/ "customer success" "remote"',
    'site:recruitee.com/o/ "account executive" "remote" "United States"',
    'site:jobs.ashbyhq.com "renewal specialist" "remote"',
    'site:job-boards.greenhouse.io "inside sales" "remote" "US"',
    'site:jobs.lever.co "account executive" "inbound" "remote"',
    'site:ats.rippling.com "client success manager" "remote"',
    'site:jobs.personio.com/job/ "partner success" "remote" "US"',
    'site:breezy.hr/p/ "client services" "remote" "US"',
    'site:recruitee.com/o/ "renewals" "remote"',
    'site:job-boards.greenhouse.io "Nashville" "account manager"',
    'site:jobs.ashbyhq.com "Nashville" "customer success"',
    'site:jobs.lever.co "Nashville" "account manager"',
    'site:ats.rippling.com "Nashville" "client success"',
    'site:breezy.hr/p/ "Nashville" "inside sales"',
    'site:jobs.personio.com/job/ "Central time" "customer success"',
    'site:job-boards.greenhouse.io "automotive" "account manager" "remote"',
    'site:jobs.ashbyhq.com "retention" "customer success" "remote"',
    'site:jobs.lever.co "account management" "renewals" "remote"',
]
KINDS = {"greenhouse", "ashby", "lever", "rippling", "breezy", "personio", "recruitee"}
# Public recruiting aggregators can run their own ATS boards. Those are NOT
# employer-origin postings and must never be counted as independently verified.
INTERMEDIARY_SLUGS = {"jobgether", "swooped", "jobot", "talentify"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,70}$", re.I)
REMOTE = re.compile(r"\b(remote|work.from.home|wfh|telecommut|virtual|distributed)\b", re.I)
US = re.compile(r"\b(?:united states|united states of america|usa|u\.s\.|us[- ]remote|remote[- ]us|north america|americas)\b", re.I)
TN = re.compile(r"\btennessee\b|(?<![A-Za-z])TN(?![A-Za-z])", re.I)
CITY_METRO = re.compile(r"\b(nashville|madison|goodlettsville|hendersonville|hermitage|donelson|antioch|brentwood|franklin|gallatin|mount juliet|mt\. juliet|smyrna|murfreesboro|lebanon)\b", re.I)
CITY_TOO_FAR = re.compile(r"\b(memphis|knoxville|chattanooga|clarksville|jackson|cookeville|johnson city|bristol|columbia)\b", re.I)
STATE_CODES = re.compile(r"(?<![A-Za-z])(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TX|UT|VT|VA|WA|WV|WI|WY)(?![A-Za-z])")
STATE_NAMES = watch.STATE_NAME_RE
FOREIGN = re.compile(r"\b(?:united kingdom|london|germany|france|india|canada|mexico|australia|brazil|philippines|spain|ireland|singapore|netherlands|europe|emea|apac|latam)\b", re.I)
EXCLUSION = re.compile(r"(?:excluding|except|not (?:available|hiring|eligible|open) (?:in|to)|cannot hire in|do not hire in|not in)\s*(?:the\s+)?(?:state of\s+)?(?:tennessee|TN\b)", re.I)

def parse_ats_url(url):
    """Only known public ATS hosts; URL never becomes an arbitrary network target."""
    try:
        u = urllib.parse.urlparse(url)
        host = (u.hostname or "").lower()
        if u.scheme != "https":
            return None
        parts = [p for p in u.path.split("/") if p]
        if host in ("job-boards.greenhouse.io", "boards.greenhouse.io") and parts:
            kind, slug = "greenhouse", parts[0]
        elif host == "jobs.ashbyhq.com" and parts:
            kind, slug = "ashby", parts[0]
        elif host == "jobs.lever.co" and parts:
            kind, slug = "lever", parts[0]
        elif host == "ats.rippling.com" and parts:
            kind, slug = "rippling", parts[0]
        elif host.endswith(".breezy.hr") and len(host.split(".")) == 3:
            kind, slug = "breezy", host.split(".")[0]
        elif re.fullmatch(r"[a-z0-9-]+\.jobs\.personio\.(?:com|de)", host):
            kind, slug = "personio", host.split(".")[0]
        elif re.fullmatch(r"[a-z0-9-]+\.recruitee\.com", host):
            kind, slug = "recruitee", host.split(".")[0]
        else:
            return None
        if not SLUG_RE.fullmatch(slug) or slug.lower() in ("jobs", "embed") | INTERMEDIARY_SLUGS:
            return None
        return (kind, slug.lower())
    except (TypeError, ValueError):
        return None

def select_queries(day, calls=3):
    """Walk entire query catalog over days; no query is silently skipped forever."""
    limit = min(max(int(calls), 0), 3)
    offset = ((day.toordinal() - 1) * 3) % len(SEARCH_QUERIES)
    return [SEARCH_QUERIES[(offset + n) % len(SEARCH_QUERIES)] for n in range(limit)]

def discover(known, now, granted_calls, key):
    """Do no more than the PRE-RESERVED calls. No retries on paid endpoints."""
    out, audit = [], []
    if not key or granted_calls <= 0:
        return out, audit
    day = datetime.fromtimestamp(now, timezone.utc).date()
    for query in select_queries(day, granted_calls):
        params = {"q": query, "count": 20, "country": "US", "search_lang": "en"}
        # Do not restrict all queries to recent indexing: older live pages can matter.
        if day.day % 2 == 0:
            params["freshness"] = "pm"
        try:
            answer = watch.get("https://api.search.brave.com/res/v1/web/search?" +
                               urllib.parse.urlencode(params),
                               headers={"X-Subscription-Token": key, "Accept": "application/json"},
                               timeout=18)
            hits = (answer.get("web") or {}).get("results") or []
            found = 0
            for hit in hits:
                item = parse_ats_url(hit.get("url", ""))
                if not item or item in known:
                    continue
                known.add(item)
                kind, slug = item
                out.append((kind, [slug, slug]))
                found += 1
            audit.append({"query": query, "results": len(hits), "candidate_boards": found, "status": "ok"})
        except Exception as exc:
            message = str(exc)
            audit.append({"query": query, "results": 0, "candidate_boards": 0,
                          "status": "rate-limited" if "429" in message else "error"})
            if "429" in message or "401" in message or "403" in message:
                break
    return out, audit

def _text(e, name):
    v = e.find(name)
    return "".join(v.itertext()).strip() if v is not None else ""

def personio(name, slug):
    root = ET.fromstring(watch.http(f"https://{slug}.jobs.personio.com/xml?language=en"))
    if root.tag != "workzag-jobs":
        raise ValueError("unexpected Personio feed")
    jobs = []
    for p in root.findall("position"):
        job_id = _text(p, "id")
        title = _text(p, "name")
        if not job_id.isdigit() or not title:
            continue
        details = []
        for e in p.findall(".//jobDescription"):
            details.append(_text(e, "name") + ": " + watch.strip_html(_text(e, "value")))
        loc = ", ".join(filter(None, [_text(p, "office"), _text(p, "subcompany")]))
        # The title often carries "Remote - US" more accurately than office labels.
        if REMOTE.search(title) and "remote" not in loc.lower():
            loc += " (Remote)"
        jobs.append(watch._job(id=job_id, company=name, title=title, location=loc.strip(", "),
                               url=f"https://{slug}.jobs.personio.com/job/{job_id}?language=en",
                               desc=" ".join(details), posted=watch.parse_ts(_text(p, "createdAt"))))
    return jobs

def breezy(name, slug):
    data = watch.get(f"https://{slug}.breezy.hr/json")
    entries = data if isinstance(data, list) else data.get("positions", [])
    if not isinstance(entries, list):
        raise ValueError("unexpected Breezy feed")
    jobs = []
    for p in entries:
        title = p.get("name") or p.get("title") or ""
        job_id = str(p.get("id") or p.get("_id") or "")
        if not job_id or not title:
            continue
        location = p.get("location") or {}
        if isinstance(location, dict):
            loc = ", ".join(str(location.get(k) or "") for k in ("city", "region", "country") if location.get(k))
        else:
            loc = str(location)
        if p.get("remote") or p.get("remote_type"):
            loc += " (Remote)"
        url = p.get("url") or p.get("link") or ""
        # Never invent a posting URL from the title; skip unverifiable records.
        if not str(url).startswith(f"https://{slug}.breezy.hr/"):
            job_slug = p.get("slug") or p.get("friendly_id") or ""
            if not re.fullmatch(r"[a-z0-9_-]+", str(job_slug), re.I):
                continue
            url = f"https://{slug}.breezy.hr/p/{job_id}-{job_slug}"
        jobs.append(watch._job(id=job_id, company=name, title=title, location=loc,
                               url=url, posted=watch.parse_ts(p.get("published_date") or p.get("created_at")),
                               pay=str(p.get("salary") or ""), detail=lambda u=url: watch.strip_html(watch.http(u,timeout=15))))
    return jobs

def recruitee(name, slug):
    # Public XML feed. Per Recruitee docs API JSON tokens become mandatory in 2027.
    root = ET.fromstring(watch.http(f"https://{slug}.recruitee.com/api/feeds/offers.xml"))
    jobs = []
    for offer in root.findall(".//offer"):
        title = _text(offer, "title")
        link = _text(offer, "url") or _text(offer, "careers_url")
        oid = _text(offer, "id")
        offer_slug = _text(offer, "slug")
        if not link and offer_slug and SLUG_RE.fullmatch(offer_slug):
            link = f"https://{slug}.recruitee.com/o/{offer_slug}"
        if not title or not link.startswith(f"https://{slug}.recruitee.com/"):
            continue
        loc = _text(offer, "location") or _text(offer, "city")
        detail = watch.strip_html(_text(offer, "description") + " " + _text(offer, "requirements"))
        jobs.append(watch._job(id=oid or offer_slug, company=name, title=title,
                               location=loc, url=link, desc=detail))
    return jobs

def fetch_workday_deep(name, tenant, wd, site, page_cap=6, searches=None):
    """All available pages per query, not the legacy 40-result limit."""
    queries = searches or ["customer success", "account manager", "renewal", "client",
                           "partner success", "inside sales", "sales", "customer experience"]
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    out, seen = [], set()
    for q in list(dict.fromkeys(queries)):
        for page in range(page_cap):
            d = watch.get(f"{base}/wday/cxs/{tenant}/{site}/jobs",
                          data={"appliedFacets":{},"limit":20,"offset":page*20,"searchText":q})
            posts = d.get("jobPostings", [])
            if not posts:
                break
            new = 0
            for p in posts:
                path = p.get("externalPath","")
                if not path or path in seen:
                    continue
                seen.add(path); new += 1
                out.append(watch._job(id=path,company=name,title=p.get("title",""),
                    location=p.get("locationsText",""),url=f"{base}/en-US/{site}{path}",
                    detail=lambda b=base,t=tenant,s=site,pa=path: watch.strip_html(
                        (watch.get(f"{b}/wday/cxs/{t}/{s}{pa}").get("jobPostingInfo") or {}).get("jobDescription",""))))
            if len(posts)<20 or new==0:
                break
    return out

def fetch_board(kind, spec):
    name = spec[0]
    if kind == "workday":
        return fetch_workday_deep(name,*spec[1:])
    if kind == "breezy":
        return breezy(name,spec[1])
    if kind == "personio":
        return personio(name,spec[1])
    if kind == "recruitee":
        return recruitee(name,spec[1])
    return watch.ADAPTERS[kind][0](name,*spec[1:])

def location_triage(location, description=""):
    """Return PASS, VERIFY, or REJECT, and reason. VERIFY stays in report."""
    raw = str(location or "").strip()
    desc = description or ""
    if EXCLUSION.search(raw) or EXCLUSION.search(desc[:1800]):
        return "REJECT", "Tennessee explicitly excluded"
    if not raw:
        return "VERIFY", "location not declared"
    remote = bool(REMOTE.search(raw))
    if remote:
        if TN.search(raw):
            return "PASS", "remote with Tennessee listed"
        if re.search(r"\b(?:Eastern|Central)\s*(?:Time|US Time|timezone)\b", raw,re.I) and not (STATE_CODES.search(raw) or STATE_NAMES.search(raw)):
            return "VERIFY", "regional remote eligibility requires confirmation"
        codes = STATE_CODES.findall(raw)
        names = STATE_NAMES.findall(raw.lower())
        # If named remote state restrictions exist and Tennessee is absent, reject.
        # However "US remote excluding California" is nationwide with exclusions.
        if (codes or names) and not re.search(r"\bexcept\b|\bexcluding\b",raw,re.I):
            return "REJECT", "remote limited to other state(s)"
        if US.search(raw) or re.fullmatch(r"\s*(remote|anywhere|work from home|virtual)\s*",raw,re.I):
            return "PASS", "nationwide remote"
        if FOREIGN.search(raw) and not US.search(raw):
            return "REJECT", "foreign-only remote"
        return "VERIFY", "remote region unclear"
    if CITY_TOO_FAR.search(raw) and TN.search(raw):
        return "REJECT", "outside Nashville metro"
    if CITY_METRO.search(raw):
        return "VERIFY", "onsite/hybrid commute needs verification"
    if TN.search(raw):
        return "VERIFY", "Tennessee location, actual commute unknown"
    if US.search(raw) and not (STATE_CODES.search(raw) or STATE_NAMES.search(raw)):
        return "VERIFY", "US location without remote designation"
    return "REJECT", "nonlocal, not clearly nationwide remote"
