import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import "./Leads.css";
import LeadDetailModal from "../components/LeadDetailModal";
import { downloadFile } from "../auth";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const LIMIT = 20;

// Colour of the "Where it is" badge: green for good news, red for blocked, amber for waiting on you
const JOURNEY_TONES = {
  in_ghl: "ok", replied: "ok", sent: "info", approved: "info", to_write: "info",
  draft: "warn", to_audit: "warn", to_check: "warn", no_email: "warn",
  dnc: "bad", bad_email: "bad", discarded: "muted",
};
const journeyTone = (key) => JOURNEY_TONES[key] ?? "muted";

function emailBadge(status) {
  const map = {
    valid:       { cls: "email-valid",    label: "✓ Valid" },
    "catch-all": { cls: "email-catchall", label: "~ Catch-all" },
    invalid:     { cls: "email-invalid",  label: "✗ Invalid" },
    unknown:     { cls: "email-unknown",  label: "? Not certain" },
  };
  return map[status] ?? { cls: "email-unknown", label: "Not checked" };
}

function Card({ label, value, sub, loading }) {
  return (
    <div className="stat-card">
      <div className="sc-label">{label}</div>
      <div className="sc-value">{loading ? "…" : value.toLocaleString()}</div>
      {sub && <div className="sc-sub">{sub}</div>}
    </div>
  );
}

