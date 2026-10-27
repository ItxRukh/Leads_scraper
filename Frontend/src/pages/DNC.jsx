import { useState, useEffect, useCallback } from "react";
import { fmtDate } from "../utils/settings";
import "./DNC.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ─── helpers ──────────────────────────────────────────────────────────────────
function reasonInfo(reason) {
  const map = {
    manual:           { label: "Manual",       cls: "dr-manual",  icon: "ti-hand-stop" },
    unsubscribed:     { label: "Unsubscribed", cls: "dr-unsub",   icon: "ti-mail-off" },
    opt_out_keyword:  { label: "Auto opt-out", cls: "dr-auto",    icon: "ti-robot" },
  };
  if (map[reason]) return map[reason];
  // Auto-detected entries store the matched reply snippet after the prefix
  // (e.g. "opt_out_keyword: Auto-detected opt-out in email reply: '...'")
  if (reason?.startsWith("opt_out_keyword")) return map.opt_out_keyword;
  return { label: reason ?? "—", cls: "dr-manual", icon: "ti-ban" };
}

function isAutoDetected(reason) {
  return reason === "opt_out_keyword" || reason === "unsubscribed" || reason?.startsWith("opt_out_keyword");
}

function formatDate(iso) {
  return fmtDate(iso, { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

// ─── Stat card ────────────────────────────────────────────────────────────────
function StatCard({ icon, color, bg, label, value, sub, loading }) {
  return (
    <div className="stat-card">
      <div className="sc-top">
        <div className="sc-icon" style={{ background: bg }}>
          <i className={`ti ${icon}`} style={{ color }} />
        </div>
      </div>
      <div className="sc-value">
        {loading
          ? <span className="skeleton" style={{ width: 50, display: "inline-block" }} />
          : (value ?? 0).toLocaleString()
        }
      </div>
      <div className="sc-label">{label}</div>
      {sub && <div className="sc-sub">{sub}</div>}
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────
export default function DNC() {
  const [entries,   setEntries]   = useState([]);
  const [total,     setTotal]     = useState(0);
  const [loading,   setLoading]   = useState(true);
  const [scanning,  setScanning]  = useState(false);
  const [scanResult, setScanResult] = useState(null);

  // filters
  const [reasonF,   setReasonF]   = useState("");
  const [page,      setPage]      = useState(1);
  const LIMIT = 12;

  // add modal
  const [addOpen,   setAddOpen]   = useState(false);
  const [form,      setForm]      = useState({ email: "", phone: "", domain: "", reason: "manual" });
  const [adding,    setAdding]    = useState(false);
  const [addError,  setAddError]  = useState("");

  // confirm remove
  const [removeTarget, setRemoveTarget] = useState(null);
  const [removing,     setRemoving]     = useState(false);

  // ── fetch ────────────────────────────────────────────────────────────────────
  const fetchEntries = useCallback(async () => {
    setLoading(true);
    try {
      const res  = await fetch(`${API}/dnc`);
      const data = await res.json();
      let arr = Array.isArray(data) ? data : data.entries ?? [];

      // client-side filter (backend has no query params for dnc)
      if (reasonF) arr = arr.filter(e => e.reason === reasonF);

      setTotal(arr.length);
      const start = (page - 1) * LIMIT;
      setEntries(arr.slice(start, start + LIMIT));
    } catch (e) {
      console.error("fetchEntries:", e);
    } finally {
      setLoading(false);
    }
  }, [reasonF, page]);

  useEffect(() => { fetchEntries(); }, [fetchEntries]);

  // ── derived stats ─────────────────────────────────────────────────────────────
  const [allEntries, setAllEntries] = useState([]);
  useEffect(() => {
    fetch(`${API}/dnc`)
      .then(r => r.json())
      .then(d => setAllEntries(Array.isArray(d) ? d : d.entries ?? []))
      .catch(() => {});
  }, [scanning, addOpen, removeTarget]);

  const totalAll  = allEntries.length;
  const autoCount = allEntries.filter(e => isAutoDetected(e.reason)).length;
  const manualCount = allEntries.filter(e => e.reason === "manual").length;

  // ── scan replies ──────────────────────────────────────────────────────────────
  const handleScan = async () => {
    setScanning(true);
    setScanResult(null);
    try {
      const res  = await fetch(`${API}/dnc/scan-replies`, { method: "POST" });
      const data = await res.json();
      setScanResult(data.auto_added_to_dnc ?? 0);
      fetchEntries();
    } catch (e) {
      console.error("scan:", e);
    } finally {
      setScanning(false);
    }
  };

  // ── add to DNC ────────────────────────────────────────────────────────────────
  const handleAdd = async () => {
    if (!form.email && !form.phone && !form.domain) {
      setAddError("At least one of email, phone, or domain is required.");
      return;
    }
    setAdding(true);
    setAddError("");
    try {
      const res = await fetch(`${API}/dnc`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!res.ok) {
        const err = await res.json();
        setAddError(err.detail ?? "Something went wrong.");
        return;
      }
      setAddOpen(false);
      setForm({ email: "", phone: "", domain: "", reason: "manual" });
      fetchEntries();
    } catch (e) {
      setAddError("Network error — check your connection.");
    } finally {
      setAdding(false);
    }
  };

  // ── remove from DNC ───────────────────────────────────────────────────────────
  const handleRemove = async () => {
    if (!removeTarget) return;
    setRemoving(true);
    try {
      const params = new URLSearchParams();
      if (removeTarget.email)  params.set("email",  removeTarget.email);
      if (removeTarget.phone)  params.set("phone",  removeTarget.phone);
      if (removeTarget.domain) params.set("domain", removeTarget.domain);
      await fetch(`${API}/dnc?${params}`, { method: "DELETE" });
      setRemoveTarget(null);
      fetchEntries();
    } catch (e) {
      console.error("remove:", e);
    } finally {
      setRemoving(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / LIMIT));

  return (
    <div className="dnc-page">

      {/* ── stat cards ── */}
      <div className="stat-grid">
        <StatCard
          icon="ti-ban"
          color="#f87171" bg="rgba(248,113,113,0.1)"
          label="Total blocked"
          value={totalAll}
          sub="Emails, phones &amp; domains"
          loading={loading}
        />
        <StatCard
          icon="ti-robot"
          color="#e8622a" bg="rgba(232,98,42,0.08)"
          label="Auto-detected"
          value={autoCount}
          sub="Opt-out keywords &amp; unsubs"
          loading={loading}
        />
        <StatCard
          icon="ti-hand-stop"
          color="#f59e0b" bg="rgba(245,158,11,0.08)"
          label="Manually added"
          value={manualCount}
          sub="Added by you"
          loading={loading}
        />
      </div>

      {/* ── scan banner ── */}
      <div className="scan-banner">
        <div className="scan-banner-left">
          <div className="scan-icon">
            <i className="ti ti-robot" />
          </div>
          <div>
            <div className="scan-title">Auto scan replies</div>
            <div className="scan-sub">
              Scans all email &amp; WhatsApp replies for opt-out keywords
              (stop, remove, unsubscribe, not interested…) and auto-adds them to DNC.
            </div>
          </div>
        </div>
        <div className="scan-banner-right">
          {scanResult !== null && (
            <div className="scan-result">
              <i className="ti ti-check" />
              {scanResult === 0
                ? "No new opt-outs found"
                : `${scanResult} new entr${scanResult === 1 ? "y" : "ies"} added`}
            </div>
          )}
          <button
            className={`btn-primary ${scanning ? "btn-scanning" : ""}`}
            onClick={handleScan}
            disabled={scanning}
          >
            {scanning
              ? <><i className="ti ti-loader-2 spin" /> Scanning...</>
              : <><i className="ti ti-scan" /> Scan replies now</>
            }
          </button>
        </div>
      </div>

      {/* ── table ── */}
      <div className="table-panel">
        <div className="table-header">
          <div>
            <div className="panel-title">DNC list</div>
            <div className="panel-sub">{total.toLocaleString()} blocked entries — none of these will ever be contacted</div>
          </div>
          <div className="table-actions">
            <select className="filter-select" value={reasonF} onChange={e => { setReasonF(e.target.value); setPage(1); }}>
              <option value="">All reasons</option>
              <option value="manual">Manual</option>
              <option value="unsubscribed">Unsubscribed</option>
              <option value="opt_out_keyword">Auto opt-out</option>
            </select>
            <button className="btn-ghost" onClick={() => window.open(`${API}/dnc/export`, "_blank")}>
              <i className="ti ti-download" /> <span>Export</span>
            </button>
            <button className="btn-primary btn-danger" onClick={() => { setAddOpen(true); setAddError(""); }}>
              <i className="ti ti-plus" /> <span>Add to DNC</span>
            </button>
          </div>
        </div>

        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Phone</th>
                <th>Domain</th>
                <th>Reason</th>
                <th>Added at</th>
                <th>Remove</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 6 }).map((_, i) => (
                    <tr key={i} className="skeleton-row">
                      {Array.from({ length: 6 }).map((_, j) => (
                        <td key={j}><div className="skeleton" /></td>
                      ))}
                    </tr>
                  ))
                : entries.length === 0
                ? (
                    <tr>
                      <td colSpan={6} className="empty-state">
                        <i className="ti ti-ban" style={{ color: "#f87171" }} />
                        <p>DNC list is empty</p>
                        <span>No one is blocked yet — scan replies or add manually</span>
                      </td>
                    </tr>
                  )
                : entries.map(entry => {
                    const ri = reasonInfo(entry.reason);
                    return (
                      <tr key={entry.id}>
                        <td>
                          <div className="dnc-val">
                            {entry.email
                              ? <><i className="ti ti-mail" />{entry.email}</>
                              : <span className="dim">—</span>}
                          </div>
                        </td>
                        <td>
                          <div className="dnc-val">
                            {entry.phone
                              ? <><i className="ti ti-phone" />{entry.phone}</>
                              : <span className="dim">—</span>}
                          </div>
                        </td>
                        <td>
                          <div className="dnc-val">
                            {entry.domain
                              ? <><i className="ti ti-world" />{entry.domain}</>
                              : <span className="dim">—</span>}
                          </div>
                        </td>
                        <td>
                          <span className={`reason-badge ${ri.cls}`}>
                            <i className={`ti ${ri.icon}`} />
                            {ri.label}
                          </span>
                        </td>
                        <td className="date-cell">{formatDate(entry.added_at)}</td>
                        <td>
                          <button
                            className="btn-remove"
                            onClick={() => setRemoveTarget(entry)}
                            title="Remove from DNC"
                          >
                            <i className="ti ti-trash" />
                          </button>
                        </td>
                      </tr>
                    );
                  })
              }
            </tbody>
          </table>
        </div>

        {/* pagination */}
        <div className="pagination">
          <div className="pag-info">
            Showing {Math.min((page - 1) * LIMIT + 1, total)}–{Math.min(page * LIMIT, total)} of {total.toLocaleString()} entries
          </div>
          <div className="pag-buttons">
            <button className="pag-btn" disabled={page === 1} onClick={() => setPage(p => p - 1)}>‹</button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const p = totalPages <= 5 ? i + 1
                : page <= 3 ? i + 1
                : page >= totalPages - 2 ? totalPages - 4 + i
                : page - 2 + i;
              return (
                <button
                  key={p}
                  className={`pag-btn ${p === page ? "active" : ""}`}
                  onClick={() => setPage(p)}
                >{p}</button>
              );
            })}
            <button className="pag-btn" disabled={page === totalPages} onClick={() => setPage(p => p + 1)}>›</button>
          </div>
        </div>
      </div>

      {/* ── add modal ── */}
      {addOpen && (
        <div className="modal-overlay" onClick={() => setAddOpen(false)}>
          <div className="modal modal--sm" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <div className="modal-title">Add to DNC list</div>
                <div className="modal-sub">This contact will never be reached out to again</div>
              </div>
              <button className="modal-close" onClick={() => setAddOpen(false)}>
                <i className="ti ti-x" />
              </button>
            </div>
            <div className="modal-body">
              <div className="form-group">
                <label>Email address</label>
                <input
                  type="email"
                  placeholder="e.g. supplier@example.com"
                  value={form.email}
                  onChange={e => setForm(f => ({ ...f, email: e.target.value }))}
                />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Phone number</label>
                  <input
                    type="text"
                    placeholder="e.g. +1 305 000 0000"
                    value={form.phone}
                    onChange={e => setForm(f => ({ ...f, phone: e.target.value }))}
                  />
                </div>
                <div className="form-group">
                  <label>Domain</label>
                  <input
                    type="text"
                    placeholder="e.g. example.com"
                    value={form.domain}
                    onChange={e => setForm(f => ({ ...f, domain: e.target.value }))}
                  />
                </div>
              </div>
              <div className="form-group">
                <label>Reason</label>
                <select
                  className="filter-select"
                  style={{ width: "100%", padding: "10px 14px" }}
                  value={form.reason}
                  onChange={e => setForm(f => ({ ...f, reason: e.target.value }))}
                >
                  <option value="manual">Manual — added by me</option>
                  <option value="unsubscribed">Unsubscribed</option>
                  <option value="opt_out_keyword">Opt-out keyword in reply</option>
                </select>
              </div>

              {addError && (
                <div className="modal-error">
                  <i className="ti ti-alert-circle" />
                  {addError}
                </div>
              )}

              <div className="modal-note" style={{ background: "rgba(248,113,113,0.06)", borderColor: "rgba(248,113,113,0.15)" }}>
                <i className="ti ti-info-circle" style={{ color: "#f87171" }} />
                <span>
                  Fill at least one field. If a matching lead exists in the database,
                  their pipeline stage will automatically be set to DNC.
                </span>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn-ghost" onClick={() => setAddOpen(false)}>Cancel</button>
              <button className="btn-primary btn-danger" onClick={handleAdd} disabled={adding}>
                {adding
                  ? <><i className="ti ti-loader-2 spin" /> Adding...</>
                  : <><i className="ti ti-ban" /> Add to DNC</>
                }
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── confirm remove modal ── */}
      {removeTarget && (
        <div className="modal-overlay" onClick={() => setRemoveTarget(null)}>
          <div className="modal modal--xs" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <div className="modal-title">Remove from DNC?</div>
                <div className="modal-sub">This contact will be allowed to receive outreach again</div>
              </div>
              <button className="modal-close" onClick={() => setRemoveTarget(null)}>
                <i className="ti ti-x" />
              </button>
            </div>
            <div className="modal-body">
              <div className="remove-confirm-info">
                {removeTarget.email  && <div><i className="ti ti-mail"  />{removeTarget.email}</div>}
                {removeTarget.phone  && <div><i className="ti ti-phone" />{removeTarget.phone}</div>}
                {removeTarget.domain && <div><i className="ti ti-world" />{removeTarget.domain}</div>}
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn-ghost" onClick={() => setRemoveTarget(null)}>Cancel</button>
              <button className="btn-primary" onClick={handleRemove} disabled={removing}>
                {removing
                  ? <><i className="ti ti-loader-2 spin" /> Removing...</>
                  : <><i className="ti ti-trash" /> Yes, remove</>
                }
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}