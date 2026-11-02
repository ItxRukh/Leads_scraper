import { useState, useEffect, useCallback } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell, PieChart, Pie,
  LineChart, Line, CartesianGrid,
  RadialBarChart, RadialBar, PolarAngleAxis,
} from "recharts";
import { getSettings, fmtDate } from "../utils/settings";
import LeadDetailModal from "../components/LeadDetailModal";
import "./WhatsApp.css";   // the same CSS file can be kept, or renamed to SMS.css

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ─── helpers ──────────────────────────────────────────────────────────────────
const STATUS_MAP = {
  sent:      { cls: "ws-sent",      label: "Sent",        color: "#6b7280" },
  delivered: { cls: "ws-delivered", label: "✓ Delivered", color: "#3b82f6" },
  read:      { cls: "ws-read",      label: "👁 Read",      color: "#f59e0b" },
  failed:    { cls: "ws-failed",    label: "✗ Failed",    color: "#f87171" },
};

function statusInfo(st) {
  return STATUS_MAP[st] ?? STATUS_MAP["sent"];
}
function formatDate(iso) {
  return fmtDate(iso, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// ─── response-time buckets (for the radial "how fast do they reply" chart) ────
const RESPONSE_BUCKETS = [
  { key: "under1h", label: "< 1 hour",  max: 1,  color: "#4ade80" },
  { key: "h1to6",   label: "1–6 hours", max: 6,  color: "#3b82f6" },
  { key: "h6to24",  label: "6–24 hours", max: 24, color: "#f59e0b" },
  { key: "over24h", label: "24h+",      max: Infinity, color: "#f87171" },
];
function bucketFor(hours) {
  return RESPONSE_BUCKETS.find((b) => hours <= b.max) ?? RESPONSE_BUCKETS[RESPONSE_BUCKETS.length - 1];
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
        {loading
          ? <span className="skeleton" style={{ width: 60, display: "inline-block" }} />
          : typeof value === "string" ? value : (value ?? 0).toLocaleString()
        }
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

// ─── variable chip helper ─────────────────────────────────────────────────────
const SMS_VARS = ["name", "business", "sender"];

const MONTH_CHIPS = ["All", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CHIP_W = 34;

/**
 * MonthYearFilter — sliding capsule: year stepper on the left, month
 * ribbon on the right with a single glowing pill that glides between
 * chips. Filters the stat cards, charts, and the message log table below.
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

function VarChips({ onInsert }) {
  return (
    <div className="wa-var-chips">
      <span className="wa-var-label">Insert:</span>
      {SMS_VARS.map(v => (
        <button
          key={v}
          type="button"
          className="wa-var-chip"
          onClick={() => onInsert(`{{${v}}}`)}
        >
          {`{{${v}}}`}
        </button>
      ))}
    </div>
  );
}

// ─── main page ────────────────────────────────────────────────────────────────
export default function SMS() {
  const [logs,       setLogs]       = useState([]);
  const [total,      setTotal]      = useState(0);
  const [loading,    setLoading]    = useState(true);
  const [chartLoad,  setChartLoad]  = useState(true);
  const [stats,      setStats]      = useState(null);
  const [statusDist, setStatusDist] = useState([]);
  const [trendData,  setTrendData]  = useState([]);
  const [replyData,  setReplyData]  = useState([]);
  const [responseTimeData, setResponseTimeData] = useState([]);

  // filters
  const [status,   setStatus]   = useState("");
  const [repliedF, setRepliedF] = useState("");
  const [year,     setYear]     = useState(null);
  const [month,    setMonth]    = useState(null);
  const [page,     setPage]     = useState(1);
  const [selected, setSelected] = useState([]);
  const LIMIT = 10;
  const [viewLeadId, setViewLeadId] = useState(null);

  // send modal
  const [sendOpen,  setSendOpen]  = useState(false);
  const [launching, setLaunching] = useState(false);

  // template mode
  const [templateMode,   setTemplateMode]   = useState("settings");
  const [templateLoaded, setTemplateLoaded] = useState(false);

  // send form
  const [sendForm, setSendForm] = useState(() => {
    const s = getSettings();
    return {
      trigger:  "auto",
      lead_id:  "",
      dry_run:  false,
      message:  s.smsTemplate ?? s.waTemplate ?? "",   // smsTemplate prefer, fallback to waTemplate
    };
  });

  // reply preview
  const [replyLog, setReplyLog] = useState(null);

  // ── load template ─────────────────────────────────────────────────────────
  const handleLoadTemplate = () => {
    const s = getSettings();
    const senderName = s.adminName?.trim() || s.companyName || "the team";
    const raw = s.smsTemplate ?? s.waTemplate ?? "";
    setSendForm(f => ({ ...f, message: raw.replace(/\{\{sender\}\}/g, senderName) || f.message }));
    setTemplateMode("settings");
    setTemplateLoaded(true);
    setTimeout(() => setTemplateLoaded(false), 2500);
  };

  const insertMsgVar = (tag) => {
    setSendForm(f => ({ ...f, message: (f.message || "") + tag }));
  };

  // ── fetch logs ────────────────────────────────────────────────────────────
  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        skip:  (page - 1) * LIMIT,
        limit: LIMIT,
        ...(status   && { delivery_status: status }),
        ...(repliedF && { replied: repliedF }),
        ...(year !== null && { year: String(year) }),
        ...(year !== null && month !== null && { month: String(month) }),
      });
      const res  = await fetch(`${API}/whatsapp-logs?${params}`);   // backend endpoint same
      const data = await res.json();
      const arr  = Array.isArray(data) ? data : data.logs ?? [];
      setLogs(arr);
      setTotal(data.total ?? arr.length);
    } catch (e) {
      console.error("fetchLogs:", e);
    } finally {
      setLoading(false);
    }
  }, [page, status, repliedF, year, month]);

  // ── fetch stats ───────────────────────────────────────────────────────────
  const fetchStats = useCallback(async () => {
    setChartLoad(true);
    try {
      const dq = new URLSearchParams();
      if (year !== null) {
        dq.set("year", year);
        if (month !== null) dq.set("month", month);
      }
      const qs = dq.toString();

      const [overviewRes, trendRes, repliedLogsRes, rawLogsRes] = await Promise.all([
        fetch(`${API}/analytics/overview${qs ? `?${qs}` : ""}`),
        fetch(`${API}/analytics/trend`),
        fetch(`${API}/whatsapp-logs?replied=true${qs ? `&${qs}` : ""}&limit=9999`),
        // Only needed when a filter is active, to rebuild the day/month
        // trend + reply-rate charts for that period (/analytics/trend is
        // always last-7-days).
        year !== null ? fetch(`${API}/whatsapp-logs?${qs}&limit=9999`) : Promise.resolve(null),
      ]);
      const data  = await overviewRes.json();
      const trend = trendRes.ok ? await trendRes.json() : { whatsapp: [] };
      const sms   = data.whatsapp ?? {};   // backend key same "whatsapp", label change

      const sent      = sms.sent      ?? 0;
      const delivered = sms.delivered ?? 0;
      const read      = sms.read      ?? 0;
      const replied   = sms.replied   ?? 0;
      const failed    = sms.failed    ?? 0;

      setStats({
        sent, delivered, read, replied,
        replyRate:   sent ? Math.round((replied   / sent) * 100) : 0,
        deliverRate: sent ? Math.round((delivered / sent) * 100) : 0,
      });

      setStatusDist([
        { name: "Replied",   value: replied,                               color: "#4ade80" },
        { name: "Read",      value: Math.max(read - replied, 0),           color: "#f59e0b" },
        { name: "Delivered", value: Math.max(delivered - read, 0),         color: "#3b82f6" },
        { name: "Sent",      value: Math.max(sent - delivered - failed, 0), color: "#6b7280" },
        { name: "Failed",    value: failed,                                color: "#f87171" },
      ]);

      // ── activity trend + reply rate — last 7 days by default, or the
      //    selected year/month rebuilt client-side from raw logs ────────
      if (year === null) {
        const dayRows = trend.whatsapp ?? [];
        setTrendData(dayRows.map((d) => ({ day: d.day, sent: d.sent, replied: d.replied })));
        setReplyData(
          dayRows.map((d) => ({
            day: d.day,
            rate: d.sent ? Math.round((d.replied / d.sent) * 100) : 0,
          }))
        );
      } else if (rawLogsRes?.ok) {
        const rawData = await rawLogsRes.json();
        const rawLogs = Array.isArray(rawData) ? rawData : rawData.logs ?? [];
        const bucket = {};
        rawLogs.forEach((l) => {
          if (!l.sent_at) return;
          const key = month !== null ? l.sent_at.slice(8, 10) : l.sent_at.slice(5, 7);
          if (!bucket[key]) bucket[key] = { sent: 0, replied: 0 };
          bucket[key].sent += 1;
          if (l.replied) bucket[key].replied += 1;
        });
        let rows;
        if (month !== null) {
          const daysInMonth = new Date(year, month, 0).getDate();
          rows = Array.from({ length: daysInMonth }, (_, i) => {
            const dayKey = String(i + 1).padStart(2, "0");
            const b = bucket[dayKey] ?? { sent: 0, replied: 0 };
            return { day: String(i + 1), ...b };
          });
        } else {
          rows = MONTH_CHIPS.slice(1).map((label, i) => {
            const monthKey = String(i + 1).padStart(2, "0");
            const b = bucket[monthKey] ?? { sent: 0, replied: 0 };
            return { day: label, ...b };
          });
        }
        setTrendData(rows);
        setReplyData(rows.map((r) => ({ day: r.day, rate: r.sent ? Math.round((r.replied / r.sent) * 100) : 0 })));
      }

      // ── response time breakdown (how fast leads reply) ────────────────
      if (repliedLogsRes.ok) {
        const repliedData = await repliedLogsRes.json();
        const repliedLogs = Array.isArray(repliedData) ? repliedData : repliedData.logs ?? [];
        const counts = { under1h: 0, h1to6: 0, h6to24: 0, over24h: 0 };
        repliedLogs.forEach((log) => {
          if (!log.sent_at || !log.replied_at) return;
          const hours = (new Date(log.replied_at) - new Date(log.sent_at)) / 36e5;
          if (hours < 0) return;
          counts[bucketFor(hours).key] += 1;
        });
        const totalReplied = Object.values(counts).reduce((s, v) => s + v, 0);
        setResponseTimeData(
          RESPONSE_BUCKETS.map((b) => ({
            name: b.label,
            count: counts[b.key],
            pct: totalReplied ? Math.round((counts[b.key] / totalReplied) * 100) : 0,
            fill: b.color,
          }))
        );
      }
    } catch (e) {
      console.error("fetchStats:", e);
    } finally {
      setChartLoad(false);
    }
  }, [year, month]);

  useEffect(() => { fetchStats(); }, [fetchStats]);
  useEffect(() => { fetchLogs(); setSelected([]); }, [fetchLogs]);

  useEffect(() => {
    if (sendOpen) {
      const s = getSettings();
      setSendForm(f => ({
        ...f,
        message: f.message || s.smsTemplate || s.waTemplate || "",
      }));
    }
  }, [sendOpen]);

  // ── send SMS ──────────────────────────────────────────────────────────────
  const handleSend = async () => {
    setLaunching(true);
    try {
      const payload = {
        trigger: sendForm.trigger,
        lead_id: sendForm.lead_id ? parseInt(sendForm.lead_id) : null,
        dry_run: sendForm.dry_run,
        ...(templateMode === "custom" && { message: sendForm.message }),
      };
      await fetch(`${API}/pipeline/whatsapp`, {   // backend endpoint same rehta
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(payload),
      });
      setSendOpen(false);
      fetchStats();
      fetchLogs();
    } catch (e) {
      console.error("send:", e);
    } finally {
      setLaunching(false);
    }
  };

  // ── select ────────────────────────────────────────────────────────────────
  const toggleAll = () =>
    setSelected(selected.length === logs.length ? [] : logs.map(l => l.id));
  const toggleOne = id =>
    setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);

  const totalPages = Math.max(1, Math.ceil(total / LIMIT));

  return (
    <div className="wa-page">

      {/* ── month/year filter ── */}
      <div className="wa-toolbar">
        <div className="wa-toolbar-label">Filter by period</div>
        <MonthYearFilter year={year} month={month} onChange={(y, m) => { setYear(y); setMonth(m); setPage(1); }} />
      </div>

      {/* ── stat cards ── */}
      <div className="stat-grid">
        <StatCard
          icon="ti-message"
          color="#3b82f6" bg="rgba(59,130,246,0.1)"
          label="SMS sent"
          value={stats?.sent}
          sub="Via Twilio SMS"
          loading={chartLoad}
        />
        <StatCard
          icon="ti-checks"
          color="#3b82f6" bg="rgba(59,130,246,0.08)"
          label="Delivered"
          value={stats?.delivered}
          sub={<><b style={{ color: "#3b82f6" }}>{stats?.deliverRate ?? 0}%</b> delivery rate</>}
          loading={chartLoad}
        />
        <StatCard
          icon="ti-eye"
          color="#f59e0b" bg="rgba(245,158,11,0.08)"
          label="Read"
          value={stats?.read}
          sub="Lead opened message"
          loading={chartLoad}
        />
        <StatCard
          icon="ti-message-reply"
          color="#4ade80" bg="rgba(74,222,128,0.08)"
          label="Replied"
          value={stats?.replied}
          sub={<><b style={{ color: "#4ade80" }}>{stats?.replyRate ?? 0}%</b> reply rate</>}
          loading={chartLoad}
        />
      </div>

      {/* ── charts ── */}
      <div className="charts-grid">

        <div className="chart-panel chart-panel--wide">
          <div className="panel-header">
            <div>
              <div className="panel-title">SMS activity{year === null ? " — last 7 days" : ""}</div>
              <div className="panel-sub">
                {year === null
                  ? "Messages sent & replies received per day"
                  : month === null
                  ? `Messages sent & replied — ${year}`
                  : `Messages sent & replied — ${MONTH_CHIPS[month]} ${year}`}
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
                  <Line type="monotone" dataKey="sent"    name="Sent"    stroke="#3b82f6" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="replied" name="Replied" stroke="#4ade80" strokeWidth={2} dot={false} strokeDasharray="4 2" />
                </LineChart>
              </ResponsiveContainer>
            )}
            <div className="chart-legend">
              {[
                { color: "#3b82f6", label: "Sent" },
                { color: "#4ade80", label: "Replied", dashed: true },
              ].map((item, i) => (
                <div key={i} className="legend-item">
                  <span className="legend-dot" style={{ background: item.color }} />
                  {item.label}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="chart-panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Delivery funnel</div>
              <div className="panel-sub">
                {year === null ? "Message status breakdown" : month === null ? `${year} status breakdown` : `${MONTH_CHIPS[month]} ${year} status breakdown`}
              </div>
            </div>
          </div>
          <div className="chart-body donut-body">
            {chartLoad ? <div className="chart-skeleton" /> : (
              <>
                <ResponsiveContainer width={130} height={130}>
                  <PieChart>
                    <Pie
                      data={statusDist}
                      cx="50%" cy="50%"
                      innerRadius={38} outerRadius={58}
                      paddingAngle={3}
                      dataKey="value"
                      animationBegin={0}
                      animationDuration={900}
                    >
                      {statusDist.map((entry, i) => (
                        <Cell key={i} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v, n) => [v.toLocaleString(), n]} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="donut-legend">
                  {statusDist.map((t, i) => (
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

        <div className="chart-panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Reply rate by day</div>
              <div className="panel-sub">Best days to send SMS</div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? <div className="chart-skeleton" /> : (
              <ResponsiveContainer width="100%" height={150}>
                <BarChart data={replyData} barCategoryGap="30%">
                  <XAxis dataKey="day" tick={{ fontSize: 10, fill: "#999" }} axisLine={false} tickLine={false} />
                  <YAxis hide />
                  <Tooltip
                    content={<CUSTOM_TOOLTIP />}
                    formatter={(v) => [`${v}%`, "Reply rate"]}
                    cursor={{ fill: "rgba(255,255,255,0.03)" }}
                  />
                  <Bar dataKey="rate" name="Reply rate %" radius={[4, 4, 0, 0]}>
                    {replyData.map((entry, i) => (
                      <Cell
                        key={i}
                        fill={entry.rate >= 30 ? "#4ade80" : entry.rate >= 20 ? "#3b82f6" : "rgba(59,130,246,0.3)"}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
            <div className="chart-legend">
              <div className="legend-item"><span className="legend-dot" style={{ background: "#4ade80" }} />High ≥30%</div>
              <div className="legend-item"><span className="legend-dot" style={{ background: "#3b82f6" }} />Med ≥20%</div>
              <div className="legend-item"><span className="legend-dot" style={{ background: "rgba(59,130,246,0.3)" }} />Low</div>
            </div>
          </div>
        </div>

        <div className="chart-panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Response time breakdown</div>
              <div className="panel-sub">
                {year === null
                  ? "How fast leads reply after a message"
                  : month === null
                  ? `How fast leads replied — ${year}`
                  : `How fast leads replied — ${MONTH_CHIPS[month]} ${year}`}
              </div>
            </div>
          </div>
          <div className="chart-body donut-body">
            {chartLoad ? <div className="chart-skeleton" /> : responseTimeData.every((d) => d.count === 0) ? (
              <div style={{ color: "rgba(255,255,255,0.2)", fontSize: 12, textAlign: "center", paddingTop: 40 }}>
                No replies yet
              </div>
            ) : (
              <>
                <div style={{ position: "relative", width: 150, height: 150 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <RadialBarChart
                      data={responseTimeData}
                      innerRadius="30%" outerRadius="100%"
                      startAngle={90} endAngle={-270}
                      barCategoryGap={6}
                    >
                      <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
                      <RadialBar
                        dataKey="pct" cornerRadius={8} background={{ fill: "rgba(255,255,255,0.05)" }}
                        animationDuration={900}
                      >
                        {responseTimeData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
                      </RadialBar>
                      <Tooltip
                        content={({ active, payload }) => {
                          if (!active || !payload?.length) return null;
                          const d = payload[0].payload;
                          return (
                            <div className="chart-tooltip">
                              <div className="ct-label">{d.name}</div>
                              <div className="ct-val">{d.count.toLocaleString()} replies ({d.pct}%)</div>
                            </div>
                          );
                        }}
                      />
                    </RadialBarChart>
                  </ResponsiveContainer>
                  <div
                    style={{
                      position: "absolute", top: "50%", left: "50%",
                      transform: "translate(-50%, -50%)", textAlign: "center", pointerEvents: "none",
                    }}
                  >
                    <div style={{ fontSize: 18, fontWeight: 800, color: "#fff" }}>
                      {responseTimeData.reduce((s, d) => s + d.count, 0)}
                    </div>
                    <div style={{ fontSize: 9, color: "rgba(255,255,255,0.4)" }}>replies</div>
                  </div>
                </div>
                <div className="donut-legend">
                  {responseTimeData.map((d, i) => (
                    <div key={i} className="dl-item">
                      <span className="dl-dot" style={{ background: d.fill }} />
                      <span className="dl-name">{d.name}</span>
                      <span className="dl-val">{d.count.toLocaleString()}</span>
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
            <div className="panel-title">Message log</div>
            <div className="panel-sub">All SMS messages sent via Twilio</div>
          </div>
          <div className="table-actions">
            <select className="filter-select" value={status} onChange={e => { setStatus(e.target.value); setPage(1); }}>
              <option value="">All status</option>
              <option value="sent">Sent</option>
              <option value="delivered">✓ Delivered</option>
              <option value="read">👁 Read</option>
              <option value="failed">✗ Failed</option>
            </select>
            <select className="filter-select" value={repliedF} onChange={e => { setRepliedF(e.target.value); setPage(1); }}>
              <option value="">All replies</option>
              <option value="true">Replied ✅</option>
              <option value="false">No reply</option>
            </select>
            <button className="btn-ghost" onClick={() => window.open(`${API}/whatsapp-logs/export`, "_blank")}>
              <i className="ti ti-download" /> <span>Export</span>
            </button>
            <button className="btn-primary btn-wa" onClick={() => setSendOpen(true)}>
              <i className="ti ti-message" /> <span>Send SMS</span>
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
                    style={{ accentColor: "#3b82f6" }}
                  />
                </th>
                <th>Lead</th>
                <th>Message preview</th>
                <th>Status</th>
                <th>Replied</th>
                <th>Sent at</th>
                <th>Replied at</th>
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
                        <i className="ti ti-message" style={{ color: "#3b82f6" }} />
                        <p>No messages yet</p>
                        <span>Send SMS follow-ups to leads who haven't replied to email</span>
                      </td>
                    </tr>
                  )
                : logs.map(log => {
                    const si = statusInfo(log.delivery_status);
                    return (
                      <tr key={log.id} className={selected.includes(log.id) ? "row-selected" : ""}>
                        <td>
                          <input
                            type="checkbox"
                            checked={selected.includes(log.id)}
                            onChange={() => toggleOne(log.id)}
                            style={{ accentColor: "#3b82f6" }}
                          />
                        </td>
                        <td>
                          <div className="biz-name">Lead #{log.lead_id}</div>
                          <div className="biz-src">
                            <span className="src-dot" style={{ background: "#3b82f6" }} />
                            Twilio SMS
                          </div>
                        </td>
                        <td>
                          <div className="wa-msg-preview">
                            {log.message_content
                              ? log.message_content.slice(0, 80) + (log.message_content.length > 80 ? "…" : "")
                              : "—"}
                          </div>
                        </td>
                        <td>
                          <span className={`wa-status-badge ${si.cls}`}>{si.label}</span>
                        </td>
                        <td>
                          {log.replied
                            ? <span className="replied-yes">✅ Yes</span>
                            : <span className="replied-no">—</span>}
                        </td>
                        <td className="date-cell">{formatDate(log.sent_at)}</td>
                        <td className="date-cell">
                          {log.replied_at
                            ? <span style={{ color: "#4ade80" }}>{formatDate(log.replied_at)}</span>
                            : <span style={{ color: "rgba(255,255,255,0.2)" }}>—</span>}
                        </td>
                        <td>
                          <div className="row-actions">
                            {log.replied && log.reply_content && (
                              <button className="btn-ghost btn-xs" title="View reply" onClick={() => setReplyLog(log)}>
                                <i className="ti ti-message" />
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

        <div className="pagination">
          <div className="pag-info">
            Showing {Math.min((page - 1) * LIMIT + 1, total)}–{Math.min(page * LIMIT, total)} of {total.toLocaleString()} messages
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

      {/* ══════════════════════════════════════════════════════════════════════
          SEND SMS MODAL
      ══════════════════════════════════════════════════════════════════════ */}
      {sendOpen && (
        <div className="modal-overlay" onClick={() => setSendOpen(false)}>
          <div className="modal wa-modal" onClick={e => e.stopPropagation()}>

            <div className="modal-header">
              <div>
                <div className="modal-title">Send SMS messages</div>
                <div className="modal-sub">Twilio sends personalized SMS follow-ups to leads</div>
              </div>
              <button className="modal-close" onClick={() => setSendOpen(false)}>
                <i className="ti ti-x" />
              </button>
            </div>

            <div className="modal-body">

              {/* ── Trigger ── */}
              <div className="eo-section-label">
                <i className="ti ti-bolt" /> Trigger
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label>Trigger mode</label>
                  <select
                    value={sendForm.trigger}
                    onChange={e => setSendForm(f => ({ ...f, trigger: e.target.value }))}
                    className="filter-select"
                    style={{ width: "100%", padding: "10px 14px" }}
                  >
                    <option value="auto">Auto — leads who didn't reply to email</option>
                    <option value="all">All — every qualifying lead</option>
                    <option value="manual">Manual — specific lead ID</option>
                  </select>
                </div>
                <div className="form-group">
                  <label>
                    Lead ID{" "}
                    {sendForm.trigger !== "manual" && (
                      <span style={{ color: "rgba(255,255,255,0.3)", fontSize: 11 }}>(manual only)</span>
                    )}
                  </label>
                  <input
                    type="number"
                    placeholder="Leave blank for bulk"
                    disabled={sendForm.trigger !== "manual"}
                    value={sendForm.lead_id}
                    onChange={e => setSendForm(f => ({ ...f, lead_id: e.target.value }))}
                    style={{ opacity: sendForm.trigger !== "manual" ? 0.4 : 1 }}
                  />
                </div>
              </div>

              {/* ── Template ── */}
              <div className="eo-section-label" style={{ marginTop: 20 }}>
                <i className="ti ti-message" /> SMS template
                <div className="eo-template-actions">
                  <button
                    type="button"
                    className={`eo-tpl-tab ${templateMode === "settings" ? "eo-tpl-tab--active" : ""}`}
                    onClick={handleLoadTemplate}
                    title="Load template from Settings"
                  >
                    <i className="ti ti-download" />
                    Load from Settings
                    {templateLoaded && <span className="eo-loaded-tick"> ✓</span>}
                  </button>
                  <button
                    type="button"
                    className={`eo-tpl-tab ${templateMode === "custom" ? "eo-tpl-tab--active" : ""}`}
                    onClick={() => setTemplateMode("custom")}
                  >
                    <i className="ti ti-pencil" />
                    Custom
                  </button>
                </div>
              </div>

              {templateMode === "settings" ? (
                <div className="eo-tpl-preview">
                  <div className="eo-tpl-row eo-tpl-row--body">
                    <span className="eo-tpl-lbl">Message</span>
                    <pre className="eo-tpl-body">{sendForm.message || <em style={{ opacity: 0.4 }}>—</em>}</pre>
                  </div>
                  <div className="eo-tpl-hint">
                    <i className="ti ti-info-circle" />
                    Edit this in <strong>Settings → Templates → SMS</strong>.
                    Variables like <code>{"{{name}}"}</code> are replaced per lead.
                  </div>
                </div>
              ) : (
                <div className="eo-tpl-editor">
                  <div className="form-group">
                    <label>Message body <span style={{ color: "rgba(255,255,255,0.35)", fontSize: 11 }}>(max 160 chars for single SMS)</span></label>
                    <VarChips onInsert={insertMsgVar} />
                    <textarea
                      className="eo-textarea"
                      rows={4}
                      placeholder="Hi {{name}}, this is {{sender}} from EcomDrop..."
                      value={sendForm.message}
                      onChange={e => setSendForm(f => ({ ...f, message: e.target.value }))}
                      maxLength={320}
                    />
                    <div style={{ fontSize: 11, color: sendForm.message?.length > 160 ? "#f59e0b" : "rgba(255,255,255,0.3)", marginTop: 4 }}>
                      {sendForm.message?.length ?? 0} / 160 chars
                      {sendForm.message?.length > 160 && " — will send as 2 SMS"}
                    </div>
                  </div>
                  <div className="eo-tpl-hint">
                    <i className="ti ti-info-circle" />
                    One-off message. Save permanently in <strong>Settings → Templates</strong>.
                  </div>
                </div>
              )}

              {/* ── Dry run ── */}
              <div style={{ marginTop: 16 }}>
                <div className="form-group form-group--check">
                  <label>Dry run (test mode)</label>
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={sendForm.dry_run}
                      onChange={e => setSendForm(f => ({ ...f, dry_run: e.target.checked }))}
                    />
                    <span className="toggle-track"><span className="toggle-thumb" /></span>
                    <span className="toggle-text">
                      {sendForm.dry_run ? "Test only — no real messages" : "Live — will send real SMS"}
                    </span>
                  </label>
                </div>
              </div>

              <div className="modal-note wa-note">
                <i className="ti ti-info-circle" style={{ color: "#3b82f6" }} />
                <span>
                  {sendForm.trigger === "auto"
                    ? "Only leads who received an email but haven't replied in 48h will get an SMS. DNC is checked automatically."
                    : sendForm.trigger === "all"
                    ? "All leads with a valid phone number will be messaged. Use with caution."
                    : "Only the specified lead will be messaged. Good for testing a specific contact."}
                </span>
              </div>

            </div>

            <div className="modal-footer">
              <button className="btn-ghost" onClick={() => setSendOpen(false)}>Cancel</button>
              <button
                className={`btn-primary btn-wa ${sendForm.dry_run ? "btn-dry-wa" : ""}`}
                onClick={handleSend}
                disabled={launching}
              >
                {launching
                  ? <><i className="ti ti-loader-2 spin" /> Sending...</>
                  : sendForm.dry_run
                  ? <><i className="ti ti-flask" /> Run dry test</>
                  : <><i className="ti ti-message" /> Send SMS</>
                }
              </button>
            </div>

          </div>
        </div>
      )}

      {/* ── reply preview modal ── */}
      {replyLog && (
        <div className="modal-overlay" onClick={() => setReplyLog(null)}>
          <div className="modal modal--sm" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <div className="modal-title">Reply from Lead #{replyLog.lead_id}</div>
                <div className="modal-sub">Received {formatDate(replyLog.replied_at)}</div>
              </div>
              <button className="modal-close" onClick={() => setReplyLog(null)}>
                <i className="ti ti-x" />
              </button>
            </div>
            <div className="modal-body">
              <div className="wa-bubble-wrap">
                <div className="wa-bubble wa-out">
                  <div className="wa-bubble-label">You (Twilio)</div>
                  <div className="wa-bubble-body">{replyLog.message_content}</div>
                  <div className="wa-bubble-time">{formatDate(replyLog.sent_at)}</div>
                </div>
                <div className="wa-bubble wa-in">
                  <div className="wa-bubble-label">Lead #{replyLog.lead_id}</div>
                  <div className="wa-bubble-body">{replyLog.reply_content}</div>
                  <div className="wa-bubble-time">{formatDate(replyLog.replied_at)}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      <LeadDetailModal leadId={viewLeadId} onClose={() => setViewLeadId(null)} />
    </div>
  );
}