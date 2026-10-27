# Leadwren

Outbound lead engine for US local-service businesses: import leads, audit their websites, write personal emails from each industry's knowledge base, send them through Instantly, and hand every interested reply to GoHighLevel (where SMS, calls and booking happen).

```
Backend/    FastAPI API, import, website audit, scoring, email writing, Instantly and GoHighLevel connections, scheduler
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
| `INSTANTLY_API_KEY` | Sending through Instantly (API v2); the campaign is chosen on the Integrations page |
| `INSTANTLY_WEBHOOK_SECRET` | Lets only Instantly post events (replies, bounces, unsubscribes) to this app |
| `MILLIONVERIFIER_API_KEY` (or `ZEROBOUNCE_API_KEY`) | Optional paid email check, only for Hot and Warm leads |
| `GHL_WEBHOOK_URL` | The GoHighLevel workflow that receives interested leads (see `docs/ghl-outbound.md`) |
| `EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD`, `EMAIL_FROM_NAME` | Only if you send over Gmail (SMTP) instead of Instantly; `EMAIL_FROM_NAME` is also the signature name |
| `DATABASE_URL` | SQLite file (default `sqlite:///./data/leads.db`) |
| `PUBLIC_BASE_URL` | Public https address of this API (a domain, or ngrok while testing), so Instantly can send events |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `AUTH_SECRET` | Admin login (see below). Empty `ADMIN_PASSWORD` = no login |

Email verification needs no key: it checks syntax, disposable domains and the domain's mail server. With a paid key the mailbox itself is also checked, only for Hot and Warm leads.

Never commit `.env`. It is already in `.gitignore`.

### Admin login

Set `ADMIN_PASSWORD` (and optionally `ADMIN_USERNAME`, default `admin`) in `Backend/.env` and restart the backend: the admin panel then asks for a sign-in and every API call needs the token it returns. Set `AUTH_SECRET` to a long random text so people stay signed in across restarts. Five wrong attempts lock that address out for 10 minutes.

With no `ADMIN_PASSWORD` there is **no login**: anyone who can reach the server can use the panel and the API. Do that only on your own computer. The Instantly webhook (`/webhooks/*`, protected by `INSTANTLY_WEBHOOK_SECRET` instead) and the open-tracking pixel stay open because Instantly and mail apps cannot send a login token.

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
| `Backend/email_verification.py` | Free check, then the paid verifier, for the leads worth contacting |
| `Backend/instantly_client.py`, `instantly_handoff.py`, `instantly_events.py` | Instantly API v2: add a lead with its whole email sequence, and act on its events (reply, bounce, unsubscribe...) |
| `Backend/ghl_sync.py`, `integrations_api.py` | Send interested leads to GoHighLevel; the Integrations page |
| `Backend/dashboard_api.py` | The numbers on the Dashboard |
| `Backend/email_sender.py`, `email_inbox.py` | Gmail over SMTP and IMAP, used only when the sending method is Gmail |
| `Backend/connections.py`, `connections_api.py` | Status of the email connection |
| `Backend/replies.py`, `reply_ai.py`, `phones.py` | Replies and opt-outs; AI labels each reply; phone number handling |
| `Backend/tracking_api.py` | Optional email open tracking (an invisible image); needs `EMAIL_OPEN_TRACKING=true` and a public `PUBLIC_BASE_URL` |
| `Backend/geo.py` | Country and time zone of a lead |
| `Backend/scripts/` | Pipeline steps: scraper, auditor, email verify |
| `Backend/webhooks/` | The Instantly webhook |
| `Backend/scheduler.py` | Dispatcher: writes drafts, hands approved leads to Instantly, reads the inbox when sending over Gmail |
| `Backend/utils/` | Crawler, AI helper, exporters, console |
| `Backend/tests/` | Automated tests |
| `Frontend/src/pages/` | Admin panel pages (Outreach queue and Industry profiles included) |
| `_removed/` | Code taken out of the app (WhatsApp extension, SMS and voice-call scripts, the old campaign launcher, the old Analytics page, the public landing page), kept for reference |

## Import leads (Import page)

