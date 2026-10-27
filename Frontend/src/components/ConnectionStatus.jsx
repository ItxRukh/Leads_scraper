import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import "./ConnectionStatus.css";
import { useConnections } from "./connectionsContext";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const WHATSAPP_URL = "https://web.whatsapp.com";

const openWhatsApp = () => window.open(WHATSAPP_URL, "_blank", "noopener");

// ── how each state looks ──────────────────────────────────────────────────────
const WHATSAPP_LOOK = {
  connected: { tone: "ok",   label: "Connected" },
  loading:   { tone: "warn", label: "Starting…" },
  offline:   { tone: "warn", label: "Not open" },
  logged_out: { tone: "bad", label: "Scan QR" },
};
const EMAIL_LOOK = {
  ok:             { tone: "ok",   label: "Ready" },
  unknown:        { tone: "warn", label: "Not tested" },
  error:          { tone: "bad",  label: "Problem" },
  not_configured: { tone: "bad",  label: "Not set up" },
};

function seenText(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

// ── one chip with a small panel under it ──────────────────────────────────────
function Chip({ icon, name, look, children }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  return (
    <div className="cx-wrap" ref={ref}>
      <button className="cx-chip" onClick={() => setOpen(o => !o)} title={`${name}: ${look.label}`}>
        <i className={`ti ${icon}`} />
        <span className="cx-name">{name}</span>
        <span className={`cx-dot cx-dot--${look.tone}`} />
        <span className="cx-label">{look.label}</span>
      </button>
      {open && <div className="cx-panel">{children}</div>}
    </div>
  );
}

// Chrome does not let a website install an extension, so the panel offers the zip and the steps.
function ExtensionDownload() {
  const [showSteps, setShowSteps] = useState(false);
  const [copied, setCopied] = useState(false);

  async function copyAddress() {
    try {
      await navigator.clipboard.writeText("chrome://extensions");
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // the address is shown in the steps anyway
    }
  }

  return (
    <div className="cx-install">
      <div className="cx-actions">
        <a className="btn-ghost cx-link" href={`${API}/extension/download`} download>
          <i className="ti ti-download" /> Download extension (.zip)
        </a>
        <button className="btn-ghost" onClick={() => setShowSteps(s => !s)}>
          {showSteps ? "Hide steps" : "How to install"}
        </button>
      </div>
      {showSteps && (
        <ol className="cx-steps">
          <li>Unzip the downloaded file. You get a folder called <b>WhatsApp_Extension</b>.</li>
          <li>
            In Chrome open <code>chrome://extensions</code>{" "}
            <button className="cx-copy" onClick={copyAddress}>{copied ? "Copied" : "Copy address"}</button>
          </li>
          <li>Turn on <b>Developer mode</b> (top right).</li>
          <li>Click <b>Load unpacked</b> and choose the unzipped folder.</li>
          <li>Open WhatsApp Web in the same Chrome and scan the QR code once.</li>
        </ol>
      )}
    </div>
  );
}

function WhatsAppPanel({ w, refresh }) {
  return (
    <>
      <div className="cx-title">WhatsApp extension</div>
      <div className="cx-row"><span>Status</span><b>{WHATSAPP_LOOK[w.state]?.label}</b></div>
      <div className="cx-row"><span>Last signal</span><b>{seenText(w.last_seen_seconds)}</b></div>
      {w.number && <div className="cx-row"><span>Number</span><b>+{w.number}</b></div>}
      <div className="cx-row"><span>Waiting to send</span><b>{w.waiting}</b></div>
      {w.state !== "connected" && (
        <>
          <p className="cx-hint">
            {w.state === "logged_out"
              ? "WhatsApp Web is open but not logged in. Scan the QR code with your phone."
              : "Open WhatsApp Web in the Chrome that has the extension installed. A different browser will not connect."}
          </p>
          <div className="cx-actions">
            <button className="btn-primary" onClick={openWhatsApp}>
              <i className="ti ti-brand-whatsapp" /> Open WhatsApp Web
            </button>
            <button className="btn-ghost" onClick={refresh}>Check again</button>
          </div>
        </>
      )}
      <ExtensionDownload />
    </>
  );
}

function EmailPanel({ e, refresh }) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);

  async function test() {
    setTesting(true);
    setResult(null);
    try {
      const res = await fetch(`${API}/connections/email/test`, { method: "POST" });
      setResult(await res.json());
      refresh();
    } catch {
      setResult({ ok: false, sending: { error: "Could not reach the backend." }, reading_replies: { ok: true } });
    } finally {
      setTesting(false);
    }
  }

  const problem = result && !result.ok ? (result.sending.error || result.reading_replies.error) : null;

  return (
    <>
      <div className="cx-title">Email account</div>
      <div className="cx-row"><span>Status</span><b>{EMAIL_LOOK[e.state]?.label}</b></div>
      {e.address && <div className="cx-row"><span>Account</span><b>{e.address}</b></div>}
      <div className="cx-row"><span>Sent today</span><b>{e.sent_today}</b></div>
      <div className="cx-row"><span>Waiting to send</span><b>{e.waiting}</b></div>
      <div className="cx-row">
        <span>Open tracking</span>
        <b>{!e.open_tracking.enabled ? "Off" : e.open_tracking.ready ? `On · ${e.open_tracking.opened} opened` : "Needs a public address"}</b>
      </div>
      {e.open_tracking.enabled && !e.open_tracking.ready && (
        <p className="cx-hint cx-hint--bad">
          PUBLIC_BASE_URL in Backend/.env must be an address the lead can reach (a domain or an ngrok URL), not localhost.
        </p>
      )}
      {e.state === "not_configured" && (
        <p className="cx-hint">
          Add EMAIL_ADDRESS and EMAIL_APP_PASSWORD (a Gmail App Password) to Backend/.env, then restart the backend.
        </p>
      )}
      {e.last_error && <p className="cx-hint cx-hint--bad">{e.last_error}</p>}
      {result?.ok && <p className="cx-hint cx-hint--ok">Sending and reading replies both work.</p>}
      {problem && <p className="cx-hint cx-hint--bad">{problem}</p>}
      {e.state !== "not_configured" && (
        <div className="cx-actions">
          <button className="btn-ghost" onClick={test} disabled={testing}>
            {testing ? <><i className="ti ti-loader-2 spin" /> Testing…</> : <><i className="ti ti-plug-connected" /> Test connection</>}
          </button>
        </div>
      )}
    </>
  );
}

