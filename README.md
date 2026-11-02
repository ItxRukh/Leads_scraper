# Leadwren

Lead preparation for outbound email to US local-service businesses. You paste or upload a list of leads; the app cleans it, checks each website, corrects the "weakness" written in the list, verifies the email addresses and writes a personal first email from each industry's knowledge base. You approve the emails, and GoHighLevel (GHL) sends them and handles everything after that: follow-ups, replies, SMS, calls, booking and reporting.

```
Backend/    FastAPI API: import, website check, weakness check, email checks, first-email writing
Frontend/   React + Vite admin panel
docs/       GoHighLevel setup guide
```

## Status

| Part | State |
| --- | --- |
| Import (CSV, Excel, paste), cleaning, duplicate and do-not-contact checks | Done |
| Website check and **weakness check** (the list's weakness is compared with the site and corrected) | Done |
| Email address check (free, plus MillionVerifier or ZeroBounce when a key is set) | Done |
| First email written per lead from the industry knowledge base, with approval, editing and rewrite | Done |
| Dashboard, Leads page, do-not-contact list, Industry profiles, admin login, dark and light theme | Done |
| GoHighLevel connection (push approved leads, daily release, read back unsubscribes, bounces and replies) | **Next** (see `docs/ghl-setup.md`) |

Until the GHL connection exists, approved emails wait on the Approve emails page. Nothing is ever sent from this app.

## How a lead moves through the app

1. **Import.** Paste or upload a file with columns such as `business_name`, `email`, `website`, `industry`, `city`, `state` and `weakness` (what the business is missing, for example "no online booking"). Columns are matched (the AI helps with unknown headings), every field is tidied, the industry is tagged, and duplicates and do-not-contact leads are skipped.
2. **Check** (one button, started automatically after an import).
   1. The website is fetched and what it has or lacks is recorded (booking, chat, click-to-call, contact form, after-hours wording, reviews, mobile page, https, and more).
   2. The **weakness is checked** against those facts (below).
   3. The email address is checked.
   4. The **first email** is written from the industry's knowledge base and the lead's weakness.
3. **Approve.** On the Approve emails page you read, edit, rewrite, discard or approve each first email.
4. **GoHighLevel** (next piece of work). Approved leads are pushed to GHL with their fields, a daily number is released, and a GHL workflow sends the first email and three short follow-up templates and stops when the contact replies.

### The weakness check

`Backend/weakness_check.py` compares each point of the written weakness with what the website audit found:

| Situation | Result |
| --- | --- |
| The point matches the site | It stays, word for word |
| The site has the thing the point says is missing | The point is removed |
| Every point was wrong, or none was written | The real gaps found by the audit are written instead |
| Nothing can prove or disprove the point (for example "outdated look") | It stays |

A point the audit has no check for (for example "no financing options") is removed only if the AI quotes words that really appear on the site. The original text is kept (`weakness_original`) and the Leads page marks an edited weakness with "AI edited". The email wording is soft ("I couldn't find..."), never a certain claim.

### Do not contact

A lead goes on the list when you press **Do not contact** on it, add an address, phone or domain on the DNC page, or (once GHL is connected) when GHL reports an unsubscribe, bounce or block. Listing an address also marks every matching lead and discards its unsent drafts, and imports skip it from then on.

## Pages

| Page | What it is for |
| --- | --- |
| Dashboard | What needs you now, funnel from import to reply, email quality, weakness numbers, by industry, by import, do-not-contact, GHL numbers |
| Import leads | Add leads, see the preview and the progress of the checks |
| Leads | Every lead with its weakness, email check and where it is; open one for details |
| Approve emails | The first emails waiting for approval, and the approved ones |
| Industry profiles | Per industry: the offer, the knowledge base the emails are written from, and the website rules |
| DNC / Blacklist | The do-not-contact list |
| Settings | Company name, sender name, theme, export |

## Setup

### Backend

```bash
pip install -r requirements.txt
cd Backend
cp .env.example .env        # then fill in the keys you have
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

API docs: http://localhost:8000/docs

### Frontend

```bash
cd Frontend
npm install
npm run dev                 # http://localhost:5173
```

Set `VITE_API_URL` if the API is not on `http://localhost:8000`.

### Tests

```bash
cd Backend
python -m pytest -q tests
```

Tests use a throwaway in-memory database and never touch `Backend/data/leads.db`.

## Configuration (`Backend/.env`)

| Key | Needed for |
| --- | --- |
| `GROQ_API_KEY` | All AI text: column matching, industry tagging, weakness checks, first emails |
| `MILLIONVERIFIER_API_KEY` (or `ZEROBOUNCE_API_KEY`) | Optional paid check that the mailbox exists. Without it only syntax, disposable domains and the mail server are checked |
| `GHL_API_TOKEN`, `GHL_LOCATION_ID` | The GoHighLevel outbound sub-account (see `docs/ghl-setup.md`) |
| `DATABASE_URL` | SQLite file (default `sqlite:///./data/leads.db`) |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `AUTH_SECRET` | Admin login. An empty `ADMIN_PASSWORD` means no login |
| `EMAIL_FROM_NAME` | The sender name used as the signature of each email |

Never commit `.env`; it is already in `.gitignore`.

### Admin login

Set `ADMIN_PASSWORD` (and optionally `ADMIN_USERNAME`, default `admin`) in `Backend/.env` and restart the backend: the admin panel then asks for a sign-in and every API call needs it. With no `ADMIN_PASSWORD` there is **no login**: anyone who can reach the server can use the panel and the API, so do that only on your own computer.

### Theme

Colours live in `Frontend/src/theme.css` (dark and light). The sun/moon button in the top bar switches; the choice is remembered in the browser. The company name is in `Frontend/src/brand.js`.

## Database

A SQLite file (`Backend/data/leads.db`, not committed). Tables and new columns are created automatically on start, and the Industry profiles are created from `Backend/industry_profiles.py` and the files in `Backend/knowledge/`. Back it up by copying the file now and then.

## Knowledge base

`Backend/knowledge/<industry>.md` holds, for each industry, a short summary (used in first emails), a FAQ and a knowledge base: who we help, the problems we fix, every workflow with what it does and needs, benefits, how we work and what we do not do. The files contain **no prices**. They are loaded into each industry profile when the backend starts. The AI is told never to state prices, and to name a specific software product as supported only if that exact name appears in the knowledge base.

Profiles nobody has edited are refreshed automatically when these files change. After you save a profile on the Industry profiles page it is yours and is never overwritten.

## Project layout

| Path | Purpose |
| --- | --- |
| `Backend/app.py` | API routes, startup, CORS, error handling |
| `Backend/lead_import.py`, `import_api.py` | Import: parsing, cleaning, duplicates, preview, commit, progress |
| `Backend/pipeline_run.py` | The checks of one import, in order, and their progress |
| `Backend/weakness_check.py` | Compares the written weakness with the website's facts |
| `Backend/utils/site_signals.py`, `scripts/auditor.py` | Website fetching (with an SSRF guard) and the audit |
| `Backend/first_email.py`, `message_writer.py` | Writes the first email of each ready lead |
| `Backend/message_service.py`, `messages_api.py` | Drafts, editing, approval |
| `Backend/email_verification.py` | Free and paid email address checks |
| `Backend/dnc_service.py` | Putting a lead or an address on the do-not-contact list |
| `Backend/lead_journey.py` | The "where it is" answer for each lead |
| `Backend/dashboard_api.py` | The Dashboard numbers |
| `Backend/profile_schema.py`, `profile_service.py`, `profiles_api.py`, `industry_profiles.py`, `industries.py` | Industry profiles and the industry list |
| `Backend/scoring_engine.py` | Runs a profile's website rules |
| `Backend/knowledge/` | Per-industry knowledge base |
| `Backend/auth.py` | Admin login |
| `Frontend/src/pages/` | Admin panel pages |
| `docs/ghl-setup.md` | GoHighLevel setup guide |
| `_removed/` | Code taken out of the product and kept for reference (Google Maps scraper, WhatsApp extension, SMS and voice-call scripts, old pages) |

Some backend modules are dormant until the GoHighLevel connection replaces them and they are deleted: the Instantly client and webhook, the outreach queue and scheduler, Gmail sending, reply handling and the old integrations API.

## Not done here on purpose

SMS, WhatsApp and voice calls are not made by this app. Cold SMS and automated calls to people who never asked are what US carriers and the TCPA punish; any SMS or call happens in GoHighLevel after a lead has replied and agreed to it.
