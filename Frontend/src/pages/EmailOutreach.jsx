import { useState, useEffect, useCallback } from "react";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Cell, PieChart, Pie, CartesianGrid,
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Legend,
} from "recharts";
import { getSettings, fmtDate } from "../utils/settings";
import LeadDetailModal from "../components/LeadDetailModal";
import "./EmailOutreach.css";

import { downloadFile } from "../auth";
const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ─── helpers ──────────────────────────────────────────────────────────────────
function statusClass(status) {
  const map = {
    sent:         "es-sent",
    opened:       "es-opened",
    replied:      "es-replied",
    bounced:      "es-bounced",
    unsubscribed: "es-unsub",
  };
  return map[status] ?? "es-sent";
}
function statusLabel(status) {
  const map = {
    sent:         "Sent",
    opened:       "📬 Opened",
    replied:      "✅ Replied",
    bounced:      "✗ Bounced",
    unsubscribed: "— Unsub",
  };
  return map[status] ?? status ?? "—";
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
      <div className="sc-value" style={{ color: color === "#7c3aed" ? "var(--wht)" : color }}>
        {loading ? <span className="skeleton w-16" /> : (value ?? 0).toLocaleString()}
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
        <div key={i} className="ct-val" style={{ color: p.color ?? "var(--wht)" }}>
          {p.name}: <b>{p.value?.toLocaleString()}</b>
        </div>
      ))}
    </div>
  );
};

