import { useCallback, useEffect, useState } from "react";
import "./MessageApprovals.css";
import { readError } from "../utils/apiErrors";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const CHANNEL_ICON = { email: "ti-mail", whatsapp: "ti-brand-whatsapp" };
const LABEL_TEXT = { interested: "Interested", question: "Has a question", not_now: "Not now" };
const POLL_MS = 20000;

function Draft({ item, busy, onAction, onSave }) {
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(item.subject ?? "");
  const [body, setBody] = useState(item.body);

  async function save() {
    if (await onSave(item.id, subject, body)) setEditing(false);
  }

  return (
    <div className="ma-card">
      <div className="ma-top">
        <span className="ma-channel"><i className={`ti ${CHANNEL_ICON[item.channel]}`} /> {item.channel === "email" ? "Email" : "WhatsApp"}</span>
        <span className="ma-lead">{item.lead_name}</span>
        <span className="ma-to">{item.to}</span>
        <span className="ma-step">{item.kind === "reply" ? "Answer" : item.step_label}</span>
      </div>

      {item.kind === "reply" && (
        <div className="ma-quote">
          <div className="ma-quote-head">
            They wrote{LABEL_TEXT[item.label] ? ` (${LABEL_TEXT[item.label]})` : ""}:
          </div>
          {item.their_message}
        </div>
      )}

      {editing ? (
        <div className="ma-edit">
          {item.channel === "email" && (
            <input className="pf-input" value={subject} placeholder="Subject" onChange={e => setSubject(e.target.value)} />
          )}
          <textarea className="pf-textarea" rows={7} value={body} onChange={e => setBody(e.target.value)} />
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
            <button className="btn-ghost" onClick={() => { setEditing(false); setSubject(item.subject ?? ""); setBody(item.body); }}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button className="btn-primary" onClick={() => onAction(item.id, "approve")} disabled={busy}>
              <i className="ti ti-check" /> Approve
            </button>
            <button className="btn-ghost" onClick={() => setEditing(true)}><i className="ti ti-pencil" /> Edit</button>
            <button className="btn-ghost" onClick={() => onAction(item.id, "discard")} disabled={busy}
                    title="Throw this away and write a new one">
              <i className="ti ti-refresh" /> Rewrite
            </button>
            <button className="btn-ghost" onClick={() => onAction(item.id, "skip")} disabled={busy}
                    title="Do not send this step to this lead">
              <i className="ti ti-player-skip-forward" /> Skip step
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/** Messages the system prepared that wait for a person's OK (channels in Assisted mode). */
export default function MessageApprovals({ onChanged }) {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/outreach-messages?status=draft`);
      if (res.ok) setItems((await res.json()).items);
    } catch {
      // keep what is on screen
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
      onChanged?.();
      return true;
    } catch {
      setError("Network error — check your connection.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  const onAction = (id, action) => call(`/outreach-messages/${id}/${action}`, { method: "POST" });
  const approveAll = () => call("/outreach-messages/approve-all", { method: "POST" });
  const onSave = (id, subject, body) => call(`/outreach-messages/${id}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ subject, body }),
  });

  if (items.length === 0 && !error) return null;

  return (
    <div className="table-panel">
      <div className="table-header">
        <div>
          <div className="panel-title">Waiting for your approval ({items.length})</div>
          <div className="panel-sub">
            Written by the AI for steps that are due. Approved messages go out at their planned time, spaced by the gaps in Sending rules.
          </div>
        </div>
        <div className="table-actions">
          <button className="btn-primary" onClick={approveAll} disabled={busy || items.length === 0}>
            <i className="ti ti-checks" /> Approve all
          </button>
        </div>
      </div>
      <div className="ma-list">
        {error && <div className="ma-error"><i className="ti ti-alert-circle" />{error}</div>}
        {items.map(item => (
          <Draft key={`${item.id}-${item.body.length}`} item={item} busy={busy} onAction={onAction} onSave={onSave} />
        ))}
      </div>
    </div>
  );
}
