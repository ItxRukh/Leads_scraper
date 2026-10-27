# Leads Scraper WhatsApp Sync

A small Chrome extension. It sends the WhatsApp messages that Leads Scraper has prepared, from **your own WhatsApp Web**, and tells the backend when a lead replies.

The backend decides what is sent and when (working hours, gaps, daily limits, approval). The extension only delivers.

## Install

1. Open `chrome://extensions` in Chrome and turn on **Developer mode**.
2. Click **Load unpacked** and choose this `WhatsApp_Extension` folder.
3. Start the backend (`uvicorn app:app --port 8000` in `Backend/`).
4. Open <https://web.whatsapp.com> in the same Chrome and scan the QR code with your phone (once).
5. Click the extension icon. The backend address is `http://localhost:8000` by default; press **Test connection**.

The navbar of the admin panel shows a WhatsApp chip: green when this extension is reporting from a logged-in WhatsApp Web.

## What has to stay running

- The computer is on and Chrome is open with a WhatsApp Web tab (it can be pinned or minimised, not closed).
- The backend is running.

If the tab is closed, nothing is lost: messages wait in the queue and go out once it is open again. The admin panel warns you when messages are waiting and WhatsApp Web is not open, and offers an **Open WhatsApp Web** button. The extension can also open the tab by itself (switch in the popup).

## How a message travels

```
backend:   an approved WhatsApp message becomes due        (GET  /whatsapp/queue)
extension: waits a few seconds, sends it in WhatsApp Web
extension: reports sent / failed / not on WhatsApp         (POST /whatsapp/report)
extension: a lead writes back                              (POST /whatsapp/inbound)
backend:   the lead's sequence stops, "STOP" adds it to the DNC list
```

- At most one message per 20 seconds from the extension itself; the backend spaces messages further with the gaps from **Sending rules**.
- Results that cannot be delivered (backend down) are kept and sent later.
- A reply is matched to a lead by phone number. Chats that WhatsApp addresses by a hidden id are matched through the numbers this extension sent to.

## Settings (popup)

| Setting | Meaning |
| --- | --- |
| Backend address | Where the API runs. Another address asks Chrome for permission once |
| Send queued messages | Turn off to pause sending; the status signal keeps going |
| Open WhatsApp Web when messages are waiting | Opens a pinned tab if none is open (needs Chrome to be running) |

## Files

| File | Runs in | Purpose |
| --- | --- | --- |
| `manifest.json` | | Permissions and which scripts run where |
| `page-bridge.js` | WhatsApp Web page | Sends a message, reads login state, watches incoming messages |
| `content.js` | WhatsApp Web tab | Every 20 s: report status, fetch work, deliver, report back |
| `background.js` | Service worker | The only code that talks to the backend; settings and retry list |
| `popup.*` | Toolbar popup | Status and settings |
| `shared.js` | everywhere | Small pure helpers |
| `tests/` | Node | `node --test WhatsApp_Extension/tests` |

## Limits to know

- This uses WhatsApp Web's internal code, like any such tool. If WhatsApp changes it, a send reports "WhatsApp Web internals not available" instead of failing silently, and `page-bridge.js` needs an update.
- Automated messaging to people who have not asked for it can get a number restricted or banned. Start with a dedicated number, low daily limits and the gaps from **Sending rules**.
- Tests cover the extension's own logic (delivery, retries, reply matching, settings). They cannot exercise WhatsApp Web itself, so the first real send should be to your own second number.
