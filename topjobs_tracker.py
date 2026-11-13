"""
topjobs.lk Job Posting Tracker
================================
Searches topjobs.lk for each of your KEYWORDS and COMPANIES, filters out
anything matching EXCLUDE_KEYWORDS, tracks which postings are NEW since
the last run, writes everything to a text file, and opens that file.

HOW TO USE
----------
1. Edit the CONFIG section below.
2. Run:  python topjobs_tracker.py
3. topjobs_results.txt is created/updated and opened automatically.
4. Run it again later (daily, etc.) - it remembers what it saw last time
   (topjobs_seen.json) and marks new postings as NEW.
"""

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# =====================================================================
# CONFIG - edit these
# =====================================================================

# Keywords to search for (checked against the job title, case-insensitive,
# partial match). Each keyword is also sent to topjobs.lk's own search
# to pre-filter results, then checked again locally for accuracy.
KEYWORDS = [
    "data",
    "analyst",
    "MIS",
    "QA",
    "Quality Assurance"
]

# Companies you always want to see, regardless of keyword match.
# (leave empty list [] if you don't want to filter by company)
COMPANIES = [
    "MAS Holdings",
    "Brandix Lanka Limited",
]

# If any of these phrases appear in the title, the job is SKIPPED
# even if it matched a keyword above. This is how "data" won't match
# "data entry".
EXCLUDE_KEYWORDS = [
    "data entry",
]

# Only keep postings dated within this many days (based on the "Opening
# Date" topjobs.lk shows for each listing).
RECENCY_DAYS = 7

BASE_URL = "https://www.topjobs.lk/applicant/vacancybyfunctionalarea.jsp"

# Where output goes
OUTPUT_FILE = "topjobs_results.txt"
SEEN_FILE = "topjobs_seen.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

ONCLICK_RE = re.compile(
    r"createAlert\('([^']*)','([^']*)','([^']*)','([^']*)','([^']*)'\)"
)


# =====================================================================
# SCRAPING
# =====================================================================

def search_params(keyword):
    """Query params matching topjobs.lk's own search form (GET works)."""
    return {
        "FA": "",
        "jst": "OPEN",
        "sQut": "",
        "txtKeyWord": keyword,
        "chkGovt": "",
        "chkParttime": "",
        "chkWalkin": "",
        "chkNGO": "",
        "chkForeign": "",
        "chkInternship": "",
    }


def fetch_search_page(session, keyword, page_no=1):
    params = search_params(keyword)
    if page_no > 1:
        params["pageNo"] = str(page_no)
    try:
        resp = session.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        print(f"[ERROR] Could not fetch results for '{keyword}' "
              f"(page {page_no}): {e}")
        return None


def parse_job_rows(html):
    """Parses one results page into a list of job dicts + returns the soup
    (soup is returned too so pagination info can be read from hidden inputs)."""
    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    for row in soup.select('tr[onclick^="createAlert"]'):
        onclick = row.get("onclick", "")
        m = ONCLICK_RE.search(onclick)
        if not m:
            continue
        _rid, ac, jc, ec, _sid = m.groups()

        title_tag = row.find("h2")
        company_tag = row.find("h1")
        if not title_tag or not company_tag:
            continue

        position = title_tag.get_text(strip=True)
        company = company_tag.get_text(strip=True)

        tds = row.find_all("td")
        opening_date = tds[4].get_text(strip=True) if len(tds) > 4 else ""
        opening_date = opening_date or datetime.now().strftime("%Y-%m-%d")

        url = (
            "https://www.topjobs.lk/employer/JobAdvertismentServlet"
            f"?ac={ac}&jc={jc}&ec={ec}&pg=applicant/vacancybyfunctionalarea.jsp"
        )

        jobs.append(
            {
                "date": opening_date,
                "company": company,
                "position": position,
                "url": url,
                "job_code": jc,
            }
        )

    return jobs, soup


def fetch_all_pages(session, keyword, max_pages=5):
    """Fetches (and paginates through, if needed) all results for one
    search term. Most single-keyword searches fit on page 1."""
    collected = {}
    page = 1
    while page <= max_pages:
        html = fetch_search_page(session, keyword, page)
        if html is None:
            break
        jobs, soup = parse_job_rows(html)
        for job in jobs:
            collected[job["job_code"]] = job
        if not jobs:
            break

        total_tag = soup.find("input", {"name": "hdnTotalJobs"})
        block_tag = soup.find("input", {"name": "hdnBlockSize"})
        try:
            total = int(total_tag.get("value", "0"))
            block = int(block_tag.get("value", "1000"))
        except (AttributeError, ValueError):
            break
        if page * block >= total:
            break
        page += 1

    return list(collected.values())


