# Finance Internship Engine

This public repository provides `jobengine`, a Python 3.10+ CLI for students.
It collects and filters finance internship postings, scores fit with a local AI
model, and updates ApplicationTracker.xlsx from Gmail. Gmail and the local
model are optional for scanning.

## Layout

- `src/jobengine/` holds the CLI and application code. `tracker_sync.py` handles Gmail-to-tracker sync.
- `config/*.yml` holds settings. Keep configurable behavior there.
- `tests/` holds the tests. Use fake data from `config/profile.example.yml`.

## Setup and tests

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

## Working rules

- Work on a new branch and open a PR into `main`. Never push to `main`.
- Keep changes scoped to the task. Skip unrelated refactors and cleanup.
- Add a test for every behavior change and ensure `pytest -q` passes.
- Never commit `config/profile.yml`, `data/`, `auth/`, `.env`, or spreadsheets. They contain personal data and are git-ignored.
- Tracker statuses never move backwards, except to Rejected.