Upload a CSV / Excel file, or paste rows copied from a sheet. A template is on the page ("Download sample CSV") and in `Backend/samples/leads_sample.csv`.

| Column | Needed? | Used for |
| --- | --- | --- |
| `business_name` | **yes** | the lead's name |
| `email` | one of email / website / phone | outreach; without it the website is searched at audit time |
| `website` | (same) | the audit: what the site is missing |
| `phone` | (same) | kept for GHL; cleaned to `+1XXXXXXXXXX` |
| `industry` | recommended | the industry tag, so the right rules and message are used |
| `city`, `state` | recommended | time zone (messages go out in the lead's working hours) and personal touches |
| `contact_name`, `contact_title` | optional | the greeting ("Hi Dana") |

Other column names are recognised too (`Company`, `E-mail`, `Cell`, `Category`, first/last name...); the page shows the mapping and lets you change it. Extra columns are kept on the lead.

What happens: columns are matched, every field is tidied (emails, US phones, websites, states, formula characters), the industry is matched from the industry column or the business name, and rows are checked against your existing leads and the Do-Not-Contact list. Groq only tags industries that are still missing and matches columns it does not recognise; it never supplies emails, phones or websites, and text pasted without column names is read by Groq only if every value it returns appears in your text. Nothing is saved until you press Import, and an import can be undone while none of its leads has been contacted.

## Industry profiles and the website audit

Every industry in `Backend/industries.py` has a ready-made profile (`Backend/industry_profiles.py`), created on start and never overwritten, so edits on the Industry profiles page are kept. A lead imported with an industry is scored under that industry's profile.

**What the audit does.** For each lead it opens the homepage plus up to two pages that look like contact or booking pages, and records what it finds (`Backend/utils/site_signals.py`): tap-to-call phone link, contact form, online booking (Calendly, Housecall Pro, Zocdoc, "Book online" buttons...), chat or text-back widget, after-hours or emergency wording, reviews, a mobile-ready page, https, and an old copyright year. Page addresses that point to internal networks are refused.

**Scoring.** The score is an opportunity score out of 100, and a high score means more to gain, so Hot leads are the best prospects. Each rule is something our agency workflows fix or build on, and it names the workflow ("we offer: After-Hours Call Coverage"): missing online booking, chat or text-back, tap-to-call, contact form, after-hours wording, service-area page, intake forms, reviews; plus signs the site shows that our workflows build on (it invites estimates, sells maintenance or recurring plans, invites referrals). Weights are set per industry from what that industry's workflows are about (an urgent trade values after-hours and chat; pest control values recurring plans; a clinic values online booking and intake forms; imaging and radiology value referral intake). A named contact, an email and a second location add bonus points. The three biggest opportunities are kept on the lead (`top_gaps`), shown in the lead details with the matching workflow, and given to the email writer, which says "I couldn't find..." and never states them as facts.

If a site cannot be reached, the lead is not punished: its checks are reported as "not checked" and the score is marked incomplete.

**Check an import.** An import starts its checks by itself: first the websites (audit and score), then the email addresses of the Hot and Warm leads. Progress and the Hot / Warm / Cold counts appear on the Import page ("Check now" in Earlier imports runs it again for an older batch). It runs in the background, and nothing is sent.

**Google Maps scrape.** "Scrape new" on the Leads page tags the leads with the industry of the search ("plumbers" becomes Plumbing), scores them under that industry's profile and saves them as an import batch ("Google Maps: plumbers in Austin"), so the batch shows in Earlier imports. When the scrape ends, one button runs the same two checks.

Contact is by email only (four steps over two weeks). SMS and calls begin after a reply, from GHL.

## Knowledge base (what the AI knows about our service)

`Backend/knowledge/<industry>.md` holds, for each industry, three parts: a short summary (used in first emails), a FAQ, and a knowledge base (who we help, the problems we fix, every workflow with what it does, what it needs and how long it takes, benefits, how we work, plans and timelines, what we do not do, optional services). They were built from the agency website (`D:gency-site`) and contain **no prices**. They are loaded into each industry profile when the backend starts, into the Offer section of the Industry profiles page ("What you offer", "Questions and answers", "Knowledge base").

- First emails use the summary, the problems and the benefits. Replies to a lead use the whole knowledge base.
- The AI is told never to state prices (it says plans are scoped per business and the numbers are shared on a short call), and to name a specific software product as supported only if that exact name appears in the knowledge base; otherwise it promises to confirm on a call.
- Profiles nobody has edited (version 1) are refreshed automatically when these files change. After you save a profile on the page it is yours and is never overwritten.
- When the agency website changes, regenerate the files or edit them; every workflow name used by a scoring rule is checked against them by the tests.

## How a lead moves through the system

1. **Import** (Import page): paste or upload a file; it is cleaned, de-duplicated and tagged with an industry.
2. **Audit**: the website is checked and the lead is scored under its industry profile (Hot / Warm / Cold) with its top opportunities.
3. **Check the email address** (Import page, Hot and Warm leads only): free check, plus the paid verifier if a key is set.
4. **Queue**: Hot and Warm leads join the Outreach queue.
5. **Write**: when a lead is due, the AI writes its **whole email sequence at once** (four emails by default), from the profile's offer and knowledge base and the lead's own findings.
6. **Approve**: you review one card per lead (the follow-ups are shown under the first email and can be edited). Approving the first email approves the sequence. *Auto* mode in Sending rules approves by itself.
7. **Hand over**: at the planned time (inside the lead's working hours, under the daily limit) the lead is added to your Instantly campaign with every email as a variable. Nothing is handed over without the company's postal address, a checked email address and a chosen campaign.
8. **Instantly sends** the emails on the campaign's schedule, rotating mailboxes and stopping the sequence when the lead replies.
9. **Events come back** to `/webhooks/instantly`: a reply is recorded and labelled by the AI (interested, question, not now, stop, other); STOP and unsubscribes go on the do-not-contact list; bounces mark the address invalid; opens (`email_opened`) fill *opened at* and move the lead to the "opened" stage (switch open tracking on in the Instantly campaign).
10. **Interested?** The lead, the reply, the audit findings and a suggested answer are sent to GoHighLevel. SMS, calls and booking continue there.

### Following a lead (Leads page)

The **Where it is** column answers, for every lead, "what is happening and what comes next": waiting for the website check, email not checked, draft waiting for you, approved (and when it goes out), with Instantly, replied, in GoHighLevel, cold (not contacted), no email, or do not contact. Open a lead to see the same sentence at the top, the audit findings, the email check, the reply and the suggested answer.

### Do not contact

A lead goes on the list in four ways: it replies STOP, it unsubscribes in Instantly, you press **Do not contact** on the lead, or you add an address, phone or domain on the DNC page. In every case (`Backend/dnc_service.py`) the email, phone and domain are listed, the lead is marked and leaves every list, its queue entry stops, unsent drafts are discarded, and the lead is removed from the Instantly campaign (if Instantly cannot be reached, the lead is still blocked here and the page says so). Imports and scrapes skip listed addresses.

### Who answers a reply

A person. The AI labels the reply and writes a suggested answer from the industry's knowledge base (for interested and question replies, whether or not GoHighLevel is connected). Open the lead on the Leads page: the reply and the suggestion are there, **Edit** changes the text, and **Send now** posts it through Instantly in the same thread (`POST /emails/reply`). Nothing is sent until you press it; STOP replies and do-not-contact leads cannot be answered. Replies from before this feature have no reply details stored, so answer those in Instantly (the page says so). The industry is found through the `lead_id` that is sent with every lead to Instantly and comes back in every event; the Dashboard shows each reply with its industry.

Set the sending method on the **Integrations** page: *Instantly* (steps above) or *Gmail* (one email at a time from this server). The dispatcher (`DISPATCHER_ENABLED`, on by default) does steps 5 and 7 every minute.

## Not done here on purpose

SMS, WhatsApp and calls are not made by this app. Cold SMS and automated calls to people who never asked are what US carriers and the TCPA punish; they start in GoHighLevel after a lead has replied.
