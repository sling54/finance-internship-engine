# Setting up jobengine for SMIF

Two parts: what one officer does once (about 20 minutes), and a 10-minute demo for a meeting.
Members then follow the README's [Quick start](../README.md#quick-start-about-30-minutes).

## Once, by one officer (~20 min)

The Gmail parts (tracker sync, alert emails, outreach) need a Google OAuth client. One client
can serve the whole club while it stays in Google's **Testing** mode (up to 100 test users).

1. **Project:** in [Google Cloud Console](https://console.cloud.google.com), create a project
   named something like `smif-jobengine`.
2. **API:** APIs & Services → Library → **Gmail API** → Enable.
3. **Consent screen:** APIs & Services → OAuth consent screen.
   - User type **External**; app name `SMIF jobengine`; your email as support and developer
     contact.
   - Leave publishing status on **Testing**. Don't submit it for verification.
   - **Test users:** add each member's Gmail address. Only these accounts can sign in.
4. **Client:** APIs & Services → Credentials → Create credentials → **OAuth client ID** →
   Application type **Desktop app** → Create → **Download JSON**.
5. **Share it privately** (a direct message or a shared drive folder only officers can open).
   Each member saves it as `auth/credentials.json` in their copy of the repo. It's git-ignored,
   so it never reaches GitHub. Never post it in a public channel.
6. **New members:** add their Gmail under Test users before they run `jobengine track`.

What members should know:

- While the app is in Testing, Google ends each sign-in after about 7 days. When `track` opens
  the browser again, they sign in again.
- The engine reads job emails, marks alert emails as read, writes and labels outreach drafts,
  and sends only drafts the member approves. It deletes nothing except outreach drafts the
  member skips.

## Demo for a meeting (~10 min)

Run it on your own laptop, already set up, with LM Studio open.

1. **The problem (1 min):** checking a dozen job sites a day and rereading the same postings.
2. **Scan (2 min):** `jobengine scan`. Point out how many postings it collects, how many it
   keeps, and that every dropped role has a reason in `data/rejected_<date>.csv`.
3. **Results (2 min):** `jobengine results`. Filter by track, open a role, click **I applied**,
   and show it appear in the tracker.
4. **Packet (2 min):** `jobengine packet <a Greenhouse role> --fill`. The form fills in;
   **you** click Submit (or close the window for the demo).
5. **Tracker and outreach (2 min):** show the tracker's App Status and Next Action columns, then
   `jobengine outreach plan --dry-run` to show the drafts it would write.
6. **Numbers (1 min):** `jobengine stats`, then point to the README Quick start: install, then
   `jobengine init --resume`, then `jobengine doctor`.

## Common questions

- **Do I need a big laptop?** No. Scans, the tracker, packets and outreach run on any laptop.
  The AI fit score and drafts need a local model (LM Studio) or any OpenAI-compatible server;
  see "Your own settings" in the README.
- **Windows?** Yes. The daily schedule uses `scripts\schedule-daily-scan.ps1`, which hasn't been
  tried on a real Windows machine yet; tell the maintainer if it fails.
- **Does it apply for me?** No. It fills forms in; you review and submit every application,
  and you approve every outreach email.
- **Can I change what it looks for?** Yes. Rerun `jobengine init` to describe the roles you
  want, and put any setting changes in `config/*.local.yml`.