export default function Leads() {
  const [searchParams] = useSearchParams();

  const [leads, setLeads] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState(null);

  const [search, setSearch] = useState("");
  const [niche, setNiche] = useState("");
  const [city, setCity] = useState("");
  const [batch, setBatch] = useState("");
  const [page, setPage] = useState(1);
  const [industries, setIndustries] = useState([]);
  const [batches, setBatches] = useState([]);
  const [viewLeadId, setViewLeadId] = useState(null);

  // Arriving via /admin/leads?lead=<id> (a notification click) opens that lead directly
  useEffect(() => {
    const id = searchParams.get("lead");
    if (id) setViewLeadId(Number(id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetch(`${API}/imports/industries`).then((r) => (r.ok ? r.json() : [])).then(setIndustries).catch(() => {});
    fetch(`${API}/imports`).then((r) => (r.ok ? r.json() : [])).then(setBatches).catch(() => {});
  }, []);

  const fetchLeads = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        skip: String((page - 1) * LIMIT), limit: String(LIMIT),
        ...(search && { search }), ...(niche && { niche }), ...(city && { city }), ...(batch && { batch }),
      });
      const res = await fetch(`${API}/leads?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setLeads(data.leads ?? []);
      setTotal(data.total ?? 0);
    } catch {
      setLeads([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, search, niche, city, batch]);

  const fetchSummary = useCallback(() => {
    fetch(`${API}/leads/status-summary`).then((r) => (r.ok ? r.json() : null)).then(setSummary).catch(() => {});
  }, []);

  useEffect(() => { fetchLeads(); }, [fetchLeads]);
  useEffect(() => { fetchSummary(); }, [fetchSummary]);

  const step = (...keys) => keys.reduce((n, k) => n + (summary?.by_step?.[k] ?? 0), 0);
  const totalPages = Math.max(1, Math.ceil(total / LIMIT));
  const closeModal = () => { setViewLeadId(null); fetchLeads(); fetchSummary(); };

  const change = (setter) => (e) => { setter(e.target.value); setPage(1); };

  return (
    <div className="lg-simple">
      <div className="lg-cards">
        <Card label="Leads" value={summary?.total ?? 0} loading={!summary} />
        <Card label="To check" value={step("to_audit", "to_check")} sub="websites and emails" loading={!summary} />
        <Card label="Drafts waiting" value={step("draft")} sub="on Approve emails" loading={!summary} />
        <Card label="Approved" value={step("approved")} sub="ready for GoHighLevel" loading={!summary} />
        <Card label="In GoHighLevel" value={step("in_ghl")} loading={!summary} />
      </div>

      <div className="table-panel">
        <div className="table-header">
          <div>
            <div className="panel-title">All leads</div>
            <div className="panel-sub">Newest import first, in the order you pasted them</div>
          </div>
          <div className="table-actions">
            <input className="filter-input lg-search" placeholder="Search name, email, phone…" value={search} onChange={change(setSearch)} />
            <select className="filter-select" value={niche} onChange={change(setNiche)}>
              <option value="">All industries</option>
              {industries.map((i) => <option key={i.key} value={i.label}>{i.label}</option>)}
            </select>
            <select className="filter-select" value={batch} onChange={change(setBatch)}>
              <option value="">All imports</option>
              {batches.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
            <input className="filter-input" placeholder="City…" value={city} onChange={change(setCity)} />
            <button className="btn-ghost" onClick={() => downloadFile(`/leads/export/csv`, "leads.csv").catch(() => {})}>
              <i className="ti ti-download" /> Export
            </button>
          </div>
        </div>

        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Business</th>
                <th>Industry / city</th>
                <th>Weakness</th>
                <th>Email</th>
                <th>Where it is</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {loading ? (
                Array.from({ length: 6 }).map((_, i) => (
                  <tr key={i} className="skeleton-row">
                    {Array.from({ length: 6 }).map((_, j) => <td key={j}><div className="skeleton" /></td>)}
                  </tr>
                ))
              ) : leads.length === 0 ? (
                <tr>
                  <td colSpan={6} className="empty-state">
                    <i className="ti ti-users-off" />
                    <p>No leads found</p>
                    <span>{total === 0 && !search && !niche && !city && !batch
                      ? <>Paste or upload leads on the <Link to="/admin/import">Import</Link> page.</>
                      : "Try different filters."}</span>
                  </td>
                </tr>
              ) : (
                leads.map((lead) => {
                  const eb = emailBadge(lead.email_check?.status);
                  return (
                    <tr key={lead.id}>
                      <td>
                        <div className="biz-name">{lead.name ?? "—"}</div>
                        <div className="biz-src">
                          {lead.linkedin_name || ""}
                          {lead.website && (
                            <a href={lead.website} target="_blank" rel="noreferrer" className="biz-link" onClick={(e) => e.stopPropagation()}>
                              <i className="ti ti-external-link" />
                            </a>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="city-text">{lead.niche ?? "—"}</div>
                        <div className="niche-text">{lead.city ?? "—"}</div>
                      </td>
                      <td>
                        {lead.weakness
                          ? <div className="lg-weak">{lead.weakness}
                              {lead.weakness_edited && <span className="lg-edited" title={lead.weakness_note || "The AI corrected this after looking at the website"}>AI edited</span>}
                            </div>
                          : <span className="dm-none">None</span>}
                      </td>
                      <td>
                        <div className="lg-email">{lead.email ?? "—"}</div>
                        {lead.email && <span className={`email-badge ${eb.cls}`}>{eb.label}</span>}
                      </td>
                      <td>
                        {lead.journey && (
                          <div className="journey" title={lead.journey.next}>
                            <span className={`journey-badge journey-badge--${journeyTone(lead.journey.key)}`}>{lead.journey.label}</span>
                            {lead.journey.next && <div className="journey-next">{lead.journey.next}</div>}
                          </div>
                        )}
                      </td>
                      <td>
                        <div className="row-actions">
                          <button className="btn-ghost btn-xs" title="View lead" onClick={() => setViewLeadId(lead.id)}>
                            <i className="ti ti-eye" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="pagination">
          <div className="pag-info">
            {total === 0 ? "No leads" : `Showing ${(page - 1) * LIMIT + 1}–${Math.min(page * LIMIT, total)} of ${total.toLocaleString()} leads`}
          </div>
          <div className="pag-buttons">
            <button className="pag-btn" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>‹</button>
            <button className="pag-btn active" disabled>{page}</button>
            <button className="pag-btn" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>›</button>
          </div>
        </div>
      </div>

      <LeadDetailModal leadId={viewLeadId} onClose={closeModal} />
    </div>
  );
}
