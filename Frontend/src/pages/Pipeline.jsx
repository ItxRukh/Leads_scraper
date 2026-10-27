import { useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { fmtDate } from "../utils/settings";
import {
  FunnelChart, Funnel, LabelList, Tooltip,
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Cell,
} from "recharts";
import "./Pipeline.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ─── Stage config — WhatsApp → SMS ───────────────────────────────────────────
const STAGES = [
  { key: "scraped",           label: "Scraped",           color: "#6b7280", icon: "ti-database"        },
  { key: "linkedin_enriched", label: "LinkedIn",          color: "#3b82f6", icon: "ti-brand-linkedin"  },
  { key: "audited",           label: "Audited",           color: "#8b5cf6", icon: "ti-search"          },
  { key: "email_verified",    label: "Email verified",    color: "#f59e0b", icon: "ti-mail-check"      },
  { key: "email_sent",        label: "Email sent",        color: "#e8622a", icon: "ti-mail"            },
  { key: "email_opened",      label: "Email opened",      color: "#fb923c", icon: "ti-mail-opened"     },
  { key: "replied",           label: "Replied",           color: "#4ade80", icon: "ti-message-check"   },
  { key: "call_placed",       label: "Call placed",       color: "#22d3ee", icon: "ti-phone"           },
  { key: "whatsapp_sent",     label: "SMS",               color: "#3b82f6", icon: "ti-message"         },  // WA → SMS
  { key: "demo_booked",       label: "Demo booked",       color: "#a78bfa", icon: "ti-calendar-check"  },
  { key: "onboarded",         label: "Onboarded ✅",      color: "#4ade80", icon: "ti-check-circle"    },
  { key: "rejected",          label: "Rejected",          color: "#f87171", icon: "ti-x"               },
  { key: "dnc",               label: "DNC",               color: "#374151", icon: "ti-ban"             },
];

const STAGE_MAP = Object.fromEntries(STAGES.map(s => [s.key, s]));

const KANBAN_STAGES = STAGES.filter(s =>
  !["scraped", "linkedin_enriched", "audited", "email_verified"].includes(s.key)
);

function tierColor(tier) {
  return tier === "hot" ? "#e8622a" : tier === "warm" ? "#f59e0b" : "#3b82f6";
}
function formatDate(iso) {
  return fmtDate(iso, { month: "short", day: "numeric" });
}

function StatCard({ icon, color, bg, label, value, sub, loading }) {
  return (
    <div className="stat-card">
      <div className="sc-top">
        <div className="sc-icon" style={{ background: bg }}>
          <i className={`ti ${icon}`} style={{ color }} />
        </div>
      </div>
      <div className="sc-value">
        {loading ? <span className="skeleton" style={{ width: 60, display: "inline-block" }} /> : (value ?? 0).toLocaleString()}
      </div>
      <div className="sc-label">{label}</div>
      {sub && <div className="sc-sub">{sub}</div>}
    </div>
  );
}

const CUSTOM_TOOLTIP = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <div className="ct-label">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="ct-val" style={{ color: p.color ?? "#fff" }}>
          {p.name}: <b>{p.value?.toLocaleString()}</b>
        </div>
      ))}
    </div>
  );
};

function LeadCard({ lead, onStageChange, dragging, onDragStart, onDragEnd }) {
  const stage = STAGE_MAP[lead.pipeline_stage] ?? STAGES[0];
  return (
    <div
      className={`kanban-card ${dragging ? "dragging" : ""}`}
      draggable
      onDragStart={() => onDragStart(lead)}
      onDragEnd={onDragEnd}
    >
      <div className="kc-top">
        <div className="kc-name">{lead.name}</div>
        <span className="kc-tier" style={{ background: `${tierColor(lead.tier)}22`, color: tierColor(lead.tier) }}>
          {lead.tier ?? "—"}
        </span>
      </div>
      {lead.niche && <div className="kc-niche">{lead.niche}</div>}
      <div className="kc-meta">
        {lead.city && <span><i className="ti ti-map-pin" />{lead.city}</span>}
        {lead.final_score != null && <span><i className="ti ti-star" />{lead.final_score}</span>}
      </div>
      {lead.email && (
        <div className="kc-email">
          <i className="ti ti-mail" />
          <span>{lead.email}</span>
        </div>
      )}
      <div className="kc-footer">
        <span className="kc-date">{formatDate(lead.scraped_at)}</span>
        <select
          className="kc-stage-select"
          value={lead.pipeline_stage ?? "scraped"}
          onChange={e => onStageChange(lead.id, e.target.value)}
          onClick={e => e.stopPropagation()}
          style={{ color: stage.color, borderColor: `${stage.color}44` }}
        >
          {STAGES.map(s => (
            <option key={s.key} value={s.key}>{s.label}</option>
          ))}
        </select>
      </div>
    </div>
  );
}

