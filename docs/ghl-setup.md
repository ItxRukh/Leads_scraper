# GoHighLevel setup

GoHighLevel (GHL) sends the emails. This app prepares the leads and the first email, and (once connected) pushes each approved lead to GHL and reads back what happened. Everything below is done inside one dedicated **outbound sub-account**, so the agency account and client sub-accounts are never involved.

> Status: the GHL connection is the next piece of work. This page is the plan it is built against. GHL menu and field names can differ slightly by plan and version, so check each one in your own account before relying on it.

## 1. Connection

1. In the outbound sub-account: **Settings > Private Integrations**, create a token with contacts access.
2. Copy the sub-account's **Location ID** (Settings > Business Profile).
3. Put both in `Backend/.env`:

```
GHL_API_TOKEN=...
GHL_LOCATION_ID=...
```

Never paste the token into chat, a ticket or a commit.

## 2. Sending domain

- Use a dedicated sending **subdomain** (for example `mail.yourdomain.com`), never the main domain.
- Set up SPF, DKIM and DMARC for it and send a test email to a mailbox you control.
- GHL has no warm-up network: ramp up by hand (see section 6).
- Every email needs the company's postal address and an unsubscribe link (GHL adds both to its email footer when the business details are filled in). The first email written by this app also ends with "reply STOP".

## 3. Contact fields (custom fields)

Create these in one folder named "Leadwren":

| Field | Type | Filled by |
| --- | --- | --- |
| `lead_id` | Single line | this app (links the contact back to the lead) |
| `industry` | Single line | this app |
| `weakness` | Large text | this app (already checked against the website) |
| `email_1_subject` | Single line | this app |
| `email_1_body` | Large text | this app (the approved first email) |

Only the first email is written per lead. The three follow-ups are short templates kept in GHL and filled with `{{contact.weakness}}` and `{{contact.industry}}`.

## 4. Tags

| Tag | Set by | Meaning |
| --- | --- | --- |
| `source-import` | this app | the contact came from an import |
| `industry-hvac` (and so on) | this app | industry |
| `leadwren-ready` | this app | released: starts the outbound workflow |
| `lw-sent` | GHL workflow | the first email went out |
| `lw-opened` | GHL workflow | an email was opened |
| `lw-replied` | GHL workflow | the contact replied |
| `lw-bounced` | GHL workflow | an email bounced |
| `lw-unsubscribed` | GHL workflow | the contact unsubscribed or was blocked |
| `lw-no-response` | GHL workflow | the whole sequence ran without a reply |

## 5. Workflows (keep them in separate folders)

**Folder "Outbound"**

1. Trigger: tag `leadwren-ready` added.
2. Wait until a time window in the contact's time zone (for example Tuesday to Thursday, 9:00 to 11:00).
3. Send email 1 from `{{contact.email_1_subject}}` and `{{contact.email_1_body}}`; add tag `lw-sent`.
4. Wait 3 days; if the contact has not replied, send follow-up 1.
5. Wait 4 days; send follow-up 2. Wait 7 days; send follow-up 3.
6. At the end add tag `lw-no-response` and move the opportunity to "No response".
7. Turn on "stop on response" so a reply removes the contact from the workflow.

**Folder "Flags" (small workflows that only add tags)**

- Email event: opened -> add `lw-opened`.
- Email event: bounced -> add `lw-bounced`.
- Email event: unsubscribed -> add `lw-unsubscribed`.
- Customer replied -> add `lw-replied` and move the opportunity to "Replied".

**Folder "Inbound" (landing page, later)**

Form submitted -> thank-you email and booking link. These contacts asked to hear from you, so they use their own workflow, and the form has a consent checkbox. A contact that books must be removed from the Outbound workflow.

## 6. Ramp-up

| Week | Emails released per day |
| --- | --- |
| 1 | 10 to 15 |
| 2 | 20 to 30 |
| 3 and later | up to 40 to 50, only while bounces stay below 2% and spam complaints below 0.1% |

If a week goes over those limits, pause for a week and find out why before sending more.

## 7. How "do not contact" reaches this app

GHL sends the emails, so unsubscribes and bounces happen there. The app asks GHL for them every few minutes (this works from a local computer, no public address needed):

1. It reads the contacts it pushed (by `lead_id`), their Do Not Disturb status and their `lw-*` tags.
2. A contact that unsubscribed, bounced or is blocked is added to the do-not-contact list with the reason "Blocked in GoHighLevel", and its lead is marked do-not-contact. Future imports skip it.
3. `lw-replied`, `lw-opened`, `lw-bounced` and `lw-sent` feed the numbers in the Dashboard's GoHighLevel box.

The exact API field names for Do Not Disturb and tags are confirmed against a real contact when the connection is built.