def fetch_job_listings():
    """Runs a topjobs.lk search for every keyword and company IN PARALLEL,
    then merges and de-duplicates the results by job code."""
    session = requests.Session()
    all_jobs = {}

    search_terms = list(dict.fromkeys(KEYWORDS + COMPANIES))  # de-dup, keep order

    with ThreadPoolExecutor(max_workers=min(8, len(search_terms) or 1)) as pool:
        future_to_term = {
            pool.submit(fetch_all_pages, session, term): term
            for term in search_terms
        }
        for future in as_completed(future_to_term):
            term = future_to_term[future]
            try:
                jobs = future.result()
            except Exception as e:
                print(f"[ERROR] search '{term}' failed: {e}")
                continue
            print(f"  '{term}' -> {len(jobs)} result(s)")
            for job in jobs:
                all_jobs[job["job_code"]] = job

    return list(all_jobs.values())


# =====================================================================
# FILTERING
# =====================================================================

def title_has_excluded_word(title):
    title_lower = title.lower()
    return any(ex.lower() in title_lower for ex in EXCLUDE_KEYWORDS)


def matches_keywords(title):
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in KEYWORDS)


def matches_company(company):
    if not COMPANIES:
        return False
    return any(c.lower() in company.lower() for c in COMPANIES)


def parse_job_date(date_text):
    """Parses topjobs.lk's 'Fri Aug 21 2026' style date. Returns a date
    object, or None if it can't be parsed."""
    try:
        return datetime.strptime(date_text.strip(), "%a %b %d %Y").date()
    except (ValueError, AttributeError):
        return None


def is_recent(date_text):
    """True if the posting's date is within RECENCY_DAYS of today.
    Postings with an unparseable date are kept (better to show them than
    silently drop them)."""
    job_date = parse_job_date(date_text)
    if job_date is None:
        return True
    age_days = (datetime.now().date() - job_date).days
    return 0 <= age_days <= RECENCY_DAYS


def filter_jobs(jobs):
    """A job is kept if it matches a keyword OR a tracked company, is not
    excluded, AND is within RECENCY_DAYS."""
    filtered = []
    for job in jobs:
        if title_has_excluded_word(job["position"]):
            continue
        if not is_recent(job["date"]):
            continue
        if matches_keywords(job["position"]) or matches_company(job["company"]):
            filtered.append(job)
    return filtered


# =====================================================================
# "NEW" TRACKING (persisted across runs)
# =====================================================================

def load_seen_urls():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_seen_urls(urls):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, indent=2)


def mark_new_jobs(jobs, seen_urls):
    for job in jobs:
        job["status"] = "NEW" if job["url"] not in seen_urls else "Seen before"
    return jobs


# =====================================================================
# OUTPUT
# =====================================================================

def write_results(jobs):
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_count = sum(1 for j in jobs if j["status"] == "NEW")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("TOPJOBS.LK POSTING TRACKER\n")
        f.write(f"Run at: {run_time}\n")
        f.write(f"Total matching jobs: {len(jobs)} | New this run: {new_count}\n")
        f.write("=" * 90 + "\n\n")

        if not jobs:
            f.write("No matching jobs found.\n")
            return

        for job in sorted(jobs, key=lambda j: j["status"] != "NEW"):
            tag = "*** NEW ***" if job["status"] == "NEW" else "seen before"
            f.write(f"[{tag}]\n")
            f.write(f"Date:     {job['date']}\n")
            f.write(f"Company:  {job['company']}\n")
            f.write(f"Position: {job['position']}\n")
            f.write(f"URL:      {job['url']}\n")
            f.write("-" * 90 + "\n")


def open_file(path):
    abs_path = os.path.abspath(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(abs_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.call(["open", abs_path])
        else:
            subprocess.call(["xdg-open", abs_path])
    except Exception as e:
        print(f"[INFO] Could not auto-open the file ({e}). "
              f"Open it manually at: {abs_path}")


# =====================================================================
# MAIN
# =====================================================================

def main():
    print("Fetching listings from topjobs.lk ...")
    all_jobs = fetch_job_listings()
    print(f"Fetched {len(all_jobs)} unique listings across all search terms.")

    matching_jobs = filter_jobs(all_jobs)
    print(f"{len(matching_jobs)} listings matched after applying "
          f"keyword/exclude/company rules.")

    seen_urls = load_seen_urls()
    matching_jobs = mark_new_jobs(matching_jobs, seen_urls)

    write_results(matching_jobs)

    seen_urls.update(job["url"] for job in matching_jobs)
    save_seen_urls(seen_urls)

    print(f"Results written to {OUTPUT_FILE}")
    open_file(OUTPUT_FILE)


if __name__ == "__main__":
    main()
