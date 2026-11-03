# finance-internship-engine

[![CI](https://github.com/sling54/finance-internship-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/sling54/finance-internship-engine/actions/workflows/ci.yml)

An automated search pipeline for finance internships. Every day it collects postings from firm
career pages, curated internship lists, job boards and job-alert emails, merges duplicates, and
filters them down to the roles worth applying to, with a logged reason for every role it drops.

I built it to run my own search for trading, quant and derivatives internships. Doing that search
by hand meant checking a dozen sites a day and rereading the same postings.

**Sample run (Nov 2, 2026), lists only:** 4,321 postings collected from SimplifyJobs and Kadoa →
156 kept after filtering and de-duplication (22 of the kept roles appeared in both lists and were merged).

## How it works

```mermaid
flowchart TD
    subgraph Collect
        direction LR
        A[Firm career pages<br/>Greenhouse · Lever · Ashby]
        B[Internship lists<br/>SimplifyJobs · Kadoa]
        C[Job boards via JobSpy<br/>LinkedIn · Indeed · ZipRecruiter]
        D[Alert emails in Gmail<br/>LinkedIn · Indeed · Handshake · eFinancialCareers]
    end
    A & B & C & D --> N[Normalize to one Posting shape]
    N --> M[De-duplicate across sources]
    M --> T[Title: internship + track]
    T --> E[Eligibility: degree + graduation window]
    E --> S[Season: detect, drop past terms]
    S --> L[Location: US-wide or 1-hour zone]
    L --> P[Already applied? check tracker]
    P --> F[AI fit score 1–5<br/>local model in LM Studio]
    F --> O[(jobs_latest.csv<br/>rejected_DATE.csv)]
```

| # | Source | How it's read | Module |
|---|--------|---------------|--------|
| 1 | Firm boards on Greenhouse, Lever, Ashby (DV, Flow Traders, IMC, Jane Street, Five Rings, Point72 and others) | Public job APIs, one call per firm | `scanners/ats.py` |
| 2 | Firms on Workday and other portals (AQR, SIG, Citadel, Group One) | Covered through Kadoa's dataset | `scanners/lists.py` |
| 3 | [SimplifyJobs](https://github.com/SimplifyJobs/Summer2027-Internships) internship list | JSON file on GitHub | `scanners/lists.py` |
| 4 | [Kadoa quant-jobs](https://github.com/kadoa-org/quant-jobs) | JSON file on GitHub (about 4,300 quant roles, internships kept) | `scanners/lists.py` |
| 5 | LinkedIn, Indeed, ZipRecruiter | [JobSpy](https://github.com/speedyapply/JobSpy), public listings, logged out | `scanners/boards.py` |
| 6 | Job-alert emails | Gmail API reads one label; job links parsed from the HTML | `scanners/alerts.py` |

The engine never logs into a job site. Firm board tokens in `config/firms.yml` were taken from
live posting URLs, not guessed.

## The filters

Each filter either passes a posting or raises `Rejected("reason")`, so every dropped role is
written to `rejected_DATE.csv` with its reason.

| Filter | Keeps | Drops |
|--------|-------|-------|
| **Title** (`titles.yml`) | Internships matching a track: Trading, Quant / derivatives, Buy-side, Data, SWE, Wealth | Investment banking, M&A, audit, PhD- or master's-only titles, full-time roles |
| **Eligibility** | Bachelor's-eligible roles asking for graduates in the profile's window (or naming no date) | `PhD only`, `Class of 2026`, `graduating Spring 2027` |
| **Season** | Upcoming terms, read from term lists, titles ("2027 Summer Internship", "January Start") and descriptions ("15–20 hours per week") | Terms already started, terms starting after graduation |
| **Location** | Summer roles anywhere in the US; fall, winter, spring, part-time and all Data/SWE roles within a 1-hour drive of campus, or remote | Non-US roles, far-away semester roles, "Remote in UK" |
| **Already applied** | Everything else | Roles already in the application tracker spreadsheet, matched despite wording differences |

Roles the engine can't judge are kept but flagged for review: `check season`, `check location`,
`check drive time`, `check division`. New roles since the last run are flagged `new`.

## AI fit score

After filtering, a local model running in [LM Studio](https://lmstudio.ai) reads each kept role's
description next to my profile and resume and scores it 1–5 with a one-line reason
(`fit_score`, `fit_reason` columns). It also settles the flagged roles, such as a plain
"Summer Analyst" whose division only the description reveals. Nothing leaves the laptop.

- **What it looks for** is plain English in `config/scoring.yml` (`what_i_want` and `rubric`).
- **Cached:** scores are saved in `data/scores.json`, so a daily run only scores new roles.
  Editing `scoring.yml`, the profile or the resume re-scores everything automatically.
- **Never blocks a scan:** if LM Studio isn't running, the scan finishes without scores. After
  3 failed replies in a row it stops asking and keeps what it has.
- The AI never drops a role. It only scores it, so a wrong call costs a lower rank, not a lost job.

**De-duplication.** The same role often appears in several sources with different wording
("Quantitative Trading Intern - Summer 2027 (DV Equities)" vs "Quantitative Trading Intern -
DV Equities", "DV Trading" vs "DV Trading LLC"). Companies are matched with legal suffixes
removed, and titles are matched on their word sets with years and filler removed. The copy from
the most trusted source (firm board > Kadoa > Simplify > boards and emails) is kept, and the
other copies add their locations and sources.

**Drive time.** City lists in `config/sources.yml` decide most cases offline. For Florida places
not on the lists, the engine can check against a real 60-minute driving zone from
[OpenRouteService](https://openrouteservice.org) (one API call to draw it, cached afterwards).

## Run it

Requires Python 3.10+.

```bash
git clone https://github.com/sling54/finance-internship-engine.git
cd finance-internship-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
jobengine init
jobengine scan --no-ai
```

### Set up your profile with `jobengine init`

Run `jobengine init` and answer the prompts one at a time. Press Enter to
accept the displayed default. The command saves your answers to the local,
git-ignored `config/profile.yml` and creates a blank tracker at the chosen
path if one is missing. It asks before replacing a profile and keeps any
existing tracker.

You can run `jobengine scan --no-ai` without Gmail credentials or LM Studio.
After submitting an application, record it with
`jobengine applied --company "Firm" --role "Role"`.

Results land in `data/jobs_latest.csv`. Useful options:

```bash
jobengine scan --only simplify kadoa    # just these sources
jobengine scan --no-ai                  # skip the AI fit score
jobengine -v scan                       # log every source call
pip install -e ".[jobspy]"              # enable job boards (set jobspy.enabled: true)
pip install -e ".[gmail]"               # enable alert emails (set alert_emails.enabled: true)
jobengine zone                          # draw the drive-time zone (needs ORS_API_KEY in .env)
```

## Browse results in your browser

After a scan, run `jobengine results` to open the latest postings in your default
browser. Filter by track, open a posting with **Apply**, and click **I applied**
after submitting it. The role disappears from the page once it is recorded in
your tracker. If the tracker is open in Excel, close it and try again.

The page is served only on your computer. Keep the terminal open while using it,
and press Ctrl+C to stop the server. If you have no results yet, run
`jobengine scan --no-ai`.

## Removed roles

Click **Closed** on the results page when a posting has closed or you've decided
against it. The role leaves the page and is skipped by every later scan; your
tracker is not changed. Removed roles are listed in `data/removed.json` by exact
URL, with the company, title, reason, date, and who removed it (`user`,
`link_check`, or `apply_agent`). Scans list them in the rejected CSV as
`removed: <reason>`.

```sh
jobengine removed               # list removed roles and their URLs
jobengine restore <url>         # bring one back on the next scan
```

## Closed-listing check

After filtering, each scan checks that every kept role's listing is still open and gives it
a status of open, closed, or unknown. Workday, Greenhouse, Lever, Ashby and SmartRecruiters
roles are checked through those boards' public job endpoints. Any other link is fetched and
counts as closed on a 404 or 410, or when the page says something like "no longer accepting
applications".

- **Closed** roles are dropped, listed in the rejected CSV as `removed: listing closed (...)`,
  and recorded in `data/removed.json` (`jobengine restore <url>` brings one back).
- **Unknown** roles cover timeouts, 403, 429, 5xx, and sites that block automated checks.
  They are kept, and the results page marks them **Not verified**. Unknown roles posted more
  than 120 days ago are dropped; roles with no date are kept.
- Links that redirect to a careers home page are flagged so you can search for the role there.

Answers are cached for 24 hours in `data/link_check.json`, and a failed check never stops the
scan. Settings (on/off, concurrency, the gap between requests to one site, timeout, cache age,
the 120-day limit and the closed phrases) are under `link_check:` in `config/sources.yml`.

If `data/removed.json` is damaged, the scan and results page keep running: a copy is saved
as `data/removed.json.unreadable-*` and a warning explains what happened.

## Tracker that updates itself

`jobengine track` reads recent job emails from Gmail and keeps the application spreadsheet
current. Fixed rules classify each email (Confirmed, Assessment, Interview, Offer, Rejected),
it is matched to a row by company (sender domain, subject, or the body for applicant-tracking
senders only), and the row moves forward. Rows never move backwards except to Rejected.

- Five columns are added beside your own the first time: App Status, Status Date, Last Email,
  Next Action, Job Link. Existing columns, formulas and dropdowns are left alone.
- A confirmation from a firm or role that isn't in the tracker yet ("Thank you for applying to
  Gelber Group for the Discretionary Trading Internship") becomes a new row, with the firm and
  role read from the subject. Only emails that name no firm go to a Review sheet, and each run
  retries them. Firms silent for 21 days get a follow-up reminder, and 45 days a "mark Ghosted?"
  prompt.
- Many firms never confirm. Record those as you submit, so the engine still tracks them and
  never shows the role again:

```bash
jobengine applied gelber discretionary                         # a role from jobs_latest.csv
jobengine applied --company "Group One Trading" --role "Trading Analyst Intern"
```
- A backup is saved before every write, and nothing is written while Excel has the file open.

```bash
jobengine track --days 30    # first run: read the last 30 days
```

## Run it every morning (macOS)

```bash
./scripts/schedule-daily-scan.sh        # every day at 07:30 (or: ./scripts/schedule-daily-scan.sh 6 45)
./scripts/schedule-daily-scan.sh off    # stop
```

launchd runs `jobengine track` every hour and `scripts/daily-scan.sh` each morning, which scans every enabled source, writes the CSVs and a log
in `logs/`, and shows a notification with the number of new roles. If the Mac is asleep at the
scheduled time, the scan runs as soon as it wakes.

## Privacy

Personal files never reach this repo. `.gitignore` excludes `config/profile.yml`, the `data/`
folder (results, tracker, caches), spreadsheets, PDFs, `.env` and Google OAuth tokens. The
committed `profile.example.yml` holds fake data, and the tests always run against it.

## Tests

```bash
pytest -q
```

122 tests cover each source parser, each filter (including the five location cases from the
design), de-duplication, the AI scorer (with a fake LM Studio) and an end-to-end run against a
fixture tracker spreadsheet. GitHub
Actions runs lint and tests on Python 3.10 and 3.12 on every push.

## Project layout

```
config/          titles.yml, firms.yml, sources.yml, scoring.yml, profile.example.yml
src/jobengine/
  scanners/      ats.py, lists.py, boards.py, alerts.py
  filters/       titles.py, eligibility.py, season.py, location.py, dedup.py, applied.py, removed.py
  removed.py     shared list of removed roles (data/removed.json)
  link_check.py  open/closed check for kept roles, cached in data/link_check.json
  pipeline.py    collect -> dedupe -> filter -> AI score -> CSV
  scorer.py      LM Studio fit score, cached in data/scores.json
  zone.py        OpenRouteService drive-time zone
  cli.py         `jobengine scan`, `jobengine zone`
tests/           pytest suite and fixtures
```

## Roadmap

- [x] Collect from six sources, de-duplicate, filter by title, eligibility, season, location
- [x] Fit score (1–5) for each role from a local LLM, with a one-line reason
- [ ] Application packets: tailored resume and prefilled answers (submission stays manual)
- [x] Tracker sync: Gmail status emails update the application spreadsheet
- [ ] Outreach: recruiter contact drafts, sent only after approval

## License

MIT
