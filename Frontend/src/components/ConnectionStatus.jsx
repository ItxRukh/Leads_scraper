import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import "./ConnectionStatus.css";
import { useConnections } from "./connectionsContext";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ── how each state looks ──────────────────────────────────────────────────────
const EMAIL_LOOK = {
  ok:             { tone: "ok",   label: "Ready" },
  unknown:        { tone: "warn", label: "Not tested" },
  error:          { tone: "bad",  label: "Problem" },
  not_configured: { tone: "bad",  label: "Not set up" },
};

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
      <div className="cx-title">{e.provider === "instantly" ? "Email sending (Instantly)" : "Email account"}</div>
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
          {e.provider === "instantly"
            ? "Open Integrations: add INSTANTLY_API_KEY to Backend/.env and choose the Instantly campaign."
            : "Add EMAIL_ADDRESS and EMAIL_APP_PASSWORD (a Gmail App Password) to Backend/.env, then restart the backend."}
        </p>
      )}
      {e.last_error && <p className="cx-hint cx-hint--bad">{e.last_error}</p>}
      {result?.ok && <p className="cx-hint cx-hint--ok">Sending and reading replies both work.</p>}
      {problem && <p className="cx-hint cx-hint--bad">{problem}</p>}
      {e.state !== "not_configured" && e.provider !== "instantly" && (
        <div className="cx-actions">
          <button className="btn-ghost" onClick={test} disabled={testing}>
            {testing ? <><i className="ti ti-loader-2 spin" /> Testing…</> : <><i className="ti ti-plug-connected" /> Test connection</>}
          </button>
        </div>
      )}
    </>
  );
}

/** The status chip shown in the navbar. */
export function ConnectionChips() {
  const { data, refresh } = useConnections();
  if (!data) return null;
  const { email: e } = data;
  return (
    <div className="cx-chips">
      <Chip icon="ti-mail" name="Email" look={EMAIL_LOOK[e.state] ?? EMAIL_LOOK.unknown}>
        <EmailPanel e={e} refresh={refresh} />
      </Chip>
    </div>
  );
}

/** A bar at the top of every page, only when something needs attention. */
export function ConnectionBanner() {
  const { data } = useConnections();
  if (!data) return null;
  const { whatsapp: w, email: e } = data;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  const bars = [];
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
  const toApprove = (w?.to_approve ?? 0) + e.to_approve;
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
