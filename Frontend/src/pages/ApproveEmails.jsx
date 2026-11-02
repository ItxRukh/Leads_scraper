import { useCallback, useEffect, useState } from "react";
import "./Leads.css";
import "../components/MessageApprovals.css";
import "./ApproveEmails.css";
import { readError } from "../utils/apiErrors";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const POLL_MS = 20000;

const CHECK = {
  valid:       { text: "Email valid",       cls: "ok" },
  "catch-all": { text: "Catch-all address", cls: "warn" },
  unknown:     { text: "Email not certain", cls: "warn" },
};

function Draft({ item, busy, onAction, onSave }) {
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(item.subject ?? "");
  const [body, setBody] = useState(item.body);
  const check = CHECK[item.email_check];

  async function save() {
    if (await onSave(item.id, subject, body)) setEditing(false);
  }

  return (
    <div className="ma-card">
      <div className="ma-top">
        <span className="ma-lead">{item.lead_name}</span>
        <span className="ma-to">{item.to}</span>
        {check && <span className={`ae-chip ae-chip--${check.cls}`}>{check.text}</span>}
        <span className="ma-step">{[item.industry, item.city].filter(Boolean).join(" · ")}</span>
      </div>

      <div className="ae-weak">
        <span className="ae-weak-label">Weakness</span>
        {item.weakness
          ? <span>{item.weakness}</span>
          : <span className="ae-weak-none">None found: this email has a general opening. Check it before approving.</span>}
        {item.weakness_edited && (
          <span className="ae-edited" title={item.weakness_note || "The AI corrected this after looking at the website"}>AI edited</span>
        )}
      </div>

      {editing ? (
        <div className="ma-edit">
          <input className="pf-input" value={subject} placeholder="Subject" onChange={e => setSubject(e.target.value)} />
          <textarea className="pf-textarea" rows={9} value={body} onChange={e => setBody(e.target.value)} />
        </div>
      ) : (
        <>
          {item.subject && <div className="ma-subject">{item.subject}</div>}
          <div className="ma-body">{item.body}</div>
        </>
      )}

      <div className="ma-actions">
        {editing ? (
          <>
            <button className="btn-primary" onClick={save} disabled={busy}><i className="ti ti-device-floppy" /> Save</button>
            <button className="btn-ghost" onClick={() => { setEditing(false); setSubject(item.subject ?? ""); setBody(item.body); }}>Cancel</button>
          </>
        ) : (
          <>
            <button className="btn-primary" onClick={() => onAction(item.id, "approve")} disabled={busy}>
              <i className="ti ti-check" /> Approve
            </button>
            <button className="btn-ghost" onClick={() => setEditing(true)}><i className="ti ti-pencil" /> Edit</button>
            <button className="btn-ghost" onClick={() => onAction(item.id, "rewrite")} disabled={busy}
                    title="Write a new email for this lead">
              <i className="ti ti-refresh" /> Rewrite
            </button>
            <button className="btn-ghost ae-discard" onClick={() => onAction(item.id, "discard")} disabled={busy}
                    title="Do not email this lead">
              <i className="ti ti-trash" /> Discard
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export default function ApproveEmails() {
  const [drafts, setDrafts] = useState([]);
  const [approved, setApproved] = useState([]);
  const [tab, setTab] = useState("draft");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, a] = await Promise.all([
        fetch(`${API}/outreach-messages?status=draft&limit=200`),
        fetch(`${API}/outreach-messages?status=approved&limit=200`),
      ]);
      if (d.ok) setDrafts((await d.json()).items);
      if (a.ok) setApproved((await a.json()).items);
    } catch {
      // keep what is on screen
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  async function call(path, init) {
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`${API}${path}`, init);
      if (!res.ok) { setError(await readError(res)); return false; }
      await load();
      return true;
    } catch {
      setError("Network error. Check your connection.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const onAction = (id, action) => call(`/outreach-messages/${id}/${action}`, { method: "POST" });
  const approveAll = () => window.confirm(`Approve all ${drafts.length} emails without reading them?`)
    && call("/outreach-messages/approve-all", { method: "POST" });
  const onSave = (id, subject, body) => call(`/outreach-messages/${id}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ subject, body }),
  });

  return (
    <div className="ae-page">
      <div className="ae-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "draft"} className={tab === "draft" ? "on" : ""} onClick={() => setTab("draft")}>
          To approve <b>{drafts.length}</b>
        </button>
        <button role="tab" aria-selected={tab === "approved"} className={tab === "approved" ? "on" : ""} onClick={() => setTab("approved")}>
          Approved <b>{approved.length}</b>
        </button>
      </div>

      {error && <div className="ma-error"><i className="ti ti-alert-circle" />{error}</div>}

      {tab === "draft" ? (
        <div className="table-panel">
          <div className="table-header">
            <div>
              <div className="panel-title">First emails waiting for you</div>
              <div className="panel-sub">
                One email per lead, written from the industry's knowledge base and the lead's weakness. The follow-ups are
                templates in GoHighLevel. Nothing is sent until you approve and release.
              </div>
            </div>
            <div className="table-actions">
              <button className="btn-primary" onClick={approveAll} disabled={busy || drafts.length === 0}>
                <i className="ti ti-checks" /> Approve all
              </button>
            </div>
          </div>
          <div className="ma-list">
            {loaded && drafts.length === 0 && (
              <div className="ae-empty">
                <i className="ti ti-mail-check" />
                <p>No emails to approve.</p>
                <span>Import leads and press "Check websites and emails": the first emails appear here.</span>
              </div>
            )}
            {drafts.map(item => (
              <Draft key={`${item.id}-${item.body.length}`} item={item} busy={busy} onAction={onAction} onSave={onSave} />
            ))}
          </div>
        </div>
      ) : (
        <div className="table-panel">
          <div className="table-header">
            <div>
              <div className="panel-title">Approved</div>
              <div className="panel-sub">Ready to go to GoHighLevel when released.</div>
            </div>
          </div>
          <div className="ma-list">
            {approved.length === 0 && <div className="ae-empty"><p>Nothing approved yet.</p></div>}
            {approved.map(item => (
              <div className="ma-card" key={item.id}>
                <div className="ma-top">
                  <span className="ma-lead">{item.lead_name}</span>
                  <span className="ma-to">{item.to}</span>
                  <span className="ma-step">{[item.industry, item.city].filter(Boolean).join(" · ")}</span>
                </div>
                <div className="ma-subject">{item.subject}</div>
                <div className="ma-body">{item.body}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