export default function Pipeline() {
  const [searchParams] = useSearchParams();

  const [view,       setView]       = useState("kanban");
  const [leads,      setLeads]      = useState([]);
  const [summary,    setSummary]    = useState({});
  const [loading,    setLoading]    = useState(true);
  const [chartLoad,  setChartLoad]  = useState(true);
  const [funnelData, setFunnelData] = useState([]);
  const [stageBar,   setStageBar]   = useState([]);

  const [stageFilter, setStageFilter] = useState("");
  const [tierFilter,  setTierFilter]  = useState("");
  const [page,        setPage]        = useState(1);
  const [total,       setTotal]       = useState(0);
  const LIMIT = 12;

  // Deep link support: arriving via /admin/pipeline?highlight=<id> (e.g.
  // from a lead's detail modal) narrows the table to that lead's own
  // stage/tier so it's virtually guaranteed to be on page 1, then we
  // scroll to it and flash-highlight the row.
  const [highlightId, setHighlightId] = useState(null);

  useEffect(() => {
    const id = searchParams.get("highlight");
    if (!id) return;
    setHighlightId(Number(id));
    setView("table");

    fetch(`${API}/leads/${id}`)
      .then(res => (res.ok ? res.json() : null))
      .then(lead => {
        if (!lead) return;
        if (lead.pipeline_stage) setStageFilter(lead.pipeline_stage);
        if (lead.tier)           setTierFilter(lead.tier);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Once the matching leads land in the table, scroll to the highlighted
  // row and flash it briefly so it's easy to spot.
  useEffect(() => {
    if (!highlightId || loading) return;
    const row = document.querySelector(`[data-lead-id="${highlightId}"]`);
    if (!row) return;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("row-highlighted");
    const t = setTimeout(() => row.classList.remove("row-highlighted"), 2500);
    return () => clearTimeout(t);
  }, [highlightId, loading, leads]);

  const dragLead = useRef(null);
  const [dragOver, setDragOver] = useState(null);

  const fetchSummary = useCallback(async () => {
    setChartLoad(true);
    try {
      const res  = await fetch(`${API}/pipeline/summary`);
      const data = await res.json();
      setSummary(data);

      const funnelStages = ["email_sent", "email_opened", "replied", "call_placed", "demo_booked", "onboarded"];
      setFunnelData(
        funnelStages.map(k => ({
          name:  STAGE_MAP[k]?.label ?? k,
          value: data[k] ?? 0,
          color: STAGE_MAP[k]?.color ?? "#e8622a",
        })).filter(d => d.value > 0)
      );

      setStageBar(
        STAGES.filter(s => !["dnc", "rejected"].includes(s.key)).map(s => ({
          name:  s.label,
          count: data[s.key] ?? 0,
          color: s.color,
        }))
      );
    } catch (e) {
      console.error("fetchSummary:", e);
    } finally {
      setChartLoad(false);
    }
  }, []);

  const fetchLeads = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        skip:  (page - 1) * LIMIT,
        limit: view === "kanban" ? 200 : LIMIT,
        ...(stageFilter && { pipeline_stage: stageFilter }),
        ...(tierFilter  && { tier: tierFilter }),
      });
      const res  = await fetch(`${API}/leads?${params}`);
      const data = await res.json();
      const arr  = Array.isArray(data) ? data : data.leads ?? [];
      setLeads(arr);
      setTotal(data.total ?? arr.length);
    } catch (e) {
      console.error("fetchLeads:", e);
    } finally {
      setLoading(false);
    }
  }, [page, stageFilter, tierFilter, view]);

  useEffect(() => { fetchSummary(); }, [fetchSummary]);
  useEffect(() => { fetchLeads(); }, [fetchLeads]);

  const handleStageChange = async (leadId, newStage) => {
    setLeads(prev => prev.map(l => l.id === leadId ? { ...l, pipeline_stage: newStage } : l));
    try {
      await fetch(`${API}/leads/${leadId}/pipeline`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stage: newStage }),
      });
      fetchSummary();
    } catch (e) {
      console.error("stageChange:", e);
      fetchLeads();
    }
  };

  const handleDragStart = (lead) => { dragLead.current = lead; };
  const handleDragEnd   = () => { dragLead.current = null; setDragOver(null); };
  const handleDrop      = (stageKey) => {
    if (dragLead.current && dragLead.current.pipeline_stage !== stageKey) {
      handleStageChange(dragLead.current.id, stageKey);
    }
    setDragOver(null);
  };

  const totalInPipeline = Object.values(summary).reduce((a, b) => a + b, 0);
  const onboarded  = summary["onboarded"]   ?? 0;
  const demoBooked = summary["demo_booked"] ?? 0;
  const replied    = summary["replied"]     ?? 0;
  const convRate   = totalInPipeline ? Math.round((onboarded / totalInPipeline) * 100) : 0;
  const totalPages = Math.max(1, Math.ceil(total / LIMIT));

  return (
    <div className="pipeline-page">

      <div className="stat-grid">
        <StatCard icon="ti-layout-kanban" color="#e8622a" bg="rgba(232,98,42,0.1)"
          label="Total in pipeline" value={totalInPipeline} sub="All active leads" loading={chartLoad} />
        <StatCard icon="ti-message-check" color="#4ade80" bg="rgba(74,222,128,0.08)"
          label="Replied" value={replied} sub="Responded to outreach" loading={chartLoad} />
        <StatCard icon="ti-calendar-check" color="#a78bfa" bg="rgba(167,139,250,0.08)"
          label="Demo booked" value={demoBooked} sub="Ready to onboard" loading={chartLoad} />
        <StatCard icon="ti-check-circle" color="#4ade80" bg="rgba(74,222,128,0.1)"
          label="Onboarded" value={onboarded}
          sub={<><b style={{ color: "#4ade80" }}>{convRate}%</b> conversion rate</>}
          loading={chartLoad} />
      </div>

      <div className="charts-grid">
        <div className="chart-panel chart-panel--wide">
          <div className="panel-header">
            <div>
              <div className="panel-title">Leads per stage</div>
              <div className="panel-sub">Distribution across all pipeline stages</div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? <div className="chart-skeleton" /> : (
              <ResponsiveContainer width="100%" height={150}>
                <BarChart data={stageBar} barCategoryGap="25%">
                  <XAxis dataKey="name" tick={{ fontSize: 9, fill: "#888" }} axisLine={false} tickLine={false} interval={0} angle={-25} textAnchor="end" height={40} />
                  <YAxis hide />
                  <Tooltip content={<CUSTOM_TOOLTIP />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
                  <Bar dataKey="count" name="Leads" radius={[4, 4, 0, 0]}>
                    {stageBar.map((entry, i) => <Cell key={i} fill={entry.color} fillOpacity={0.85} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        <div className="chart-panel chart-panel--funnel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Conversion funnel</div>
              <div className="panel-sub">Email → Onboard</div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? <div className="chart-skeleton" /> : funnelData.length === 0 ? (
              <div className="chart-empty">No data yet</div>
            ) : (
              <div className="funnel-list">
                {funnelData.map((item, i) => {
                  const max = funnelData[0]?.value || 1;
                  const pct = Math.round((item.value / max) * 100);
                  return (
                    <div key={i} className="funnel-row">
                      <div className="funnel-label">{item.name}</div>
                      <div className="funnel-bar-wrap">
                        <div className="funnel-bar-fill" style={{ width: `${pct}%`, background: item.color }} />
                      </div>
                      <div className="funnel-val" style={{ color: item.color }}>{item.value.toLocaleString()}</div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="pipeline-toolbar">
        <div className="view-toggle">
          <button className={`vt-btn ${view === "kanban" ? "active" : ""}`} onClick={() => setView("kanban")}>
            <i className="ti ti-layout-columns" /> Kanban
          </button>
          <button className={`vt-btn ${view === "table" ? "active" : ""}`} onClick={() => setView("table")}>
            <i className="ti ti-table" /> Table
          </button>
        </div>
        <div className="table-actions">
          <select className="filter-select" value={stageFilter} onChange={e => { setStageFilter(e.target.value); setPage(1); }}>
            <option value="">All stages</option>
            {STAGES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <select className="filter-select" value={tierFilter} onChange={e => { setTierFilter(e.target.value); setPage(1); }}>
            <option value="">All tiers</option>
            <option value="hot">🔥 Hot</option>
            <option value="warm">⚡ Warm</option>
            <option value="cold">❄️ Cold</option>
          </select>
        </div>
      </div>

      {/* ── KANBAN ── */}
      {view === "kanban" && (
        <div className="kanban-scroll">
          <div className="kanban-board">
            {KANBAN_STAGES.map(stage => {
              const stageLeads = leads.filter(l => l.pipeline_stage === stage.key);
              const isDragOver = dragOver === stage.key;
              return (
                <div key={stage.key}
                  className={`kanban-col ${isDragOver ? "drag-over" : ""}`}
                  onDragOver={e => { e.preventDefault(); setDragOver(stage.key); }}
                  onDragLeave={() => setDragOver(null)}
                  onDrop={() => handleDrop(stage.key)}
                >
                  <div className="kanban-col-header" style={{ borderTopColor: stage.color }}>
                    <div className="kch-left">
                      <i className={`ti ${stage.icon}`} style={{ color: stage.color }} />
                      <span className="kch-label">{stage.label}</span>
                    </div>
                    <span className="kch-count" style={{ background: `${stage.color}22`, color: stage.color }}>
                      {summary[stage.key] ?? 0}
                    </span>
                  </div>
                  <div className="kanban-cards">
                    {loading
                      ? Array.from({ length: 2 }).map((_, i) => <div key={i} className="kanban-card-skeleton" />)
                      : stageLeads.length === 0
                      ? <div className="kanban-empty">Drop leads here</div>
                      : stageLeads.map(lead => (
                          <LeadCard key={lead.id} lead={lead} onStageChange={handleStageChange}
                            onDragStart={handleDragStart} onDragEnd={handleDragEnd} />
                        ))
                    }
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── TABLE ── */}
      {view === "table" && (
        <div className="table-panel">
          <div className="table-header">
            <div>
              <div className="panel-title">All pipeline leads</div>
              <div className="panel-sub">{total.toLocaleString()} leads across all stages</div>
            </div>
            <div className="table-actions">
              <button className="btn-ghost" onClick={() => window.open(`${API}/leads/export/csv`, "_blank")}>
                <i className="ti ti-download" /> <span>Export CSV</span>
              </button>
            </div>
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Lead</th><th>Stage</th><th>Tier</th><th>Score</th>
                  <th>Niche</th><th>City</th><th>Email</th><th>Updated</th><th>Move to</th>
                </tr>
              </thead>
              <tbody>
                {loading
                  ? Array.from({ length: 8 }).map((_, i) => (
                      <tr key={i} className="skeleton-row">
                        {Array.from({ length: 9 }).map((_, j) => <td key={j}><div className="skeleton" /></td>)}
                      </tr>
                    ))
                  : leads.length === 0
                  ? (
                      <tr>
                        <td colSpan={9} className="empty-state">
                          <i className="ti ti-layout-kanban" /><p>No leads found</p>
                          <span>Try changing your filters</span>
                        </td>
                      </tr>
                    )
                  : leads.map(lead => {
                      const stage = STAGE_MAP[lead.pipeline_stage] ?? STAGES[0];
                      return (
                        <tr key={lead.id} data-lead-id={lead.id}>
                          <td>
                            <div className="biz-name">{lead.name}</div>
                            <div className="biz-src">
                              <span className="src-dot" style={{ background: "#e8622a" }} />ID #{lead.id}
                            </div>
                          </td>
                          <td>
                            <span className="pipeline-stage-badge" style={{ background: `${stage.color}18`, color: stage.color, border: `1px solid ${stage.color}33` }}>
                              <i className={`ti ${stage.icon}`} />{stage.label}
                            </span>
                          </td>
                          <td>
                            <span className="tier-badge" style={{ background: `${tierColor(lead.tier)}18`, color: tierColor(lead.tier) }}>
                              {lead.tier ?? "—"}
                            </span>
                          </td>
                          <td>
                            <div className="score-wrap">
                              <div className="score-bar" style={{ width: `${lead.final_score ?? 0}%`, background: tierColor(lead.tier) }} />
                              <span className="score-num">{lead.final_score ?? 0}</span>
                            </div>
                          </td>
                          <td className="dim-cell">{lead.niche ?? "—"}</td>
                          <td className="dim-cell">{lead.city ?? "—"}</td>
                          <td className="dim-cell">
                            {lead.email_verified
                              ? <span style={{ color: "#4ade80" }}>✓ {lead.email}</span>
                              : <span style={{ color: "rgba(255,255,255,0.3)" }}>{lead.email ?? "—"}</span>}
                          </td>
                          <td className="date-cell">{formatDate(lead.updated_at)}</td>
                          <td>
                            <select className="stage-quick-select" value={lead.pipeline_stage ?? "scraped"}
                              onChange={e => handleStageChange(lead.id, e.target.value)} style={{ color: stage.color }}>
                              {STAGES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
                            </select>
                          </td>
                        </tr>
                      );
                    })
                }
              </tbody>
            </table>
          </div>

          <div className="pagination">
            <div className="pag-info">
              Showing {Math.min((page - 1) * LIMIT + 1, total)}–{Math.min(page * LIMIT, total)} of {total.toLocaleString()}
            </div>
            <div className="pag-buttons">
              <button className="pag-btn" disabled={page === 1} onClick={() => setPage(p => p - 1)}>‹</button>
              {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                const p = totalPages <= 5 ? i + 1 : page <= 3 ? i + 1 : page >= totalPages - 2 ? totalPages - 4 + i : page - 2 + i;
                return <button key={p} className={`pag-btn ${p === page ? "active" : ""}`} onClick={() => setPage(p)}>{p}</button>;
              })}
              <button className="pag-btn" disabled={page === totalPages} onClick={() => setPage(p => p + 1)}>›</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}