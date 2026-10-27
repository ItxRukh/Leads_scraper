# GoHighLevel: interested leads from outbound email

When a lead answers an outbound email with interest (or a real question), this app sends the lead to a
GoHighLevel workflow. From there everything is GoHighLevel's job: SMS, calls, the booking calendar and the
pipeline, because the lead has now reached out to us.

## Set up (once)

1. In GoHighLevel: **Automation → Workflows → Create workflow → trigger "Inbound Webhook"**.
2. Copy the webhook URL into `Backend/.env` as `GHL_WEBHOOK_URL`, then restart the backend.
3. Open **Integrations → GoHighLevel → Send a test contact**. In the workflow, open the trigger and choose
   **Fetch sample requests**: the test contact appears with `isTest: true`, so the fields can be mapped.
4. Build the workflow (suggestions below) and **publish** it. A draft does nothing.

## What is sent (JSON, POST)

| Field | Meaning |
|---|---|
| `source` | always `outbound-prospecting` (the website forms use other values), so one workflow can branch on it |
| `event` | `interested`, `question`, `meeting_booked`, `manual` (sent by hand from the lead's page) or `test` |
| `isTest` | `true` only for the test contact |
| `leadId` | this app's id for the lead |
| `businessName`, `firstName`, `lastName`, `contactName` | who they are |
| `email`, `phone`, `website`, `city`, `state` | how to reach them (phone is `+1XXXXXXXXXX`) |
| `industry`, `industryKey` | for example `HVAC` and `hvac` |
| `tags` | `outbound`, `industry-<key>`, and the reply label (`interested` / `question`) |
| `auditScore`, `tier` | how much the website was missing, and Hot / Warm / Cold |
| `topOpportunities` | the three biggest findings, in plain words |
| `matchingServices` | our workflows that fix them (names from the agency website) |
| `replyLabel`, `replySummary`, `replyText` | what the lead wrote, as the AI read it |
| `suggestedReply` | a draft answer from the industry's knowledge base, for the person who replies |

A lead is sent once. If GoHighLevel rejects the request, the lead's page shows the reason and the push is
retried the next time the lead is sent (or with **Send to GoHighLevel now**).

## Suggested workflow

1. Create or update the contact: map name, email, phone, website, city, state.
2. Add the tags from `tags`.
3. Create an opportunity in the pipeline, stage *Replied* (value: leave empty).
4. Store `replyText`, `topOpportunities`, `matchingServices` and `suggestedReply` in custom fields or a note,
   so the person who replies can see them.
5. Notify the owner (email or task): "Reply from {{businessName}}: {{replySummary}}".
6. Only after the lead has replied: SMS, a call, or the booking link are allowed.

## Rules to keep

- Do **not** text or call leads that have not replied: cold SMS and automated calls to people who never asked
  are what carriers and the FCC/TCPA rules punish.
- Leads that unsubscribe or reply STOP never reach this workflow; they go on the do-not-contact list here and in
  Instantly.
