# finance-internship-engine

[![CI](https://github.com/sling54/finance-internship-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/sling54/finance-internship-engine/actions/workflows/ci.yml)

An automated search pipeline for finance internships. Every day it collects postings from firm
career pages, curated internship lists, job boards and job-alert emails, merges duplicates, and
filters them down to the roles worth applying to, with a logged reason for every role it drops.

I built it to run my own search for trading, quant and derivatives internships. Doing that search
by hand meant checking a dozen sites a day and rereading the same postings.

**Sample run (Nov 2, 2026), lists only:** 4,321 postings collected from SimplifyJobs and Kadoa →
156 kept after filtering and de-duplication (22 of the kept roles appeared in both lists and were merged).

After the scan it covers the rest of the search too: an application packet for each role (with
the form filled in for you to submit), a tracker spreadsheet that updates itself from Gmail, and
cold outreach drafts to recruiters that go out only after you approve them.

## Quick start (about 30 minutes)

For SMIF members and anyone else on macOS, Windows or Linux with Python 3.10+.

1. **Install** (5 min):
   ```bash
   git clone https://github.com/sling54/finance-internship-engine.git
   cd finance-internship-engine
   python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
   pip install -e ".[gmail,apply,jobspy]" && playwright install chromium
   ```
2. **Your profile from your resume** (5 min): `jobengine init --resume ~/Documents/Resume.pdf`.
   Press Enter to keep each answer it found; the last question asks what internships you want,
   in plain English.
3. **First scan** (5 min): `jobengine scan --no-ai`, then `jobengine results` to browse the
   roles it kept.
4. **Gmail** (10 min; needed for the tracker and outreach): follow [Gmail access](#gmail-access),
   then `jobengine track`. Scans and packets work without it.
5. **Check everything**: `jobengine doctor` says what works and what's left, one line each.
   Then `./scripts/schedule-daily-scan.sh` (Windows: `.\scripts\schedule-daily-scan.ps1`) to
   run it every morning.

Optional parts: a local AI model for fit scores and drafts ([AI fit score](#ai-fit-score)),
cold outreach ([Cold outreach drafts](#cold-outreach-drafts)), and your own settings for any
part ([Your own settings](#your-own-settings)).

### Commands

| Command | What it does |
|---|---|
| `jobengine init --resume PATH` | Set up your profile and tracker from your resume |
| `jobengine scan` | Collect, filter and score postings into `data/jobs_latest.csv` |
| `jobengine results` | Browse kept roles; "I applied" and "Closed" buttons |
| `jobengine packet <role> [--fill]` | Answers for the role's form, and fill it in (you submit) |
| `jobengine applied <role>` | Record an application yourself |
| `jobengine track` | Update the tracker from Gmail (statuses, replies, reminders) |
| `jobengine outreach plan / review / send` | Contacts and drafts, approve, send (max 20 a day) |
| `jobengine stats [--markdown]` | Scans, applications, response rate, outreach |
| `jobengine removed` / `restore URL` | Roles you or the engine removed, and bring one back |
| `jobengine doctor` | Check your setup |

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
| **Location** | Summer roles anywhere in the US; fall, winter, spring and part-time roles within a 1-hour drive of campus, or remote | Non-US roles, far-away semester roles, "Remote in UK" |
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

To skip most of the typing, point it at your resume:

```bash
jobengine init --resume ~/Documents/Resume.pdf
```

It reads your name, school, degree, major, minor, GPA, expected graduation and work
authorization from the resume and shows each one as the default, so you press Enter to keep it
or type a correction. Fixed rules always run, and if LM Studio is running the local model reads
the resume too. Nothing leaves your computer. The last prompt asks what internships you want in
plain English; your answer is saved in `config/profile.yml` and replaces `what_i_want` from
`config/scoring.yml` for the AI fit score.

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

## Gmail access

`jobengine track`, alert emails and outreach read and write your Gmail through Google's API,
with your own OAuth client. One person (for SMIF, one officer) can set this up once and add
everyone else as test users:

1. In [Google Cloud Console](https://console.cloud.google.com), create a project and enable the
   **Gmail API**.
2. Under **OAuth consent screen**, choose **External**, leave it in **Testing**, and add each
   member's Gmail address under **Test users** (Testing allows up to 100).
3. Under **Credentials**, create an **OAuth client ID** of type **Desktop app** and download it.
   Each member saves it as `auth/credentials.json` (git-ignored). Share it privately, not in
   the repo.
4. Run `jobengine track`. A browser opens to sign in; the token is saved in `auth/token.json`.

Step-by-step for the officer, plus a 10-minute demo script: [docs/SMIF-setup.md](docs/SMIF-setup.md).

While the app stays in Testing, Google ends the sign-in after about 7 days; when `track` asks
again, sign in again. The engine uses the `gmail.modify` scope. It reads job emails, marks
job-alert emails as read, writes and labels outreach drafts, and sends only the drafts you
approved. The only thing it ever deletes is an outreach draft you chose to skip.

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

## Application packets

```bash
jobengine packet dv commodities trading       # a role from data/jobs_latest.csv
jobengine packet --url "https://..." --company "Firm" --role "Role"
jobengine packet dv commodities trading --fill   # also fill in the form in a browser
```

`jobengine packet` writes `data/packets/<firm-role>/packet.md` with every question the form
asks and the answer to give:

- **From your profile:** name, email, phone, LinkedIn, school, degree, major, GPA, graduation
  date, resume file, work authorization and sponsorship (Yes/No from `work_authorization`).
- **Fixed rules:** "How did you hear about us" and, where offered, "decline to self-identify"
  on gender, race, veteran and disability questions.
- **AI drafts:** open questions ("Why this firm?") drafted by your local model from the posting
  and your resume, checked like outreach hooks, and marked for you to edit.
- **Left for you:** anything else, with the dropdown's options listed.

Greenhouse forms are read from Greenhouse's public API, so custom questions are included; other
boards get the standard fields plus `default_questions` from `config/apply.yml`.

`--fill` opens Chromium on the application page (sign-ins are kept in `data/browser-profile`),
types the answers in and uploads your resume. **It never clicks Submit.** You review the form,
submit it yourself, then answer "y" and the application is added to your tracker. Needs
`pip install -e ".[apply]" && playwright install chromium`.

## Cold outreach drafts

After you apply, `jobengine outreach plan` finds people at each firm with
[Hunter.io](https://hunter.io) and writes a Gmail draft to each from your own templates.
Nothing is sent.

- **Who:** tracker rows marked Applied or Confirmed that have no outreach yet, oldest first.
  Each gets 2 contacts: recruiting first, then the role's team (trader, quant, portfolio...),
  then anyone else, by Hunter's confidence. At most 3 per firm, and never the same person
  twice within 90 days.
- **What:** your template for that kind of contact, word for word, plus one sentence
  (`{hook}`) the local model writes from the posting and your resume. A sentence with a link,
  a number that isn't in the posting or resume, another firm's name or more than 30 words is
  thrown out, and the template's `fallback_hook` is used instead.
- **Where:** drafts are labelled `jobengine/outreach` in Gmail. Every contact is logged in
  `data/outreach.json` and in an Outreach sheet in your tracker.
- **Cost:** Hunter lookups are cached for 30 days and capped by
  `hunter.monthly_credit_limit` in `config/outreach.yml`.

```bash
cp config/outreach_templates.example.yml config/outreach_templates.local.yml   # then edit it
echo "HUNTER_API_KEY=your-key" >> .env
jobengine outreach plan --dry-run    # see the drafts it would write (works with the examples)
jobengine outreach plan              # write the drafts
jobengine outreach review            # approve or skip each one on a local page
jobengine outreach send              # send the approved ones
```

- **Approve:** `outreach review` opens a page on your computer showing each draft as it is in
  Gmail now, with a link to edit it there. Approve marks it to send; Skip deletes the draft.
- **Send:** only approved drafts are ever sent, as they are in Gmail at that moment, so your
  edits go out. At most `daily_send_limit` (20) in any 24 hours, with a random 45–120 second
  pause between sends. The morning run sends whatever you approved; the rest wait a day.
- **Replies:** `jobengine track` reads each sent email's thread. A reply marks the contact
  Replied and sets the application's Next Action; a bounce marks it Bounced. App Status
  never changes because of outreach.

## Your numbers

```bash
jobengine stats              # scans, applications by status, response and interview rates, outreach
jobengine stats --markdown   # the same as a table to paste into a README or a slide
```

Each scan adds a line to `data/scan_history.json`. A response is an assessment, interview,
offer or rejection; an automatic "we received your application" email doesn't count.

## Your own settings

Every file in `config/` can be changed for you alone with a git-ignored `.local.yml` next to it:
`titles.local.yml`, `sources.local.yml`, `scoring.local.yml`, `outreach.local.yml`,
`firms.local.yml`. Write only what you change; the rest comes from the shared file, and
`git pull` never conflicts with your choices. Lists (like `tracks`) are replaced whole.

```yaml
# config/sources.local.yml: no LinkedIn/Indeed scraping for me
jobspy:
  enabled: false
```

```yaml
# config/scoring.local.yml: a smaller model, or any OpenAI-compatible server
lm_studio:
  url: http://localhost:11434/v1     # Ollama; for a hosted server put LLM_API_KEY=... in .env
  model: qwen3
```

Run `jobengine doctor` any time to see what is set up and what to fix next.

## Run it every morning (macOS)

```bash
./scripts/schedule-daily-scan.sh        # every day at 07:30 (or: ./scripts/schedule-daily-scan.sh 6 45)
./scripts/schedule-daily-scan.sh off    # stop
```

launchd runs `jobengine track` every hour and `scripts/daily-scan.sh` each morning, which scans every enabled source, writes the CSVs and a log
in `logs/`, and shows a notification with the number of new roles. If the Mac is asleep at the
scheduled time, the scan runs as soon as it wakes.

- **Windows:** `.\scripts\schedule-daily-scan.ps1` in PowerShell does the same with Task
  Scheduler (`.\scripts\schedule-daily-scan.ps1 off` stops it). Not yet tried on a real
  Windows machine; tell us if it fails.
- **Linux:** add `30 7 * * * cd /path/to/finance-internship-engine && ./scripts/daily-scan.sh`
  with `crontab -e`.

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
  outreach/      Hunter.io contacts, templates and Gmail drafts (data/outreach.json)
  packet.py      application packets; formfill.py fills the form in a browser (never submits)
  zone.py        OpenRouteService drive-time zone
  cli.py         `jobengine scan`, `jobengine zone`
tests/           pytest suite and fixtures
```

## Roadmap

- [x] Collect from six sources, de-duplicate, filter by title, eligibility, season, location
- [x] Fit score (1–5) for each role from a local LLM, with a one-line reason
- [x] Application packets: prefilled answers and form filling (submission stays manual)
- [x] Tracker sync: Gmail status emails update the application spreadsheet
- [x] Outreach: recruiter contact drafts, sent only after approval

## License

MIT
