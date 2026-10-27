import { useState, useEffect, useCallback } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell, PieChart, Pie,
  LineChart, Line, CartesianGrid,
  RadialBarChart, RadialBar, PolarAngleAxis,
} from "recharts";
import "./CallLogs.css";
import LeadDetailModal from "../components/LeadDetailModal";
import { fmtDate } from "../utils/settings";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ─── helpers ──────────────────────────────────────────────────────────────────
const OUTCOME_MAP = {
  interested:     { cls: "co-interested",  label: "✅ Interested",     color: "#4ade80" },
  not_interested: { cls: "co-not",         label: "✗ Not interested",  color: "#f87171" },
  callback:       { cls: "co-callback",    label: "📅 Callback",       color: "#e8622a" },
  no_answer:      { cls: "co-noanswer",    label: "— No answer",       color: "#6b7280" },
  wrong_number:   { cls: "co-wrong",       label: "✗ Wrong number",    color: "#f87171" },
  voicemail:      { cls: "co-voicemail",   label: "📩 Voicemail",      color: "#f59e0b" },
};

function outcomeInfo(outcome) {
  return OUTCOME_MAP[outcome] ?? { cls: "co-noanswer", label: outcome ?? "—", color: "#6b7280" };
}

function formatDuration(sec) {
  if (!sec) return "—";
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}m ${s}s`;
}

function formatDate(iso) {
  return fmtDate(iso, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// ─── sub-components ───────────────────────────────────────────────────────────
function StatCard({ icon, color, bg, label, value, sub, loading }) {
  return (
    <div className="stat-card">
      <div className="sc-top">
        <div className="sc-icon" style={{ background: bg }}>
          <i className={`ti ${icon}`} style={{ color }} />
        </div>
      </div>
      <div className="sc-value" style={{ color: "#fff" }}>
        {loading ? <span className="skeleton" style={{ width: 60, display: "inline-block" }} /> : (value ?? 0).toLocaleString()}
      </div>
      <div className="sc-label">{label}</div>
      {sub && <div className="sc-sub">{sub}</div>}
    </div>
  );
}

const MONTH_CHIPS = ["All", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CHIP_W = 34;

/**
 * MonthYearFilter — sliding capsule: year stepper on the left, month
 * ribbon on the right with a single glowing pill that glides between
 * chips. Filters the stat cards, charts, and the call log table below.
 */
function MonthYearFilter({ year, month, onChange }) {
  const now = new Date();
  const yearMin = now.getFullYear() - 4;
  const yearMax = now.getFullYear();
  const activeIdx = month ?? 0;

  const stepYear = (delta) => {
    if (year === null) { onChange(yearMax, null); return; }
    const next = year + delta;
    if (next < yearMin || next > yearMax) return;
    onChange(next, month);
  };

  return (
    <div className="my-filter">
      <div className="my-year">
        <button className="my-year-arrow" onClick={() => stepYear(-1)} disabled={year === null || year <= yearMin} aria-label="Previous year">
          <i className="ti ti-chevron-left" />
        </button>
        <span className="my-year-label">{year ?? "All time"}</span>
        <button className="my-year-arrow" onClick={() => stepYear(1)} disabled={year !== null && year >= yearMax} aria-label="Next year">
          <i className="ti ti-chevron-right" />
        </button>
        {year !== null && (
          <button className="my-year-clear" onClick={() => onChange(null, null)} aria-label="Clear filter" title="Back to all time">
            <i className="ti ti-x" />
          </button>
        )}
      </div>

      <div className="my-divider" />

      <div className={"my-month-track" + (year === null ? " my-month-track--disabled" : "")}>
        <div className="my-month-slider" style={{ transform: `translateX(${activeIdx * CHIP_W}px)` }} />
        {MONTH_CHIPS.map((label, i) => (
          <button
            key={label}
            className={"my-month-chip" + (activeIdx === i ? " my-month-chip--active" : "")}
            style={{ width: CHIP_W }}
            disabled={year === null}
            onClick={() => onChange(year, i === 0 ? null : i)}
          >
            {label}
          </button>
        ))}
      </div>
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

// ─── main page ────────────────────────────────────────────────────────────────
export default function CallLogs() {
  // data
  const [logs,       setLogs]       = useState([]);
  const [total,      setTotal]      = useState(0);
  const [loading,    setLoading]    = useState(true);
  const [chartLoad,  setChartLoad]  = useState(true);
  const [stats,      setStats]      = useState(null);
  const [outcomeDist, setOutcomeDist] = useState([]);
  const [trendData,  setTrendData]  = useState([]);
  const [durationData, setDurationData] = useState([]);
  const [pickupData, setPickupData] = useState([]);
  const [pickupRate, setPickupRate] = useState(0);

  // filters
  const [outcome,  setOutcome]  = useState("");
  const [year,     setYear]     = useState(null);
  const [month,    setMonth]    = useState(null);
  const [page,     setPage]     = useState(1);
  const [selected, setSelected] = useState([]);
  const LIMIT = 10;
  const [viewLeadId, setViewLeadId] = useState(null);

  // call modal
  const [callOpen,  setCallOpen]  = useState(false);
  const [callForm,  setCallForm]  = useState({ hot_leads_only: true, min_score: null, lead_id: "", dry_run: false });
  const [launching, setLaunching] = useState(false);

  // recording modal
  const [recordingUrl, setRecordingUrl] = useState(null);

  // ── fetch logs ───────────────────────────────────────────────────────────────
  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        skip:  (page - 1) * LIMIT,
        limit: LIMIT,
        ...(outcome && { outcome }),
        ...(year !== null && { year: String(year) }),
        ...(year !== null && month !== null && { month: String(month) }),
      });
      const res  = await fetch(`${API}/call-logs?${params}`);
      const data = await res.json();
      const arr  = Array.isArray(data) ? data : data.logs ?? [];
      setLogs(arr);
      setTotal(data.total ?? arr.length);
    } catch (e) {
      console.error("fetchLogs:", e);
    } finally {
      setLoading(false);
    }
  }, [page, outcome, year, month]);

  // ── fetch stats ──────────────────────────────────────────────────────────────
  const fetchStats = useCallback(async () => {
    setChartLoad(true);
    try {
      const dq = new URLSearchParams();
      if (year !== null) {
        dq.set("year", year);
        if (month !== null) dq.set("month", month);
      }
      const qs = dq.toString();

      const [overviewRes, trendRes, rawLogsRes] = await Promise.all([
        fetch(`${API}/analytics/overview${qs ? `?${qs}` : ""}`),
        fetch(`${API}/analytics/trend`),
        year !== null ? fetch(`${API}/call-logs?${qs}&limit=9999`) : Promise.resolve(null),
      ]);
      const data  = await overviewRes.json();
      const trend = trendRes.ok ? await trendRes.json() : { calls: [] };
      const calls = data.calls ?? {};

      setStats({
        total:       calls.total        ?? 0,
        interested:  calls.interested   ?? 0,
        callbacks:   calls.callback     ?? 0,
        avgDuration: calls.avg_duration ?? 0,
      });

      // outcome distribution donut — real counts from backend
      setOutcomeDist([
        { name: "Interested",     value: calls.interested    ?? 0, color: "#4ade80" },
        { name: "Callback",       value: calls.callback      ?? 0, color: "#e8622a" },
        { name: "Voicemail",      value: calls.voicemail     ?? 0, color: "#f59e0b" },
        { name: "No answer",      value: calls.no_answer     ?? 0, color: "#6b7280" },
        { name: "Not interested", value: calls.not_interested ?? 0, color: "#f87171" },
      ]);

      // ── call trend — last 7 days by default, or the selected year/month
      //    rebuilt client-side from raw logs ─────────────────────────────
      if (year === null) {
        setTrendData(
          (trend.calls ?? []).map((d) => ({
            day: d.day,
            calls: d.calls,
            interested: d.interested,
          }))
        );
      } else if (rawLogsRes?.ok) {
        const rawData = await rawLogsRes.json();
        const rawLogs = Array.isArray(rawData) ? rawData : rawData.logs ?? [];
        const bucket = {};
        rawLogs.forEach((l) => {
          if (!l.called_at) return;
          const key = month !== null ? l.called_at.slice(8, 10) : l.called_at.slice(5, 7);
          if (!bucket[key]) bucket[key] = { calls: 0, interested: 0 };
          bucket[key].calls += 1;
          if (l.outcome === "interested") bucket[key].interested += 1;
        });
        let rows;
        if (month !== null) {
          const daysInMonth = new Date(year, month, 0).getDate();
          rows = Array.from({ length: daysInMonth }, (_, i) => {
            const dayKey = String(i + 1).padStart(2, "0");
            const b = bucket[dayKey] ?? { calls: 0, interested: 0 };
            return { day: String(i + 1), ...b };
          });
        } else {
          rows = MONTH_CHIPS.slice(1).map((label, i) => {
            const monthKey = String(i + 1).padStart(2, "0");
            const b = bucket[monthKey] ?? { calls: 0, interested: 0 };
            return { day: label, ...b };
          });
        }
        setTrendData(rows);
      }

      // avg duration by outcome — real averages from backend
      const byOutcome = calls.avg_duration_by_outcome ?? {};
      setDurationData([
        { name: "Interested",     avg: byOutcome.interested     ?? 0 },
        { name: "Callback",       avg: byOutcome.callback       ?? 0 },
        { name: "Not interested", avg: byOutcome.not_interested ?? 0 },
        { name: "Voicemail",      avg: byOutcome.voicemail      ?? 0 },
        { name: "No answer",      avg: byOutcome.no_answer      ?? 0 },
      ]);

      // picked up vs not picked up — someone answered (interested/not
      // interested/callback/wrong number) vs nobody answered (no answer/voicemail)
      const pickedUp = (calls.interested ?? 0) + (calls.not_interested ?? 0)
                      + (calls.callback ?? 0) + (calls.wrong_number ?? 0);
      const notPicked = (calls.no_answer ?? 0) + (calls.voicemail ?? 0);
      setPickupData([
        { name: "Picked up",     value: pickedUp,  color: "#4ade80" },
        { name: "Not picked up", value: notPicked, color: "#f87171" },
      ]);
      const totalPicked = pickedUp + notPicked;
      setPickupRate(totalPicked ? Math.round((pickedUp / totalPicked) * 100) : 0);
    } catch (e) {
      console.error("fetchStats:", e);
    } finally {
      setChartLoad(false);
    }
  }, [year, month]);

  useEffect(() => { fetchStats(); }, [fetchStats]);
  useEffect(() => { fetchLogs(); setSelected([]); }, [fetchLogs]);

  // ── launch calls ─────────────────────────────────────────────────────────────
  const handleLaunch = async () => {
    setLaunching(true);
    try {
      await fetch(`${API}/pipeline/vapi-calls`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hot_leads_only: callForm.hot_leads_only,
          min_score:      callForm.min_score || null,
          lead_id:        callForm.lead_id   ? parseInt(callForm.lead_id) : null,
          dry_run:        callForm.dry_run,
        }),
      });
      setCallOpen(false);
      fetchStats();
      fetchLogs();
    } catch (e) {
      console.error("launch:", e);
    } finally {
      setLaunching(false);
    }
  };

  // ── select ───────────────────────────────────────────────────────────────────
  const toggleAll = () =>
    setSelected(selected.length === logs.length ? [] : logs.map(l => l.id));
  const toggleOne = id =>
    setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);

  const totalPages = Math.max(1, Math.ceil(total / LIMIT));

  return (
    <div className="calls-page">

      {/* ── month/year filter ── */}
      <div className="cl-toolbar">
        <div className="cl-toolbar-label">Filter by period</div>
        <MonthYearFilter year={year} month={month} onChange={(y, m) => { setYear(y); setMonth(m); setPage(1); }} />
      </div>

      {/* ── stat cards ── */}
      <div className="stat-grid">
        <StatCard
          icon="ti-phone"
          color="#e8622a" bg="rgba(232,98,42,0.1)"
          label="Total calls"
          value={stats?.total}
          sub="Via VAPI AI agent"
          loading={chartLoad}
        />
        <StatCard
          icon="ti-thumb-up"
          color="#4ade80" bg="rgba(74,222,128,0.08)"
          label="Interested"
          value={stats?.interested}
          sub={<><b style={{ color: "#4ade80" }}>
            {stats?.total ? Math.round((stats.interested / stats.total) * 100) : 0}%
          </b> conversion rate</>}
          loading={chartLoad}
        />
        <StatCard
          icon="ti-calendar-time"
          color="#e8622a" bg="rgba(232,98,42,0.08)"
          label="Callbacks scheduled"
          value={stats?.callbacks}
          sub="Follow-up required"
          loading={chartLoad}
        />
        <StatCard
          icon="ti-clock"
          color="#f59e0b" bg="rgba(245,158,11,0.08)"
          label="Avg call duration"
          value={formatDuration(stats?.avgDuration)}
          sub="Per connected call"
          loading={chartLoad}
        />
      </div>

      {/* ── charts ── */}
      <div className="charts-grid cl-charts-grid">

        {/* line — 7-day call trend */}
        <div className="chart-panel chart-panel--wide cl-gp-wide">
          <div className="panel-header">
            <div>
              <div className="panel-title">Call activity{year === null ? " — last 7 days" : ""}</div>
              <div className="panel-sub">
                {year === null
                  ? "Total calls placed & interested responses"
                  : month === null
                  ? `Total calls placed & interested — ${year}`
                  : `Total calls placed & interested — ${MONTH_CHIPS[month]} ${year}`}
              </div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? <div className="chart-skeleton" /> : (
              <ResponsiveContainer width="100%" height={150}>
                <LineChart data={trendData}>
                  <CartesianGrid stroke="rgba(255,255,255,0.05)" strokeDasharray="3 3" />
                  <XAxis dataKey="day" tick={{ fontSize: 10, fill: "#999" }} axisLine={false} tickLine={false} />
                  <YAxis hide />
                  <Tooltip content={<CUSTOM_TOOLTIP />} cursor={{ stroke: "rgba(255,255,255,0.08)" }} />
                  <Line type="monotone" dataKey="calls"      name="Calls"      stroke="#e8622a" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="interested" name="Interested" stroke="#4ade80" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
            <div className="chart-legend">
              {[{ color: "#e8622a", label: "Calls placed" }, { color: "#4ade80", label: "Interested" }].map((item, i) => (
                <div key={i} className="legend-item">
                  <span className="legend-dot" style={{ background: item.color }} />
                  {item.label}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* donut — outcome breakdown */}
        <div className="chart-panel cl-gp-outcome">
          <div className="panel-header">
            <div>
              <div className="panel-title">Outcome breakdown</div>
              <div className="panel-sub">
                {year === null ? "All-time call results" : month === null ? `${year} call results` : `${MONTH_CHIPS[month]} ${year} call results`}
              </div>
            </div>
          </div>
          <div className="chart-body donut-body">
            {chartLoad ? <div className="chart-skeleton" /> : (
              <>
                <ResponsiveContainer width={130} height={130}>
                  <PieChart>
                    <Pie
                      data={outcomeDist}
                      cx="50%" cy="50%"
                      innerRadius={38} outerRadius={58}
                      paddingAngle={3}
                      dataKey="value"
                      animationBegin={0}
                      animationDuration={900}
                    >
                      {outcomeDist.map((entry, i) => (
                        <Cell key={i} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v, n) => [v.toLocaleString(), n]} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="donut-legend">
                  {outcomeDist.map((t, i) => (
                    <div key={i} className="dl-item">
                      <span className="dl-dot" style={{ background: t.color }} />
                      <span className="dl-name">{t.name}</span>
                      <span className="dl-val">{t.value.toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>

        {/* bar — avg duration by outcome */}
        <div className="chart-panel cl-gp-avg">
          <div className="panel-header">
            <div>
              <div className="panel-title">Avg duration by outcome</div>
              <div className="panel-sub">Seconds per call type</div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? <div className="chart-skeleton" /> : (
              <ResponsiveContainer width="100%" height={150}>
                <BarChart data={durationData} barCategoryGap="30%" layout="vertical">
                  <XAxis type="number" hide />
                  <YAxis
                    type="category" dataKey="name"
                    tick={{ fontSize: 9, fill: "#888" }}
                    axisLine={false} tickLine={false} width={78}
                  />
                  <Tooltip
                    formatter={(v) => [`${formatDuration(v)}`, "Avg duration"]}
                    cursor={{ fill: "rgba(255,255,255,0.03)" }}
                  />
                  <Bar dataKey="avg" radius={[0, 4, 4, 0]}>
                    {durationData.map((entry, i) => {
                      const colors = ["#4ade80", "#e8622a", "#f87171", "#f59e0b", "#6b7280"];
                      return <Cell key={i} fill={colors[i] ?? "#e8622a"} />;
                    })}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* donut — picked up vs not picked up */}
        <div className="chart-panel cl-gp-new">
          <div className="panel-header">
            <div>
              <div className="panel-title">Picked up vs not picked up</div>
              <div className="panel-sub">Someone answered vs no answer / voicemail</div>
            </div>
          </div>
          <div className="chart-body gauge-body">
            {chartLoad ? <div className="chart-skeleton" /> : (
              <>
                <div className="gauge-wrap">
                  <ResponsiveContainer width={150} height={150}>
                    <RadialBarChart
                      cx="50%" cy="50%"
                      innerRadius="72%" outerRadius="100%"
                      barSize={14}
                      startAngle={90}
                      endAngle={-270}
                      data={[{ value: pickupRate }]}
                    >
                      <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
                      <RadialBar
                        dataKey="value"
                        cornerRadius={8}
                        fill="#4ade80"
                        background={{ fill: "rgba(255,255,255,0.06)" }}
                      />
                    </RadialBarChart>
                  </ResponsiveContainer>
                  <div className="gauge-center">
                    <div className="gauge-value">{pickupRate}%</div>
                    <div className="gauge-label">picked up</div>
                  </div>
                </div>
                <div className="donut-legend">
                  {pickupData.map((t, i) => (
                    <div key={i} className="dl-item">
                      <span className="dl-dot" style={{ background: t.color }} />
                      <span className="dl-name">{t.name}</span>
                      <span className="dl-val">{t.value.toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ── table ── */}
      <div className="table-panel">
        <div className="table-header">
          <div>
            <div className="panel-title">Call log</div>
            <div className="panel-sub">All VAPI voice calls with outcomes &amp; recordings</div>
          </div>
          <div className="table-actions">
            <select className="filter-select" value={outcome} onChange={e => { setOutcome(e.target.value); setPage(1); }}>
              <option value="">All outcomes</option>
              <option value="interested">✅ Interested</option>
              <option value="callback">📅 Callback</option>
              <option value="voicemail">📩 Voicemail</option>
              <option value="no_answer">— No answer</option>
              <option value="not_interested">✗ Not interested</option>
              <option value="wrong_number">✗ Wrong number</option>
            </select>
            <button className="btn-ghost" onClick={() => window.open(`${API}/call-logs/export`, "_blank")}>
              <i className="ti ti-download" /> <span>Export</span>
            </button>
            <button className="btn-primary" onClick={() => setCallOpen(true)}>
              <i className="ti ti-phone-plus" /> <span>Launch calls</span>
            </button>
          </div>
        </div>

        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    checked={selected.length === logs.length && logs.length > 0}
                    onChange={toggleAll}
                    style={{ accentColor: "#e8622a" }}
                  />
                </th>
                <th>Lead</th>
                <th>Outcome</th>
                <th>Duration</th>
                <th>Called at</th>
                <th>VAPI call ID</th>
                <th>Notes</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 6 }).map((_, i) => (
                    <tr key={i} className="skeleton-row">
                      {Array.from({ length: 8 }).map((_, j) => (
                        <td key={j}><div className="skeleton" /></td>
                      ))}
                    </tr>
                  ))
                : logs.length === 0
                ? (
                    <tr>
                      <td colSpan={8} className="empty-state">
                        <i className="ti ti-phone-off" />
                        <p>No calls found</p>
                        <span>Launch VAPI calls to hot leads to get started</span>
                      </td>
                    </tr>
                  )
                : logs.map(log => {
                    const oc = outcomeInfo(log.outcome);
                    return (
                      <tr key={log.id} className={selected.includes(log.id) ? "row-selected" : ""}>
                        <td>
                          <input
                            type="checkbox"
                            checked={selected.includes(log.id)}
                            onChange={() => toggleOne(log.id)}
                            style={{ accentColor: "#e8622a" }}
                          />
                        </td>
                        <td>
                          <div className="biz-name">Lead #{log.lead_id}</div>
                          <div className="biz-src">
                            <span className="src-dot" style={{ background: "#e8622a" }} />
                            VAPI agent
                          </div>
                        </td>
                        <td>
                          <span className={`call-outcome-badge ${oc.cls}`}>
                            {oc.label}
                          </span>
                        </td>
                        <td>
                          <div className="duration-cell">
                            <i className="ti ti-clock" style={{ fontSize: 11, color: "rgba(255,255,255,0.25)" }} />
                            {formatDuration(log.duration_sec)}
                          </div>
                        </td>
                        <td className="date-cell">{formatDate(log.called_at)}</td>
                        <td>
                          {log.vapi_call_id
                            ? <span className="vapi-id">{log.vapi_call_id.slice(0, 16)}…</span>
                            : <span style={{ color: "rgba(255,255,255,0.2)" }}>—</span>}
                        </td>
                        <td>
                          <div className="call-notes">{log.notes ?? "—"}</div>
                        </td>
                        <td>
                          <div className="row-actions">
                            {log.recording_url && (
                              <button
                                className="btn-primary btn-xs"
                                title="Play recording"
                                onClick={() => setRecordingUrl(log.recording_url)}
                              >
                                <i className="ti ti-player-play" />
                              </button>
                            )}
                            <button className="btn-ghost btn-xs" title="View lead" onClick={() => setViewLeadId(log.lead_id)}>
                              <i className="ti ti-eye" />
                            </button>
                          </div>
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
            Showing {Math.min((page - 1) * LIMIT + 1, total)}–{Math.min(page * LIMIT, total)} of {total.toLocaleString()} calls
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

      {/* ── launch calls modal ── */}
      {callOpen && (
        <div className="modal-overlay" onClick={() => setCallOpen(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <div className="modal-title">Launch VAPI calls</div>
                <div className="modal-sub">AI agent calls hot leads with a personalized script via VAPI</div>
              </div>
              <button className="modal-close" onClick={() => setCallOpen(false)}>
                <i className="ti ti-x" />
              </button>
            </div>
            <div className="modal-body">
              <div className="form-row">
                <div className="form-group">
                  <label>Min lead score</label>
                  <input
                    type="number" min="0" max="100"
                    placeholder="e.g. 70"
                    value={callForm.min_score ?? ""}
                    onChange={e => setCallForm(f => ({ ...f, min_score: e.target.value ? parseInt(e.target.value) : null }))}
                  />
                </div>
                <div className="form-group">
                  <label>Specific lead ID</label>
                  <input
                    type="number"
                    placeholder="Leave blank for bulk"
                    value={callForm.lead_id}
                    onChange={e => setCallForm(f => ({ ...f, lead_id: e.target.value }))}
                  />
                </div>
              </div>

              <div className="form-row">
                <div className="form-group form-group--check">
                  <label>Hot leads only</label>
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={callForm.hot_leads_only}
                      onChange={e => setCallForm(f => ({ ...f, hot_leads_only: e.target.checked }))}
                    />
                    <span className="toggle-track">
                      <span className="toggle-thumb" />
                    </span>
                    <span className="toggle-text">
                      {callForm.hot_leads_only ? "🔥 Hot leads only (score 85+)" : "All qualifying leads"}
                    </span>
                  </label>
                </div>
                <div className="form-group form-group--check">
                  <label>Dry run (test mode)</label>
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={callForm.dry_run}
                      onChange={e => setCallForm(f => ({ ...f, dry_run: e.target.checked }))}
                    />
                    <span className="toggle-track">
                      <span className="toggle-thumb" />
                    </span>
                    <span className="toggle-text">
                      {callForm.dry_run ? "Test only — no real calls" : "Live — will place real calls"}
                    </span>
                  </label>
                </div>
              </div>

              <div className="modal-note">
                <i className="ti ti-info-circle" />
                <span>
                  VAPI agent generates a personalized script per lead using Groq AI.
                  Leads on the DNC list are automatically skipped.
                  Each call is recorded and the outcome is saved automatically.
                </span>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn-ghost" onClick={() => setCallOpen(false)}>Cancel</button>
              <button
                className={`btn-primary ${callForm.dry_run ? "btn-dry" : ""}`}
                onClick={handleLaunch}
                disabled={launching}
              >
                {launching
                  ? <><i className="ti ti-loader-2 spin" /> Launching...</>
                  : callForm.dry_run
                  ? <><i className="ti ti-flask" /> Run dry test</>
                  : <><i className="ti ti-phone-plus" /> Start calling</>
                }
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── recording player modal ── */}
      {recordingUrl && (
        <div className="modal-overlay" onClick={() => setRecordingUrl(null)}>
          <div className="modal modal--sm" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <div className="modal-title">Call recording</div>
                <div className="modal-sub">VAPI voice call playback</div>
              </div>
              <button className="modal-close" onClick={() => setRecordingUrl(null)}>
                <i className="ti ti-x" />
              </button>
            </div>
            <div className="modal-body">
              <audio controls src={recordingUrl} style={{ width: "100%", accentColor: "#e8622a" }} />
            </div>
          </div>
        </div>
      )}

      <LeadDetailModal leadId={viewLeadId} onClose={() => setViewLeadId(null)} />
    </div>
  );
}