const MONTH_CHIPS = ["All", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CHIP_W = 34;

/**
 * MonthYearFilter — sliding capsule: year stepper on the left, month
 * ribbon on the right with a single glowing pill that glides between
 * chips. Filters the stat cards, charts, and the email log table below.
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

// ─── variable chip helper ─────────────────────────────────────────────────────
const VARS = ["name", "business", "city", "niche", "sender"];

function VarChips({ onInsert }) {
  return (
    <div className="eo-var-chips">
      <span className="eo-var-label">Insert:</span>
      {VARS.map(v => (
        <button
          key={v}
          type="button"
          className="eo-var-chip"
          onClick={() => onInsert(`{{${v}}}`)}
          title={`Insert {{${v}}}`}
        >
          {`{{${v}}}`}
        </button>
      ))}
    </div>
  );
}

// ─── main page ────────────────────────────────────────────────────────────────
export default function EmailOutreach() {
  // data
  const [logs,       setLogs]       = useState([]);
  const [total,      setTotal]      = useState(0);
  const [loading,    setLoading]    = useState(true);
  const [chartLoad,  setChartLoad]  = useState(true);
  const [stats,      setStats]      = useState(null);
  const [trendData,  setTrendData]  = useState([]);
  const [statusDist, setStatusDist] = useState([]);
  const [nicheData,  setNicheData]  = useState([]);
  const [rateData,   setRateData]   = useState([]);

  // filters
  const [status,   setStatus]   = useState("");
  const [year,     setYear]     = useState(null);
  const [month,    setMonth]    = useState(null);
  const [page,     setPage]     = useState(1);
  const [selected, setSelected] = useState([]);
  const LIMIT = 10;
  const [viewLeadId, setViewLeadId] = useState(null);

  // compose modal
  const [composeOpen, setComposeOpen] = useState(false);
  const [launching,   setLaunching]   = useState(false);

  // ── template mode: "settings" | "custom" ─────────────────────────────────
  const [templateMode, setTemplateMode] = useState("settings");
  const [templateLoaded, setTemplateLoaded] = useState(false);

  // campaign form
  const [form, setForm] = useState(() => {
    const s = getSettings();
    return {
      min_score:    s.warmThreshold ?? 40,
      dry_run:      false,
      niche:        s.defaultNiche  ?? "",
      city:         s.defaultCity   ?? "",
      // template fields (used in custom mode)
      subject:      s.emailSubject  ?? "",
      body:         s.emailBody     ?? "",
      email_delay:  s.emailDelay    ?? 3,
      max_per_day:  s.maxEmailsDay  ?? 100,
    };
  });

  // Load template from Settings into form
  const handleLoadTemplate = () => {
    const s = getSettings();
    // {{sender}} in the template resolves to adminName (falls back to the
    // company name if no admin name is set in Settings → General)
    const senderName = s.adminName?.trim() || s.companyName || "the team";
    const withSender = (txt) => (txt ?? "").replace(/\{\{sender\}\}/g, senderName);
    setForm(f => ({
      ...f,
      subject:     withSender(s.emailSubject ?? f.subject),
      body:        withSender(s.emailBody    ?? f.body),
      email_delay: s.emailDelay   ?? f.email_delay,
      max_per_day: s.maxEmailsDay ?? f.max_per_day,
    }));
    setTemplateMode("settings");
    setTemplateLoaded(true);
    setTimeout(() => setTemplateLoaded(false), 2500);
  };

  // Insert variable into body textarea
  const insertBodyVar = (tag) => {
    setForm(f => ({ ...f, body: (f.body || "") + tag }));
  };

  // ── fetch email logs ────────────────────────────────────────────────────────
  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        skip:  (page - 1) * LIMIT,
        limit: LIMIT,
        ...(status && { status }),
        ...(year !== null && { year: String(year) }),
        ...(year !== null && month !== null && { month: String(month) }),
      });
      const res  = await fetch(`${API}/email-logs?${params}`);
      const data = await res.json();
      setLogs(Array.isArray(data) ? data : data.logs ?? []);
      setTotal(data.total ?? (Array.isArray(data) ? data.length : 0));
    } catch (e) {
      console.error("fetchLogs:", e);
    } finally {
      setLoading(false);
    }
  }, [page, status, year, month]);

  // ── fetch stats + charts ────────────────────────────────────────────────────
  const fetchStats = useCallback(async () => {
    setChartLoad(true);
    try {
      const dq = new URLSearchParams();
      if (year !== null) {
        dq.set("year", year);
        if (month !== null) dq.set("month", month);
      }
      const qs = dq.toString();

      const [overviewRes, trendRes, nicheRes, rawLogsRes] = await Promise.all([
        fetch(`${API}/analytics/overview${qs ? `?${qs}` : ""}`),
        fetch(`${API}/analytics/trend`),
        fetch(`${API}/analytics/niche-breakdown${qs ? `?${qs}` : ""}`),
        // Only needed when a filter is active, to rebuild the trend chart
        // for that period (the /analytics/trend endpoint is always last-7-days).
        year !== null ? fetch(`${API}/email-logs?${qs}&limit=9999`) : Promise.resolve(null),
      ]);
      const data  = await overviewRes.json();
      const trend = trendRes.ok ? await trendRes.json() : { emails: [] };
      const niche = nicheRes.ok ? await nicheRes.json() : [];
      const em    = data.emails ?? {};

      // NOTE: "bounced" isn't tracked in the DB yet (no bounced column on
      // EmailLog), so this always reads 0 from the backend until that's added.
      setStats({
        sent:      em.sent      ?? 0,
        opened:    em.opened    ?? 0,
        replied:   em.replied   ?? 0,
        openRate:  em.open_rate  ?? 0,
        replyRate: em.reply_rate ?? 0,
        bounced:   em.bounced   ?? 0,
      });

      const sent    = em.sent    ?? 0;
      const opened  = em.opened  ?? 0;
      const replied = em.replied ?? 0;
      const bounced = em.bounced ?? 0;
      const pending = Math.max(sent - opened - replied - bounced, 0);
      setStatusDist([
        { name: "Replied",  value: replied, color: "#4ade80" },
        { name: "Opened",   value: opened,  color: "#7c3aed" },
        { name: "Pending",  value: pending, color: "#3b82f6" },
        { name: "Bounced",  value: bounced, color: "#f87171" },
      ]);

      // ── activity trend — last 7 days by default, or the selected
      //    year/month rebuilt client-side from raw logs ──────────────────
      if (year === null) {
        setTrendData(
          (trend.emails ?? []).map((d) => ({
            day: d.day,
            sent: d.sent,
            opened: d.opened,
            replied: d.replied,
          }))
        );
      } else if (rawLogsRes?.ok) {
        const rawData = await rawLogsRes.json();
        const rawLogs = Array.isArray(rawData) ? rawData : rawData.logs ?? [];
        const bucket = {};
        rawLogs.forEach((l) => {
          if (!l.sent_at) return;
          const key = month !== null ? l.sent_at.slice(8, 10) : l.sent_at.slice(5, 7);
          if (!bucket[key]) bucket[key] = { sent: 0, opened: 0, replied: 0 };
          bucket[key].sent += 1;
          if (l.email_opened) bucket[key].opened += 1;
          if (l.replied) bucket[key].replied += 1;
        });
        let rows;
        if (month !== null) {
          const daysInMonth = new Date(year, month, 0).getDate();
          rows = Array.from({ length: daysInMonth }, (_, i) => {
            const dayKey = String(i + 1).padStart(2, "0");
            const b = bucket[dayKey] ?? { sent: 0, opened: 0, replied: 0 };
            return { day: String(i + 1), ...b };
          });
        } else {
          rows = MONTH_CHIPS.slice(1).map((label, i) => {
            const monthKey = String(i + 1).padStart(2, "0");
            const b = bucket[monthKey] ?? { sent: 0, opened: 0, replied: 0 };
            return { day: label, ...b };
          });
        }
        setTrendData(rows);
      }

      // niche breakdown — real counts grouped by lead niche
      setNicheData(
        niche.map((n) => ({
          niche: n.niche,
          sent: n.sent,
          replied: n.replied,
        }))
      );

      // open rate vs reply rate per niche — % of sent that were opened/replied
      setRateData(
        niche.map((n) => ({
          niche: n.niche,
          openRate:  n.sent ? Math.round(((n.opened  ?? 0) / n.sent) * 100) : 0,
          replyRate: n.sent ? Math.round(((n.replied ?? 0) / n.sent) * 100) : 0,
        }))
      );
    } catch (e) {
      console.error("fetchStats:", e);
    } finally {
      setChartLoad(false);
    }
  }, [year, month]);

  useEffect(() => { fetchStats(); }, [fetchStats]);
  useEffect(() => { fetchLogs(); setSelected([]); }, [fetchLogs]);

  // Re-sync defaults from settings when modal opens
  useEffect(() => {
    if (composeOpen) {
      const s = getSettings();
      setForm(f => ({
        ...f,
        niche:       f.niche       || s.defaultNiche  || "",
        city:        f.city        || s.defaultCity    || "",
        min_score:   f.min_score   || s.warmThreshold  || 40,
        email_delay: f.email_delay || s.emailDelay     || 3,
        max_per_day: f.max_per_day || s.maxEmailsDay   || 100,
      }));
    }
  }, [composeOpen]);

  // ── launch outreach ─────────────────────────────────────────────────────────
  const handleLaunch = async () => {
    setLaunching(true);
    try {
      const payload = {
        min_score: form.min_score,
        dry_run:   form.dry_run,
        niche:     form.niche,
        city:      form.city,
        // include template only in custom mode
        ...(templateMode === "custom" && {
          subject:      form.subject,
          body:         form.body,
          email_delay:  form.email_delay,
          max_per_day:  form.max_per_day,
        }),
      };
      await fetch(`${API}/pipeline/send-emails`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(payload),
      });
      setComposeOpen(false);
      fetchStats();
      fetchLogs();
    } catch (e) {
      console.error("launch:", e);
    } finally {
      setLaunching(false);
    }
  };

  // ── select ──────────────────────────────────────────────────────────────────
  const toggleAll = () =>
    setSelected(selected.length === logs.length ? [] : logs.map(l => l.id));
  const toggleOne = id =>
    setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);

  const totalPages = Math.max(1, Math.ceil(total / LIMIT));

  return (
    <div className="email-page">

      {/* ── month/year filter ── */}
      <div className="eo-toolbar">
        <div className="eo-toolbar-label">Filter by period</div>
        <MonthYearFilter year={year} month={month} onChange={(y, m) => { setYear(y); setMonth(m); setPage(1); }} />
      </div>

      {/* ── stat cards ── */}
      <div className="stat-grid">
        <StatCard
          icon="ti-send"
          color="#7c3aed" bg="rgba(124,58,237,0.1)"
          label="Emails sent"
          value={stats?.sent}
          sub={<><b style={{ color: "#7c3aed" }}>via Instantly</b></>}
          loading={chartLoad}
        />
        <StatCard
          icon="ti-mail-opened"
          color="#f59e0b" bg="rgba(245,158,11,0.1)"
          label="Opened"
          value={stats?.opened}
          sub={<><b style={{ color: "#f59e0b" }}>{stats?.openRate ?? 0}%</b> open rate</>}
          loading={chartLoad}
        />
        <StatCard
          icon="ti-message-check"
          color="#4ade80" bg="rgba(74,222,128,0.08)"
          label="Replied"
          value={stats?.replied}
          sub={<><b style={{ color: "#4ade80" }}>{stats?.replyRate ?? 0}%</b> reply rate</>}
          loading={chartLoad}
        />
        <StatCard
          icon="ti-mail-x"
          color="#f87171" bg="rgba(248,113,113,0.08)"
          label="Bounced"
          value={stats?.bounced}
          sub="Invalid / unreachable"
          loading={chartLoad}
        />
      </div>

      {/* ── charts row ── */}
      <div className="charts-grid eo-charts-grid">

        <div className="chart-panel chart-panel--wide eo-gp-wide">
          <div className="panel-header">
            <div>
              <div className="panel-title">Email activity{year === null ? " — last 7 days" : ""}</div>
              <div className="panel-sub">
                {year === null
                  ? "Sent, opened & replied per day"
                  : month === null
                  ? `Sent, opened & replied — ${year}`
                  : `Sent, opened & replied — ${MONTH_CHIPS[month]} ${year}`}
              </div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? <div className="chart-skeleton" /> : (
              <ResponsiveContainer width="100%" height={150}>
                <LineChart data={trendData}>
                  <CartesianGrid stroke="rgba(var(--ov),0.05)" strokeDasharray="3 3" />
                  <XAxis dataKey="day" tick={{ fontSize: 10, fill: "#999" }} axisLine={false} tickLine={false} />
                  <YAxis hide />
                  <Tooltip content={<CUSTOM_TOOLTIP />} cursor={{ stroke: "rgba(var(--ov),0.08)" }} />
                  <Line type="monotone" dataKey="sent"    name="Sent"    stroke="#7c3aed" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="opened"  name="Opened"  stroke="#f59e0b" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="replied" name="Replied" stroke="#4ade80" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
            <div className="chart-legend">
              {[
                { color: "#7c3aed", label: "Sent" },
                { color: "#f59e0b", label: "Opened" },
                { color: "#4ade80", label: "Replied" },
              ].map((item, i) => (
                <div key={i} className="legend-item">
                  <span className="legend-dot" style={{ background: item.color }} />
                  {item.label}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="chart-panel eo-gp-status">
          <div className="panel-header">
            <div>
              <div className="panel-title">Status breakdown</div>
              <div className="panel-sub">
                {year === null ? "All-time distribution" : month === null ? `${year} distribution` : `${MONTH_CHIPS[month]} ${year} distribution`}
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

        <div className="chart-panel eo-gp-niche">
          <div className="panel-header">
            <div>
              <div className="panel-title">Replies by niche</div>
              <div className="panel-sub">Campaign performance</div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? <div className="chart-skeleton" /> : (
              <ResponsiveContainer width="100%" height={150}>
                <BarChart data={nicheData} barCategoryGap="30%">
                  <XAxis dataKey="niche" tick={{ fontSize: 10, fill: "#999" }} axisLine={false} tickLine={false} />
                  <YAxis hide />
                  <Tooltip content={<CUSTOM_TOOLTIP />} cursor={{ fill: "rgba(0,0,0,0.03)" }} />
                  <Bar dataKey="sent"    name="Sent"    fill="rgba(124,58,237,0.25)" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="replied" name="Replied" fill="#4ade80"              radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
            <div className="chart-legend">
              <div className="legend-item"><span className="legend-dot" style={{ background: "rgba(124,58,237,0.5)" }} />Sent</div>
              <div className="legend-item"><span className="legend-dot" style={{ background: "#4ade80" }} />Replied</div>
            </div>
          </div>
        </div>

        <div className="chart-panel eo-gp-new">
          <div className="panel-header">
            <div>
              <div className="panel-title">Open rate vs reply rate</div>
              <div className="panel-sub">% of sent emails, by niche</div>
            </div>
          </div>
          <div className="chart-body">
            {chartLoad ? <div className="chart-skeleton" /> : (
              <ResponsiveContainer width="100%" height={190}>
                <RadarChart data={rateData} outerRadius="70%">
                  <PolarGrid stroke="rgba(var(--ov),0.08)" />
                  <PolarAngleAxis dataKey="niche" tick={{ fontSize: 10, fill: "#999" }} />
                  <PolarRadiusAxis domain={[0, 100]} tick={{ fontSize: 8, fill: "#666" }} axisLine={false} />
                  <Tooltip content={<CUSTOM_TOOLTIP />} />
                  <Radar name="Open rate %"  dataKey="openRate"  stroke="#f59e0b" fill="#f59e0b" fillOpacity={0.35} />
                  <Radar name="Reply rate %" dataKey="replyRate" stroke="#4ade80" fill="#4ade80" fillOpacity={0.35} />
                </RadarChart>
              </ResponsiveContainer>
            )}
            <div className="chart-legend">
              <div className="legend-item"><span className="legend-dot" style={{ background: "#f59e0b" }} />Open rate %</div>
              <div className="legend-item"><span className="legend-dot" style={{ background: "#4ade80" }} />Reply rate %</div>
            </div>
          </div>
        </div>
      </div>

      {/* ── email logs table ── */}
      <div className="table-panel">
        <div className="table-header">
          <div>
            <div className="panel-title">Email log</div>
            <div className="panel-sub">All outreach emails sent via Instantly</div>
          </div>
          <div className="table-actions">
            <select className="filter-select" value={status} onChange={e => { setStatus(e.target.value); setPage(1); }}>
              <option value="">All status</option>
              <option value="sent">Sent</option>
              <option value="opened">Opened</option>
              <option value="replied">Replied</option>
              <option value="bounced">Bounced</option>
              <option value="unsubscribed">Unsubscribed</option>
            </select>
            <button className="btn-ghost" onClick={() => downloadFile("/email-logs/export", "email-logs.csv")}>
              <i className="ti ti-download" /> <span>Export</span>
            </button>
            <button className="btn-primary" onClick={() => setComposeOpen(true)}>
              <i className="ti ti-send" /> <span>Launch outreach</span>
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
                    style={{ accentColor: "#7c3aed" }}
                  />
                </th>
                <th>Recipient</th>
                <th>Subject</th>
                <th>Status</th>
                <th>Sent at</th>
                <th>Opened at</th>
                <th>Replied at</th>
                <th>Campaign</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading
                ? Array.from({ length: 6 }).map((_, i) => (
                    <tr key={i} className="skeleton-row">
                      {Array.from({ length: 9 }).map((_, j) => (
                        <td key={j}><div className="skeleton" /></td>
                      ))}
                    </tr>
                  ))
                : logs.length === 0
                ? (
                    <tr>
                      <td colSpan={9} className="empty-state">
                        <i className="ti ti-mail-off" />
                        <p>No emails found</p>
                        <span>Launch an outreach campaign to get started</span>
                      </td>
                    </tr>
                  )
                : logs.map(log => {
                    const st =
                      log.unsubscribed ? "unsubscribed" :
                      log.replied      ? "replied" :
                      log.email_opened ? "opened" : "sent";
                    return (
                      <tr key={log.id} className={selected.includes(log.id) ? "row-selected" : ""}>
                        <td>
                          <input
                            type="checkbox"
                            checked={selected.includes(log.id)}
                            onChange={() => toggleOne(log.id)}
                            style={{ accentColor: "#7c3aed" }}
                          />
                        </td>
                        <td>
                          <div className="biz-name">{log.personalized_for ?? `Lead #${log.lead_id}`}</div>
                          <div className="biz-src">
                            <span className="src-dot" style={{ background: "#7c3aed" }} />
                            Lead ID: {log.lead_id}
                          </div>
                        </td>
                        <td><div className="email-subject">{log.subject ?? "—"}</div></td>
                        <td>
                          <span className={`email-status-badge ${statusClass(st)}`}>
                            {statusLabel(st)}
                          </span>
                        </td>
                        <td className="date-cell">{formatDate(log.sent_at)}</td>
                        <td className="date-cell">{formatDate(log.opened_at)}</td>
                        <td className="date-cell">
                          {log.replied_at
                            ? <span style={{ color: "#4ade80" }}>{formatDate(log.replied_at)}</span>
                            : <span style={{ color: "rgba(var(--ov),0.2)" }}>—</span>}
                        </td>
                        <td>
                          {log.campaign_id
                            ? <span className="stage-badge">{log.campaign_id}</span>
                            : <span style={{ color: "rgba(var(--ov),0.2)" }}>—</span>}
                        </td>
                        <td>
                          <div className="row-actions">
                            {log.replied && (
                              <button className="btn-ghost btn-xs" title="View reply">
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
            Showing {Math.min((page - 1) * LIMIT + 1, total)}–{Math.min(page * LIMIT, total)} of {total.toLocaleString()} emails
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
          LAUNCH OUTREACH MODAL
      ══════════════════════════════════════════════════════════════════════ */}
      {composeOpen && (
        <div className="modal-overlay" onClick={() => setComposeOpen(false)}>
          <div className="modal eo-modal" onClick={e => e.stopPropagation()}>

            <div className="modal-header">
              <div>
                <div className="modal-title">Launch email outreach</div>
                <div className="modal-sub">Groq AI personalizes emails → sent via Instantly</div>
              </div>
              <button className="modal-close" onClick={() => setComposeOpen(false)}>
                <i className="ti ti-x" />
              </button>
            </div>

            <div className="modal-body">

              {/* ── Targeting ── */}
              <div className="eo-section-label">
                <i className="ti ti-crosshair" /> Targeting
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label>Min lead score</label>
                  <input
                    type="number" min="0" max="100"
                    value={form.min_score}
                    onChange={e => setForm(f => ({ ...f, min_score: parseInt(e.target.value) || 0 }))}
                  />
                </div>
                <div className="form-group">
                  <label>Niche <span className="opt-tag">(optional)</span></label>
                  <input
                    placeholder="e.g. toys"
                    value={form.niche}
                    onChange={e => setForm(f => ({ ...f, niche: e.target.value }))}
                  />
                </div>
                <div className="form-group">
                  <label>City <span className="opt-tag">(optional)</span></label>
                  <input
                    placeholder="e.g. Miami FL"
                    value={form.city}
                    onChange={e => setForm(f => ({ ...f, city: e.target.value }))}
                  />
                </div>
              </div>

              {/* ── Template ── */}
              <div className="eo-section-label" style={{ marginTop: 20 }}>
                <i className="ti ti-template" /> Email template
                <div className="eo-template-actions">
                  <button
                    type="button"
                    className={`eo-tpl-tab ${templateMode === "settings" ? "eo-tpl-tab--active" : ""}`}
                    onClick={handleLoadTemplate}
                    title="Load template saved in Settings page"
                  >
                    <i className="ti ti-download" />
                    Load from Settings
                    {templateLoaded && <span className="eo-loaded-tick"> ✓</span>}
                  </button>
                  <button
                    type="button"
                    className={`eo-tpl-tab ${templateMode === "custom" ? "eo-tpl-tab--active" : ""}`}
                    onClick={() => setTemplateMode("custom")}
                    title="Write a one-off custom template"
                  >
                    <i className="ti ti-pencil" />
                    Custom
                  </button>
                </div>
              </div>

              {templateMode === "settings" ? (
                /* ── Settings template preview ── */
                <div className="eo-tpl-preview">
                  <div className="eo-tpl-row">
                    <span className="eo-tpl-lbl">Subject</span>
                    <span className="eo-tpl-val">{form.subject || <em style={{ opacity: 0.4 }}>—</em>}</span>
                  </div>
                  <div className="eo-tpl-row eo-tpl-row--body">
                    <span className="eo-tpl-lbl">Body</span>
                    <pre className="eo-tpl-body">{form.body || <em style={{ opacity: 0.4 }}>—</em>}</pre>
                  </div>
                  <div className="eo-tpl-row">
                    <span className="eo-tpl-lbl">Delay</span>
                    <span className="eo-tpl-val">{form.email_delay}s between emails</span>
                  </div>
                  <div className="eo-tpl-row">
                    <span className="eo-tpl-lbl">Daily cap</span>
                    <span className="eo-tpl-val">{form.max_per_day} emails/day</span>
                  </div>
                  <div className="eo-tpl-hint">
                    <i className="ti ti-info-circle" />
                    Edit this template in <strong>Settings → Templates</strong>. Groq AI will personalize <code>{"{{}}"}</code> variables per lead.
                  </div>
                </div>
              ) : (
                /* ── Custom template editor ── */
                <div className="eo-tpl-editor">
                  <div className="form-group">
                    <label>Subject line</label>
                    <input
                      placeholder="e.g. Quick question about {{business}}"
                      value={form.subject}
                      onChange={e => setForm(f => ({ ...f, subject: e.target.value }))}
                    />
                  </div>
                  <div className="form-group">
                    <label>Email body</label>
                    <VarChips onInsert={insertBodyVar} />
                    <textarea
                      className="eo-textarea"
                      rows={7}
                      placeholder="Hi {{name}}, I came across {{business}}..."
                      value={form.body}
                      onChange={e => setForm(f => ({ ...f, body: e.target.value }))}
                    />
                  </div>
                  <div className="form-row">
                    <div className="form-group">
                      <label>Send delay (seconds)</label>
                      <input
                        type="number" min="1" max="60"
                        value={form.email_delay}
                        onChange={e => setForm(f => ({ ...f, email_delay: parseInt(e.target.value) || 1 }))}
                      />
                    </div>
                    <div className="form-group">
                      <label>Max emails per day</label>
                      <input
                        type="number" min="1" max="500"
                        value={form.max_per_day}
                        onChange={e => setForm(f => ({ ...f, max_per_day: parseInt(e.target.value) || 1 }))}
                      />
                    </div>
                  </div>
                  <div className="eo-tpl-hint">
                    <i className="ti ti-info-circle" />
                    This custom template is one-off only. To save permanently, go to <strong>Settings → Templates</strong>.
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
                      checked={form.dry_run}
                      onChange={e => setForm(f => ({ ...f, dry_run: e.target.checked }))}
                    />
                    <span className="toggle-track"><span className="toggle-thumb" /></span>
                    <span className="toggle-text">
                      {form.dry_run ? "Test only — no emails sent" : "Live — will send real emails"}
                    </span>
                  </label>
                </div>
              </div>

              <div className="modal-note">
                <i className="ti ti-info-circle" />
                <span>
                  Only leads with verified emails and score ≥ {form.min_score} will be targeted.
                  DNC list is checked automatically.
                </span>
              </div>

            </div>{/* /modal-body */}

            <div className="modal-footer">
              <button className="btn-ghost" onClick={() => setComposeOpen(false)}>Cancel</button>
              <button
                className={`btn-primary ${form.dry_run ? "btn-dry" : ""}`}
                onClick={handleLaunch}
                disabled={launching}
              >
                {launching
                  ? <><i className="ti ti-loader-2 spin" /> Launching...</>
                  : form.dry_run
                  ? <><i className="ti ti-flask" /> Run dry test</>
                  : <><i className="ti ti-send" /> Launch campaign</>
                }
              </button>
            </div>

          </div>
        </div>
      )}

      <LeadDetailModal leadId={viewLeadId} onClose={() => setViewLeadId(null)} />
    </div>
  );
}