/** The two status chips shown in the navbar. */
export function ConnectionChips() {
  const { data, refresh } = useConnections();
  if (!data) return null;
  const { whatsapp: w, email: e } = data;
  return (
    <div className="cx-chips">
      <Chip icon="ti-brand-whatsapp" name="WhatsApp" look={WHATSAPP_LOOK[w.state] ?? WHATSAPP_LOOK.offline}>
        <WhatsAppPanel w={w} refresh={refresh} />
      </Chip>
      <Chip icon="ti-mail" name="Email" look={EMAIL_LOOK[e.state] ?? EMAIL_LOOK.unknown}>
        <EmailPanel e={e} refresh={refresh} />
      </Chip>
    </div>
  );
}

/** A bar at the top of every page, only when something needs attention. */
export function ConnectionBanner() {
  const { data, refresh } = useConnections();
  if (!data) return null;
  const { whatsapp: w, email: e } = data;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  const bars = [];
  if (w.waiting > 0 && !w.online) {
    bars.push(
      <div className="cx-banner cx-banner--warn" key="wa">
        <i className="ti ti-alert-triangle" />
        <span>
          {plural(w.waiting, "WhatsApp message")} waiting, but WhatsApp Web is{" "}
          {w.state === "logged_out" ? "not logged in" : "not open"}.
        </span>
        <button className="btn-primary" onClick={openWhatsApp}>
          <i className="ti ti-brand-whatsapp" /> Open WhatsApp Web
        </button>
        <button className="btn-ghost" onClick={refresh}>Check again</button>
      </div>
    );
  }
  if (e.waiting > 0 && (e.state === "error" || e.state === "not_configured")) {
    bars.push(
      <div className="cx-banner cx-banner--warn" key="em">
        <i className="ti ti-alert-triangle" />
        <span>
          {plural(e.waiting, "email")} waiting, but the email account is{" "}
          {e.state === "not_configured" ? "not set up" : "not working"}. Open the Email chip at the top for details.
        </span>
      </div>
    );
  }
  const toApprove = w.to_approve + e.to_approve;
  if (toApprove > 0) {
    bars.push(
      <div className="cx-banner cx-banner--info" key="ap">
        <i className="ti ti-message-check" />
        <span>{plural(toApprove, "message")} ready for your approval.</span>
        <Link className="btn-ghost cx-link" to="/admin/queue">Review</Link>
      </div>
    );
  }
  return bars.length ? <div className="cx-banners">{bars}</div> : null;
}
