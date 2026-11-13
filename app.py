"""
FastAPI wrapper around tracker.py.
POST /run  ->  {"text": "...", "seen_json": "[...]"}
"""

import json
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

import tracker

app = FastAPI(title="TopJobs Tracker")

MAX_KEYWORDS, MAX_COMPANIES, MAX_EXCLUDES = 10, 5, 10
MAX_SEEN = 500                    # newest URLs kept in the seen list
RATE_LIMIT, RATE_WINDOW = 6, 60   # 6 requests per 60 s per IP

_hits = defaultdict(deque)


class RunRequest(BaseModel):
    keywords: str = ""      # comma-separated, e.g. "data, analyst"
    companies: str = ""     # comma-separated
    exclude: str = ""       # comma-separated
    days: int = Field(default=7, ge=1, le=30)
    seen: str = "[]"        # JSON list of URLs from the previous run


def _split(s):
    return [x.strip() for x in s.split(",") if x.strip()]


def _client_ip(request: Request):
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else request.client.host


def _rate_limit(ip):
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(429, "Too many requests, wait a minute and retry")
    q.append(now)


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/run")
def run(req: RunRequest, request: Request):
    _rate_limit(_client_ip(request))

    keywords, companies, exclude = _split(req.keywords), _split(req.companies), _split(req.exclude)
    if not keywords and not companies:
        raise HTTPException(400, "Add at least one keyword or company")
    if len(keywords) > MAX_KEYWORDS or len(companies) > MAX_COMPANIES or len(exclude) > MAX_EXCLUDES:
        raise HTTPException(
            400,
            f"Too many terms (max {MAX_KEYWORDS} keywords, "
            f"{MAX_COMPANIES} companies, {MAX_EXCLUDES} excludes)",
        )

    try:
        old_seen = [u for u in json.loads(req.seen or "[]") if isinstance(u, str)]
    except (ValueError, TypeError):
        old_seen = []
    seen_set = set(old_seen)

    listings, failed = tracker.fetch_job_listings(keywords + companies)
    if failed:
        # Never return partial results: the shortcut would overwrite its seen file.
        raise HTTPException(502, f"topjobs.lk search failed for: {', '.join(failed)}")

    jobs = tracker.filter_jobs(listings, keywords, companies, exclude, req.days)
    for j in jobs:
        j["status"] = "NEW" if j["url"] not in seen_set else "Seen before"
    jobs.sort(key=lambda j: j["status"] != "NEW")

    new_seen = list(dict.fromkeys(old_seen + [j["url"] for j in jobs]))[-MAX_SEEN:]

    return {
        "text": tracker.format_text(jobs),
        "seen_json": json.dumps(new_seen),
    }