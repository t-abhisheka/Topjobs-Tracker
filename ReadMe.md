# TopJobs Tracker

Searches [topjobs.lk](https://www.topjobs.lk) for your keywords and companies, hides anything you exclude, flags postings that are **new since your last run**, and drops the result into the iOS Notes app with one tap.

It is a small FastAPI server (`app.py` + `tracker.py`) that an iOS Shortcut calls.

## Run your own copy (free, ~5 minutes)

1. Fork this repo.
2. On [render.com](https://render.com): **New → Web Service**, connect your fork.
3. Settings:
   - Instance type: **Free**
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Deploy. Your URL will look like `https://your-app.onrender.com`.
5. Open the URL. You should see `{"status":"ok"}`.

> Render's free tier sleeps after 15 minutes idle, so the first run can take 30-60 seconds.

## API

`POST /run` with JSON:

| Field | Type | Example |
|---|---|---|
| `keywords` | text, comma-separated | `data, analyst, QA` |
| `companies` | text, comma-separated | `MAS Holdings, Brandix` |
| `exclude` | text, comma-separated | `data entry` |
| `days` | number (1-30) | `7` |
| `seen` | text (JSON list from the previous run) | `[]` |

Returns `{"text": "...", "seen_json": "[...]"}`.

Limits: 10 keywords, 5 companies, 10 excludes, 6 requests/minute per IP.

## Notes

- The server stores nothing. The list of already-seen jobs is kept on your phone in `Shortcuts/seen.json` and sent with each request.
- Search results are cached for 10 minutes to go easy on topjobs.lk.
- Be respectful: this scrapes a public site. Don't run it in a tight loop.