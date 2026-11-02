import { useState, useEffect } from "react";
import "./LeadDetailModal.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ─── small local helpers (mirrors Leads.jsx so styling/labels stay consistent) ──
function emailBadge(status) {
  const map = {
    valid:       { cls: "email-valid",    label: "✓ Valid" },
    deliverable: { cls: "email-valid",    label: "✓ Valid" },
    "catch-all": { cls: "email-catchall", label: "~ Catch-all" },
    catch_all:   { cls: "email-catchall", label: "~ Catch-all" },
    invalid:     { cls: "email-invalid",  label: "✗ Invalid" },
    unknown:     { cls: "email-unknown",  label: "? Unknown" },
  };
  return map[status] ?? { cls: "email-unknown", label: status ?? "—" };
}
function formatDate(d) {
  if (!d) return "—";
  return new Date(d).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/**
 * Shared "eye view" modal — shows full details for a single lead.
 * Used from the Leads and Email outreach pages so the
 * eye icon actually does something everywhere instead of being dead
 * (Leads.jsx) or completely unwired (the other three pages).
 *
 * Props:
 *   leadId  — number|null. Modal is hidden when null.
 *   onClose — called when the user closes the modal.
 */
export default function LeadDetailModal({ leadId, onClose }) {
  const [lead,    setLead]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [replyText, setReplyText] = useState("");
  const [editing, setEditing] = useState(false);
  const [sendBusy, setSendBusy] = useState(false);
  const [sendNote, setSendNote] = useState({ ok: true, text: "" });
  const [blockBusy, setBlockBusy] = useState(false);
  const [blockNote, setBlockNote] = useState("");

  useEffect(() => {
    if (!leadId) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    setLead(null);
    setBlockNote("");
    setEditing(false);
    setSendNote({ ok: true, text: "" });

    fetch(`${API}/leads/${leadId}`)
      .then((res) => {
        if (!res.ok) throw new Error(res.status === 404 ? "Lead not found" : `HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => { if (!cancelled) { setLead(data); setReplyText(data.reply?.suggestion ?? ""); } })
      .catch((e) => { if (!cancelled) setError(e.message || "Failed to load lead"); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [leadId]);

  async function sendReply() {
    if (!window.confirm(`Send this reply to ${lead.name} now, through Instantly?`)) return;
    setSendBusy(true);
    setSendNote({ ok: true, text: "" });
    try {
      const res = await fetch(`${API}/leads/${lead.id}/reply`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: replyText }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { setSendNote({ ok: false, text: body.detail || "Could not send the reply." }); return; }
      const fresh = await fetch(`${API}/leads/${lead.id}`).then((r) => r.json());
      setLead(fresh);
      setEditing(false);
    } catch { setSendNote({ ok: false, text: "Network error." }); } finally { setSendBusy(false); }
  }

  async function blockLead() {
    if (!window.confirm(`Put ${lead.name} on the do-not-contact list? Nothing more will be sent to them, and they are removed from the Instantly campaign.`)) return;
    setBlockBusy(true);
    setBlockNote("");
    try {
      const res = await fetch(`${API}/leads/${lead.id}/do-not-contact`, { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { setBlockNote(body.detail || "Could not block this lead."); return; }
      const note = { removed: "Removed from the Instantly campaign.", not_in_instantly: "", not_configured: "Instantly is not set up, so nothing to remove there." }[body.instantly]
        ?? `Blocked here, but Instantly could not remove it (${body.instantly}). Remove it in Instantly by hand.`;
      setBlockNote(`Done. ${note}`.trim());
      const fresh = await fetch(`${API}/leads/${lead.id}`).then((r) => r.json());
      setLead(fresh);
    } catch { setBlockNote("Network error."); } finally { setBlockBusy(false); }
  }

  if (!leadId) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal ld-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <div className="modal-title">
              {loading ? "Loading lead…" : lead?.name ?? "Lead details"}
            </div>
            <div className="modal-sub">
              {loading ? " " : lead ? `Lead #${lead.id}` : "—"}
            </div>
          </div>
          <button className="modal-close" onClick={onClose}>
            <i className="ti ti-x" />
          </button>
        </div>

        <div className="modal-body ld-body">
          {loading && (
            <div className="ld-skeletons">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ height: 16, width: `${70 - i * 8}%` }} />
              ))}
            </div>
          )}

          {!loading && error && (
            <div className="ld-error">
              <i className="ti ti-alert-circle" />
              <span>{error}</span>
            </div>
          )}

          {!loading && !error && lead && (
            <>
              <div className="ld-top-row">
                <span className="stage-badge">{lead.niche ?? "Lead"}</span>
                {lead.do_not_contact && <span className="ld-dnc-badge"><i className="ti ti-ban" /> Do not contact</span>}
              </div>

              {lead.journey && (
                <div className="ld-section ld-journey">
                  <div className="ld-section-label">Where this lead is</div>
                  <div className="ld-journey-label">{lead.journey.label}</div>
                  {lead.journey.next && <div className="ld-journey-next">{lead.journey.next}</div>}
                </div>
              )}

              <div className="ld-section">
                <div className="ld-section-label">Contact</div>
                <div className="ld-grid">
                  <div className="ld-field">
                    <span className="ld-field-label">Phone</span>
                    <span className="ld-field-val">{lead.phone ?? "—"}</span>
                  </div>
                  <div className="ld-field">
                    <span className="ld-field-label">Email</span>
                    <span className="ld-field-val">
                      {lead.email ?? "—"}
                      {lead.email && (
                        <span className={`email-badge ld-inline-badge ${emailBadge(lead.email_status).cls}`}>
                          {emailBadge(lead.email_status).label}
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="ld-field">
                    <span className="ld-field-label">City</span>
                    <span className="ld-field-val">{lead.city ?? "—"}</span>
                  </div>
                  <div className="ld-field">
                    <span className="ld-field-label">Industry</span>
                    <span className="ld-field-val">{lead.niche ?? "—"}</span>
                  </div>
                  <div className="ld-field ld-field-wide">
                    <span className="ld-field-label">Address</span>
                    <span className="ld-field-val">{lead.address ?? "—"}</span>
                  </div>
                  <div className="ld-field ld-field-wide">
                    <span className="ld-field-label">Website</span>
                    <span className="ld-field-val">
                      {lead.website
                        ? <a href={lead.website} target="_blank" rel="noreferrer" className="biz-link">{lead.website}</a>
                        : "—"}
                    </span>
                  </div>
                </div>
              </div>

              <div className="ld-section">
                <div className="ld-section-label">Weakness</div>
                <div className="ld-audit-text">{lead.weakness || "None. The first email has a general opening."}</div>
                {lead.weakness_edited && (
                  <div className="ld-fix">
                    The AI corrected this after looking at the website.{lead.weakness_note ? ` ${lead.weakness_note}` : ""}
                    {lead.weakness_original ? ` Your list said: "${lead.weakness_original}".` : ""}
                  </div>
                )}
              </div>

              {lead.linkedin_found && (
                <div className="ld-section">
                  <div className="ld-section-label">Decision maker</div>
                  <div className="ld-grid">
                    <div className="ld-field">
                      <span className="ld-field-label">Name</span>
                      <span className="ld-field-val">{lead.linkedin_name ?? "—"}</span>
                    </div>
                    <div className="ld-field">
                      <span className="ld-field-label">Title</span>
                      <span className="ld-field-val">{lead.linkedin_title ?? "—"}</span>
                    </div>
                    {lead.linkedin_url && (
                      <div className="ld-field ld-field-wide">
                        <span className="ld-field-label">LinkedIn</span>
                        <span className="ld-field-val">
                          <a href={lead.linkedin_url} target="_blank" rel="noreferrer" className="biz-link">{lead.linkedin_url}</a>
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {lead.top_gaps?.length > 0 && (
                <div className="ld-section">
                  <div className="ld-section-label">Biggest opportunities found on the website</div>
                  <ul className="ld-gaps">
                    {lead.top_gaps.map((g) => (
                      <li key={g.key}>{g.label} <b>+{g.points}</b>{g.fix && <span className="ld-fix">We offer: {g.fix}</span>}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="ld-section">
                <div className="ld-section-label">Email address</div>
                <div className="ld-field">
                  <span className="ld-field-label">Email check</span>
                  <span className="ld-field-val">
                    {lead.email_check ? `${lead.email_check.status} (${lead.email_check.provider}${lead.email_check.detail ? `: ${lead.email_check.detail}` : ""})` : "Not checked yet"}
                  </span>
                </div>
              </div>

              {lead.reply && (
                <div className="ld-section">
                  <div className="ld-section-label">Their reply{lead.reply.label ? ` (${lead.reply.label.replace("_", " ")})` : ""}</div>
                  <div className="ld-audit-text">{lead.reply.text}</div>

                  {lead.reply.answered ? (
                    <div className="ld-fix">You answered on {formatDate(lead.reply.sent?.at)}. Continue the conversation in Instantly.</div>
                  ) : lead.reply.label === "stop" ? (
                    <div className="ld-fix">They asked us to stop. Nothing can be sent.</div>
                  ) : (
                    <>
                      <div className="ld-section-label" style={{ marginTop: 12 }}>
                        {lead.reply.suggestion ? "Suggested reply (from the knowledge base)" : "Your reply"}
                      </div>
                      {editing || !lead.reply.suggestion ? (
                        <textarea className="ld-reply-box" rows={7} value={replyText} onChange={(e) => setReplyText(e.target.value)}
                          placeholder="Write your reply…" />
                      ) : (
                        <div className="ld-audit-text">{replyText}</div>
                      )}
                      <div className="ld-reply-actions">
                        {lead.reply.suggestion && (
                          <button className="btn-ghost" disabled={sendBusy}
                            onClick={() => { if (editing) setReplyText(lead.reply.suggestion); setEditing(!editing); }}>
                            <i className={`ti ${editing ? "ti-rotate" : "ti-pencil"}`} /> {editing ? "Undo my edits" : "Edit"}
                          </button>
                        )}
                        <button className="btn-primary" disabled={sendBusy || !lead.reply.can_send || !replyText.trim()} onClick={sendReply}>
                          <i className="ti ti-send" /> {sendBusy ? "Sending…" : "Send now"}
                        </button>
                        {lead.reply.unibox_url && (
                          <a className="biz-link" href={lead.reply.unibox_url} target="_blank" rel="noreferrer">Open in Instantly</a>
                        )}
                      </div>
                      {!lead.reply.can_send && (
                        <div className="ld-fix">Sending from here needs the reply details from Instantly. For this one, answer in Instantly.</div>
                      )}
                      {sendNote.text && <div className={sendNote.ok ? "ld-fix" : "ld-error-text"}>{sendNote.text}</div>}
                    </>
                  )}
                </div>
              )}

              <div className="ld-section">
                <div className="ld-section-label">Timeline</div>
                <div className="ld-grid">
                  <div className="ld-field">
                    <span className="ld-field-label">Imported</span>
                    <span className="ld-field-val">{formatDate(lead.scraped_at)}</span>
                  </div>
                  <div className="ld-field">
                    <span className="ld-field-label">Website checked</span>
                    <span className="ld-field-val">{formatDate(lead.audited_at)}</span>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-ghost" onClick={onClose}>Close</button>
          {lead && !lead.do_not_contact && (
            <button className="btn-ghost ld-danger" disabled={blockBusy} onClick={blockLead}>
              <i className="ti ti-ban" /> {blockBusy ? "Blocking…" : "Do not contact"}
            </button>
          )}
          {blockNote && <span className="ld-fix">{blockNote}</span>}
        </div>
      </div>
    </div>
  );
}