# Leads Scraper

Lead capture and outreach automation: find businesses, enrich and score them, then follow up by email, WhatsApp and (later) voice.

```
Backend/    FastAPI API, scrapers, scoring, outreach scripts, webhooks, scheduler
Frontend/   React + Vite admin panel
```

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

Tests use a throwaway in-memory database and never touch `data/leads.db`.

## Configuration (`Backend/.env`)

| Key | Needed for |
| --- | --- |
| `GROQ_API_KEY` | All AI text: emails, website analysis, replies |
| `GOOGLE_PLACES_API_KEY` | Lead capture (SerpAPI is an optional second source) |
| `EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD`, `EMAIL_FROM_NAME` | Sending email and reading replies (a Gmail App Password) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` | Voice calls (later) |
| `ELEVENLABS_API_KEY` | Voice agent speech (optional if another voice is used) |
| `DATABASE_URL` | SQLite file (default `sqlite:///./data/leads.db`) |
| `PUBLIC_BASE_URL` | Public address of this API, so Twilio can reach the webhooks |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `AUTH_SECRET` | Admin login (see below). Empty `ADMIN_PASSWORD` = no login |

Email verification needs no key: it checks syntax, disposable domains and the domain's mail server. If `ZEROBOUNCE_API_KEY` is set it is used instead.

Never commit `.env`. It is already in `.gitignore`.

### Admin login

Set `ADMIN_PASSWORD` (and optionally `ADMIN_USERNAME`, default `admin`) in `Backend/.env` and restart the backend: the admin panel then asks for a sign-in and every API call needs the token it returns. Set `AUTH_SECRET` to a long random text so people stay signed in across restarts. Five wrong attempts lock that address out for 10 minutes.

With no `ADMIN_PASSWORD` there is **no login**: anyone who can reach the server can use the panel and the API. Do that only on your own computer. The webhooks (`/webhooks/*`, `/voice/*`) and the open-tracking pixel stay open because Instantly and Twilio cannot send a token.

### Theme

Colours live in `Frontend/src/theme.css` (dark and light, the agency-site purple). The sun/moon button in the top bar switches; the choice is remembered in the browser. The company name is in `Frontend/src/brand.js`.

## Database

A SQLite file (`Backend/data/leads.db`). Tables and new columns are added by themselves on start.

Back it up by copying the file now and then. Moving to a hosted database (for example PostgreSQL) is a later step; the old Supabase/Alembic work is kept in `_removed/supabase/`.

## Project layout

| Path | Purpose |
| --- | --- |
| `Backend/app.py` | API routes, startup, CORS, error handling |
| `Backend/database.py` | Models and queries (SQLite) |
| `Backend/profile_schema.py`, `profile_service.py`, `profiles_api.py` | Industry Profiles: search wording, scoring rules, offer, follow-up sequence |
| `Backend/scoring_engine.py` | Scores a lead by running its profile's rules |
| `Backend/queue_service.py`, `queue_api.py` | Outreach Queue: Hot/Warm leads waiting to be contacted |
| `Backend/scheduling.py` | Per-lead schedule: working hours in the lead's time zone, gaps, daily limits, follow-up timing |
| `Backend/message_writer.py`, `message_service.py`, `messages_api.py` | Writes each step's message, approval, release time, sending |
| `Backend/email_sender.py`, `email_inbox.py` | Gmail: send by SMTP, read replies and bounces by IMAP |
| `Backend/whatsapp_api.py`, `connections.py`, `connections_api.py` | Contract with the WhatsApp extension; status of WhatsApp and email |
| `Backend/replies.py`, `reply_ai.py`, `phones.py` | Replies and opt-outs for both channels; AI labels each reply and drafts the answer; phone number handling |
| `Backend/tracking_api.py` | Optional email open tracking (an invisible image); needs `EMAIL_OPEN_TRACKING=true` and a public `PUBLIC_BASE_URL` |
| `Backend/geo.py` | Country and time zone of a lead |
| `Backend/scripts/` | Pipeline steps: scraper, auditor, email verify, outreach, voice |
| `Backend/webhooks/` | Instantly, Twilio and voice callbacks |
| `Backend/scheduler.py` | Dispatcher: prepares messages, sends due emails, reads the inbox |
| `Backend/utils/` | Crawler, AI helper, exporters, console |
| `Backend/tests/` | Automated tests |
| `Frontend/src/pages/` | Admin panel pages (Outreach queue and Industry profiles included) |
| `WhatsApp_Extension/` | Chrome extension that sends the WhatsApp messages; see its README. The admin panel's WhatsApp chip offers it as a zip download (`/extension/download`) with the install steps |
| `_removed/` | Code taken out of the app (public landing page, login), kept for reference |

## How a lead moves through the system

1. **Capture**: the scraper searches using the chosen profile's wording and saves leads with their country and time zone.
2. **Audit**: the lead is crawled, the profile's rules are run, and it gets a score and a tier (Hot / Warm / Cold).
3. **Queue**: Hot and Warm leads join the Outreach Queue automatically.
4. **Schedule**: each lead follows the profile's sequence on its own clock, inside its own working hours.
5. **Write**: for a due email or WhatsApp step the AI writes the message from the profile's offer.
6. **Approve**: per channel, in **Sending rules**: *Manual* (you do it and press Done), *Assisted* (you approve the draft on the Outreach queue page) or *Auto* (approved by itself). Approved messages get a planned send time, spaced by random gaps, inside the lead's working hours.
7. **Send**: email goes out from this server through Gmail; WhatsApp is picked up by the Chrome extension. Calls are done by hand for now.
8. **Stop**: a reply (email or WhatsApp) or an opt-out stops the lead's sequence; "STOP" adds the lead to the DNC list; a bounce marks the address invalid.
9. **Understand**: the AI labels each reply (interested, question, not now, stop, other) and drafts an answer from the profile's offer and FAQ. A "not now" lead is contacted again after a set number of days. An AI answer is sent only after approval, unless "AI answers without approval" is on for an Auto channel.

The dispatcher (`DISPATCHER_ENABLED`, on by default) does steps 5, 6 (Auto only) and 7 for email every minute, and reads the inbox every few minutes. Nothing is sent without approval unless a channel is set to Auto.
