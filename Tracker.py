"""
topjobs.lk scraping + filtering logic (no files, no globals).
Used by app.py, but can also be imported and used on its own.
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.topjobs.lk/applicant/vacancybyfunctionalarea.jsp"
TZ = ZoneInfo("Asia/Colombo")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

ONCLICK_RE = re.compile(
    r"createAlert\('([^']*)','([^']*)','([^']*)','([^']*)','([^']*)'\)"
)

# Cache each search term for 10 minutes so many users with the same
# keywords don't hammer topjobs.lk.
CACHE_TTL = 600
_cache = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------

def _search_params(term):
    return {
        "FA": "", "jst": "OPEN", "sQut": "", "txtKeyWord": term,
        "chkGovt": "", "chkParttime": "", "chkWalkin": "",
        "chkNGO": "", "chkForeign": "", "chkInternship": "",
    }


def _fetch_page(session, term, page_no):
    params = _search_params(term)
    if page_no > 1:
        params["pageNo"] = str(page_no)
    resp = session.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def parse_job_rows(html):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for row in soup.select('tr[onclick^="createAlert"]'):
        m = ONCLICK_RE.search(row.get("onclick", ""))
        if not m:
            continue
        _rid, ac, jc, ec, _sid = m.groups()

        title_tag = row.find("h2")
        company_tag = row.find("h1")
        if not title_tag or not company_tag:
            continue

        tds = row.find_all("td")
        date = tds[4].get_text(strip=True) if len(tds) > 4 else ""
        date = date or datetime.now(TZ).strftime("%a %b %d %Y")

        jobs.append({
            "date": date,
            "company": company_tag.get_text(strip=True),
            "position": title_tag.get_text(strip=True),
            "url": (
                "https://www.topjobs.lk/employer/JobAdvertismentServlet"
                f"?ac={ac}&jc={jc}&ec={ec}&pg=applicant/vacancybyfunctionalarea.jsp"
            ),
            "job_code": jc,
        })
    return jobs, soup


def _fetch_all_pages(term, max_pages=5):
    """Returns all jobs for one search term. Raises on network errors."""
    session = requests.Session()
    collected = {}
    page = 1
    while page <= max_pages:
        html = _fetch_page(session, term, page)
        jobs, soup = parse_job_rows(html)
        for job in jobs:
            collected[job["job_code"]] = job
        if not jobs:
            break
        try:
            total = int(soup.find("input", {"name": "hdnTotalJobs"}).get("value", "0"))
            block = int(soup.find("input", {"name": "hdnBlockSize"}).get("value", "1000"))
        except (AttributeError, ValueError):
            break
        if page * block >= total:
            break
        page += 1
    return list(collected.values())


def _fetch_term_cached(term):
    key = term.lower()
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    jobs = _fetch_all_pages(term)
    with _cache_lock:
        _cache[key] = (now, jobs)
    return jobs


def fetch_job_listings(terms):
    """Searches every term in parallel. Returns (jobs, failed_terms)."""
    terms = list(dict.fromkeys(t for t in terms if t))
    all_jobs, failed = {}, []
    if not terms:
        return [], failed

    with ThreadPoolExecutor(max_workers=min(8, len(terms))) as pool:
        futures = {pool.submit(_fetch_term_cached, t): t for t in terms}
        for fut in as_completed(futures):
            term = futures[fut]
            try:
                for job in fut.result():
                    all_jobs[job["job_code"]] = job
            except Exception:
                failed.append(term)
    return list(all_jobs.values()), failed


# ---------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------

def _parse_date(text):
    try:
        return datetime.strptime(text.strip(), "%a %b %d %Y").date()
    except (ValueError, AttributeError):
        return None


def _is_recent(text, days):
    d = _parse_date(text)
    if d is None:
        return True  # keep unparseable dates rather than hide them
    return 0 <= (datetime.now(TZ).date() - d).days <= days


def filter_jobs(jobs, keywords, companies, exclude, days):
    kws = [k.lower() for k in keywords]
    cos = [c.lower() for c in companies]
    exc = [e.lower() for e in exclude]
    out = []
    for job in jobs:
        title = job["position"].lower()
        if any(e in title for e in exc):
            continue
        if not _is_recent(job["date"], days):
            continue
        if any(k in title for k in kws) or any(c in job["company"].lower() for c in cos):
            out.append(job)
    return out


# ---------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------

def format_text(jobs):
    new_count = sum(1 for j in jobs if j["status"] == "NEW")
    lines = [
        "TOPJOBS.LK TRACKER",
        f"Run at: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}",
        f"Total: {len(jobs)} | New: {new_count}",
        "",
    ]
    if not jobs:
        lines.append("No matching jobs found.")
    for j in jobs:
        tag = "*** NEW ***" if j["status"] == "NEW" else "seen before"
        lines += [
            f"[{tag}]",
            f"Date: {j['date']}",
            f"Company: {j['company']}",
            f"Position: {j['position']}",
            f"URL: {j['url']}",
            "-" * 30,
        ]
    return "\n".join(lines)