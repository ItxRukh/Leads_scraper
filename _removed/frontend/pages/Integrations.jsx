import { useCallback, useEffect, useState } from "react";
import "./Integrations.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function call(path, init) {
  const res = await fetch(`${API}${path}`, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Something went wrong (${res.status})`);
  return body;
}
const post = (path, data) => call(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: data ? JSON.stringify(data) : undefined,
});

function Dot({ ok }) {
  return <span className={`ig-dot ig-dot--${ok ? "ok" : "bad"}`} aria-label={ok ? "set" : "not set"} />;
}

function Row({ label, ok, children }) {
  return (
    <div className="ig-row">
      <span className="ig-row-label">{ok !== undefined && <Dot ok={ok} />}{label}</span>
      <span className="ig-row-value">{children}</span>
    </div>
  );
}

function Copy({ text }) {
  const [done, setDone] = useState(false);
  return (
    <button className="ig-link" onClick={() => navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); })}>
      {done ? "Copied" : "Copy"}
    </button>
  );
}

export default function Integrations() {
  const [status, setStatus] = useState(null);
  const [campaigns, setCampaigns] = useState(null);
  const [template, setTemplate] = useState(null);
  const [company, setCompany] = useState({ name: "", address: "" });
  const [note, setNote] = useState({ kind: "", text: "" });
  const [busy, setBusy] = useState("");

  const say = (kind, text) => setNote({ kind, text });

  const load = useCallback(async () => {
    try {
      const s = await call("/integrations/status");
      setStatus(s);
      setCompany((c) => (c.address || c.name ? c : { name: s.company.name, address: s.company.address }));
    } catch (e) { say("bad", e.message); }
  }, []);

  useEffect(() => { load(); call("/integrations/instantly/template").then(setTemplate).catch(() => {}); }, [load]);

  async function run(name, fn, okText) {
    setBusy(name);
    setNote({ kind: "", text: "" });
    try {
      const result = await fn();
      if (okText) say("ok", typeof okText === "function" ? okText(result) : okText);
      await load();
      return result;
    } catch (e) { say("bad", e.message); } finally { setBusy(""); }
  }

  if (!status) return <div className="ig-page"><p className="ig-help">Loading…</p></div>;
  const s = status;

  return (
    <div className="ig-page">
      {note.text && <div className={`ig-note ig-note--${note.kind}`} role="status">{note.text}</div>}

      {/* ── sending ── */}
      <section className="ig-card">
        <h2>Sending</h2>
        <p className="ig-help">
          <b>Instantly</b> sends the emails (it rotates mailboxes, warms them up and stops a lead when they reply). We write and approve
          the text, then hand each lead to an Instantly campaign with all its emails. <b>Gmail</b> sends one email at a time from the account in <code>.env</code>.
        </p>
        <div className="ig-seg" role="group" aria-label="Sending method">
          {[["instantly", "Instantly"], ["smtp", "Gmail (SMTP)"]].map(([value, label]) => (
            <button key={value} className={s.provider === value ? "on" : ""} disabled={busy !== ""}
              onClick={() => run("provider", () => post("/integrations/provider", { provider: value }))}>{label}</button>
          ))}
        </div>
        {s.blockers.length > 0 ? (
          <ul className="ig-blockers">{s.blockers.map((b) => <li key={b}><i className="ti ti-alert-triangle" /> {b}</li>)}</ul>
        ) : s.provider === "instantly" ? <p className="ig-ready"><i className="ti ti-circle-check" /> Ready: approved leads are handed to Instantly.</p> : null}
        <button className="ig-btn ig-btn--ghost" disabled={busy !== ""}
          onClick={() => run("send", () => post("/integrations/send-now"),
            (r) => `Handed over: ${r.sent ?? 0}, failed: ${r.failed ?? 0}${r.held ? `, held back: ${r.held}` : ""}.`)}>
          Send approved emails now
        </button>
      </section>

      {/* ── instantly ── */}
      <section className="ig-card">
        <h2>Instantly</h2>
        <Row label="API key (INSTANTLY_API_KEY in Backend/.env)" ok={s.instantly.configured}>{s.instantly.configured ? "Set" : "Not set"}</Row>
        <Row label="Campaign" ok={!!s.instantly.campaign_id}>
          {s.instantly.campaign_id ? <code>{s.instantly.campaign_id}</code> : "Not chosen"}
        </Row>
        <div className="ig-inline">
          <button className="ig-btn ig-btn--ghost" disabled={busy !== "" || !s.instantly.configured}
            onClick={() => run("campaigns", async () => setCampaigns(await call("/integrations/instantly/campaigns")))}>
            {busy === "campaigns" ? "Loading…" : "Load my campaigns"}
          </button>
          {campaigns && (
            <select value={s.instantly.campaign_id} onChange={(e) => run("campaign", () => post("/integrations/instantly/campaign", { campaign_id: e.target.value }), "Campaign saved.")}>
              <option value="" disabled>Choose a campaign</option>
              {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.status_label})</option>)}
            </select>
          )}
        </div>

        <h3>Campaign steps to create in Instantly</h3>
        <p className="ig-help">One step per email. Each lead's own text goes in through these variables. Keep "stop on reply" and the unsubscribe option on in the campaign settings, and turn open and link tracking off.</p>
        {template && (
          <table className="ig-table">
            <thead><tr><th>Step</th><th>Wait</th><th>Subject</th><th>Body</th></tr></thead>
            <tbody>
              {template.steps.map((t) => (
                <tr key={t.step}>
                  <td>{t.step}</td>
                  <td>{t.wait_days === 0 ? "—" : `${t.wait_days} days after the previous`}</td>
                  <td><code>{t.subject}</code> <Copy text={t.subject} /></td>
                  <td><code>{t.body}</code> <Copy text={t.body} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h3>Events from Instantly</h3>
        <p className="ig-help">
          Replies, bounces and unsubscribes come back to this app. Instantly needs a public https address:
          set <code>PUBLIC_BASE_URL</code> and <code>INSTANTLY_WEBHOOK_SECRET</code> in <code>Backend/.env</code> (a domain, or an ngrok tunnel while testing), then register.
        </p>
        <Row label="Public address (PUBLIC_BASE_URL)" ok={s.public_base_url.startsWith("https://")}>{s.public_base_url || "Not set"}</Row>
        <Row label="Secret (INSTANTLY_WEBHOOK_SECRET)" ok={s.instantly.webhook_secret_set}>{s.instantly.webhook_secret_set ? "Set" : "Not set"}</Row>
        <button className="ig-btn ig-btn--ghost" disabled={busy !== "" || !s.instantly.configured}
          onClick={() => run("webhooks", () => post("/integrations/instantly/webhooks", {}),
            (r) => `Registered ${r.created.length} event${r.created.length === 1 ? "" : "s"}${r.failed.length ? `; ${r.failed.length} failed: ${r.failed[0].error}` : ""}.`)}>
          Register webhooks in Instantly
        </button>
      </section>

      {/* ── company ── */}
      <section className="ig-card">
        <h2>Company details in every email</h2>
        <p className="ig-help">US anti-spam law requires your real postal address in commercial email. It is added to the bottom of every email handed to Instantly, and nothing is handed over until it is filled in.</p>
        <label className="ig-field">Company name
          <input value={company.name} maxLength={120} onChange={(e) => setCompany({ ...company, name: e.target.value })} />
        </label>
        <label className="ig-field">Postal address
          <input value={company.address} maxLength={300} placeholder="Street, city, state ZIP"
            onChange={(e) => setCompany({ ...company, address: e.target.value })} />
        </label>
        <button className="ig-btn" disabled={busy !== ""}
          onClick={() => run("company", () => post("/integrations/company", { company_name: company.name, address: company.address }), "Saved.")}>
          Save
        </button>
      </section>

      {/* ── verification ── */}
      <section className="ig-card">
        <h2>Email verification</h2>
        <p className="ig-help">Every address gets a free check (syntax, throwaway domains, mail server). With a paid verifier key the mailbox itself is checked too, only for Hot and Warm leads.</p>
        <Row label="Paid verifier (MILLIONVERIFIER_API_KEY in Backend/.env)" ok={!!s.verification.paid_provider}>
          {s.verification.paid_provider ? s.verification.paid_provider : "Not set: free checks only"}
        </Row>
        <label className="ig-check">
          <input type="checkbox" checked={s.verification.verify_before_send} disabled={busy !== ""}
            onChange={(e) => run("verify", () => post("/integrations/company", { verify_before_send: e.target.checked }))} />
          Check every address before a lead is handed to Instantly
        </label>
        <label className="ig-check">
          <input type="checkbox" checked={s.verification.send_to_catch_all} disabled={busy !== ""}
            onChange={(e) => run("catchall", () => post("/integrations/company", { send_to_catch_all: e.target.checked }))} />
          Also send to catch-all addresses (servers that accept any address; more bounces)
        </label>
      </section>

      {/* ── ghl ── */}
      <section className="ig-card">
        <h2>GoHighLevel</h2>
        <p className="ig-help">
          A lead that replies with interest (or a real question) is sent to a GoHighLevel workflow, with the reply, the audit findings and a suggested answer.
          SMS, calls and booking continue there. Create a workflow with an <b>Inbound Webhook</b> trigger and put its URL in <code>GHL_WEBHOOK_URL</code>.
        </p>
        <Row label="Webhook URL (GHL_WEBHOOK_URL)" ok={s.ghl.configured}>{s.ghl.configured ? "Set" : "Not set"}</Row>
        <Row label="Sent to GoHighLevel when a reply is">{s.ghl.push_labels.join(", ") || "—"}</Row>
        <button className="ig-btn ig-btn--ghost" disabled={busy !== "" || !s.ghl.configured}
          onClick={() => run("ghl", () => post("/integrations/ghl/test"),
            (r) => (r.status === "sent" ? "A test contact was sent. Open the workflow in GoHighLevel and fetch the sample request to map the fields." : `Not sent: ${r.error}`))}>
          Send a test contact
        </button>
      </section>
    </div>
  );
}
