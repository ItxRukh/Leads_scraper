import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import "./LeadDetailModal.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ─── small local helpers (mirrors Leads.jsx so styling/labels stay consistent) ──
function tierClass(tier) {
  const map = { hot: "tier-hot", warm: "tier-warm", cold: "tier-cold" };
  return map[tier] ?? "tier-unscored";
}
function tierLabel(tier) {
  const map = { hot: "🔥 Hot", warm: "Warm", cold: "Cold" };
  return map[tier] ?? "Unscored";
}
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
function stageLabel(s) {
  if (!s) return "—";
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
// Visual meta (icon + color) for each possible outreach channel status —
// mirrors the status strings returned by Backend/utils/outreach_reason.py
function channelMeta(status) {
  const map = {
    blocked:          { color: "#f87171", icon: "ti-ban" },
    replied:          { color: "#4ade80", icon: "ti-message-circle-2" },
    sent:             { color: "#60a5fa", icon: "ti-clock" },
    waiting:          { color: "#60a5fa", icon: "ti-clock" },
    due:              { color: "#fbbf24", icon: "ti-alert-circle" },
    ready:            { color: "#4ade80", icon: "ti-circle-check" },
    no_email:         { color: "#fbbf24", icon: "ti-mail-off" },
    no_phone:         { color: "#fbbf24", icon: "ti-phone-off" },
    unverified:       { color: "#fbbf24", icon: "ti-alert-triangle" },
    score_low:        { color: "#fbbf24", icon: "ti-trending-down" },
    stuck_no_trigger: { color: "#fbbf24", icon: "ti-alert-triangle" },
    not_needed:       { color: "rgba(var(--ov),0.35)", icon: "ti-minus" },
    called:           { color: "#60a5fa", icon: "ti-phone-check" },
  };
  return map[status] ?? { color: "rgba(var(--ov),0.3)", icon: "ti-help-circle" };
}
function formatDate(d) {
  if (!d) return "—";
  return new Date(d).toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/**
 * Shared "eye view" modal — shows full details for a single lead.
 * Used from Leads, EmailOutreach, CallLogs, and WhatsApp pages so the
 * eye icon actually does something everywhere instead of being dead
 * (Leads.jsx) or completely unwired (the other three pages).
 *
 * Props:
 *   leadId  — number|null. Modal is hidden when null.
 *   onClose — called when the user closes the modal.
 */
export default function LeadDetailModal({ leadId, onClose }) {
  const navigate = useNavigate();
  const [lead,    setLead]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  // Feature 5 — Manual "Contact Now" buttons: per-channel busy/result state
  // so each button (email/sms/call) shows its own spinner + outcome
  // independently, without needing three separate booleans.
  const [contactState, setContactState] = useState({}); // { email: "sending"|"sent"|"error", sms: ..., call: ... }

  useEffect(() => {
    if (!leadId) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    setLead(null);
    setContactState({});

    fetch(`${API}/leads/${leadId}`)
      .then((res) => {
        if (!res.ok) throw new Error(res.status === 404 ? "Lead not found" : `HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => { if (!cancelled) setLead(data); })
      .catch((e) => { if (!cancelled) setError(e.message || "Failed to load lead"); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [leadId]);

  async function handleContactNow(channel) {
    if (!lead) return;
    setContactState((s) => ({ ...s, [channel]: "sending" }));
    const endpoint = {
      email: "/pipeline/send-emails",
      sms:   "/pipeline/whatsapp",
      call:  "/pipeline/vapi-calls",
    }[channel];
    const body = channel === "email"
      ? { lead_id: lead.id, dry_run: false }
      : channel === "sms"
      ? { lead_id: lead.id, trigger: "manual", dry_run: false }
      : { lead_id: lead.id, dry_run: false };

    try {
      const res = await fetch(`${API}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setContactState((s) => ({ ...s, [channel]: "sent" }));
    } catch {
      setContactState((s) => ({ ...s, [channel]: "error" }));
    }
    // Reset the "sent"/"error" pill back to idle after a few seconds so the
    // button is clickable again for another send.
    setTimeout(() => {
      setContactState((s) => ({ ...s, [channel]: undefined }));
    }, 4000);
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
                <span className={`tier-badge ${tierClass(lead.tier)}`}>{tierLabel(lead.tier)}</span>
                <span className="stage-badge">{stageLabel(lead.pipeline_stage)}</span>
                {lead.do_not_contact && <span className="ld-dnc-badge"><i className="ti ti-ban" /> Do not contact</span>}
              </div>

              <div className="ld-score-row">
                <div className="ld-score-num">{lead.final_score ?? 0}</div>
                <div className="ld-score-sub">
                  <div>Final score</div>
                  <div className="ld-score-breakdown">
                    base {lead.base_score ?? 0} + bonus {lead.bonus_score ?? 0}
                  </div>
                </div>
              </div>

              {lead.score_incomplete && (
                <div className="ld-incomplete-banner">
                  <i className="ti ti-alert-triangle" />
                  <span>{lead.score_incomplete_reason || "Score incomplete — some signals couldn't be checked."}</span>
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
                    <span className="ld-field-label">Niche</span>
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
                <div className="ld-section-label">Contact now</div>
                {lead.do_not_contact ? (
                  <div className="ld-incomplete-banner">
                    <i className="ti ti-ban" />
                    <span>This lead is on the Do-Not-Contact list — manual outreach is disabled.</span>
                  </div>
                ) : (
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {[
                      { channel: "email", icon: "ti-mail",         label: "Email now", enabled: !!lead.email },
                      { channel: "sms",   icon: "ti-message-2",    label: "SMS now",   enabled: !!lead.phone },
                      { channel: "call",  icon: "ti-phone-call",   label: "Call now",  enabled: !!lead.phone },
                    ].map(({ channel, icon, label, enabled }) => {
                      const state = contactState[channel];
                      return (
                        <button
                          key={channel}
                          className="btn-ghost"
                          disabled={!enabled || state === "sending"}
                          onClick={() => handleContactNow(channel)}
                          title={enabled ? "" : `No ${channel === "email" ? "email" : "phone"} on file for this lead`}
                          style={{ display: "flex", alignItems: "center", gap: 6 }}
                        >
                          {state === "sending" ? (
                            <><i className="ti ti-loader-2" /> Sending…</>
                          ) : state === "sent" ? (
                            <><i className="ti ti-circle-check" style={{ color: "#4ade80" }} /> Sent</>
                          ) : state === "error" ? (
                            <><i className="ti ti-alert-triangle" style={{ color: "#f87171" }} /> Failed — retry</>
                          ) : (
                            <><i className={`ti ${icon}`} /> {label}</>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
                <div style={{ fontSize: 11, color: "#888", marginTop: 6 }}>
                  Bypasses automatic scoring/timing rules and sends right away.
                </div>
              </div>

              {lead.outreach_status && (
                <div className="ld-section">
                  <div className="ld-section-label">Outreach status</div>
                  <div className="ld-outreach-list">
                    {[
                      { key: "email", label: "Email", icon: "ti-mail" },
                      { key: "sms",   label: "SMS",   icon: "ti-message-2" },
                      { key: "call",  label: "Call",  icon: "ti-phone" },
                    ].map(({ key, label, icon }) => {
                      const ch = lead.outreach_status[key];
                      if (!ch) return null;
                      const meta = channelMeta(ch.status);
                      return (
                        <div key={key} className="ld-outreach-row">
                          <div className="ld-outreach-channel">
                            <i className={`ti ${icon}`} />
                            <span>{label}</span>
                          </div>
                          <div className="ld-outreach-reason">
                            <i className={`ti ${meta.icon}`} style={{ color: meta.color }} />
                            <span>{ch.reason}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

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

              {lead.audit_summary && (
                <div className="ld-section">
                  <div className="ld-section-label">Audit summary</div>
                  <div className="ld-audit-text">{lead.audit_summary}</div>
                </div>
              )}

              <div className="ld-section">
                <div className="ld-section-label">Timeline</div>
                <div className="ld-grid">
                  <div className="ld-field">
                    <span className="ld-field-label">Scraped</span>
                    <span className="ld-field-val">{formatDate(lead.scraped_at)}</span>
                  </div>
                  <div className="ld-field">
                    <span className="ld-field-label">Audited</span>
                    <span className="ld-field-val">{formatDate(lead.audited_at)}</span>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-ghost" onClick={onClose}>Close</button>
          {lead && (
            <button
              className="btn-primary"
              onClick={() => navigate(`/admin/pipeline?highlight=${lead.id}`)}
            >
              <i className="ti ti-list-check" /> View in pipeline
            </button>
          )}
        </div>
      </div>
    </div>
  );
}