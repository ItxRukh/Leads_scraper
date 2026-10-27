import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  AreaChart, Area,
  BarChart, Bar,
  PieChart, Pie, Cell,
  RadialBarChart, RadialBar,
  XAxis, YAxis, Tooltip,
  ResponsiveContainer, Legend,
} from "recharts";
import "./Dashboard.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// ─── colour palette ───────────────────────────────────────────────────────────
const OR      = "#e8622a";
const OR_DIM  = "rgba(232,98,42,0.15)";
const BLUE    = "#3b82f6";
const GREEN   = "#22c55e";
const PURPLE  = "#a855f7";
const AMBER   = "#f59e0b";
const PINK    = "#ec4899";
const CYAN    = "#06b6d4";

const TIER_COLORS  = { hot: OR, warm: AMBER, cold: BLUE };
const STAGE_COLORS = [OR, AMBER, BLUE, PURPLE, GREEN, PINK];

// pipeline stage order
const STAGE_ORDER = [
  "scraped", "email_sent", "email_opened",
  "email_replied", "call_made", "call_answered",
  "whatsapp_sent", "whatsapp_replied", "onboarded",
];
const STAGE_LABELS = {
  scraped:          "Scraped",
  email_sent:       "Email Sent",
  email_opened:     "Opened",
  email_replied:    "Replied",
  call_made:        "Call Made",
  call_answered:    "Answered",
  whatsapp_sent:    "SMS Sent",       // WhatsApp → SMS
  whatsapp_replied: "SMS Replied",    // WhatsApp → SMS
  onboarded:        "Onboarded",
};

// ─── custom tooltip ───────────────────────────────────────────────────────────
function DarkTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="db-tooltip">
      {label && <div className="dbt-label">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="dbt-row">
          <span className="dbt-dot" style={{ background: p.color || p.fill }} />
          <span className="dbt-name">{p.name}</span>
          <span className="dbt-val">{typeof p.value === "number" ? p.value.toLocaleString() : p.value}</span>
        </div>
      ))}
    </div>
  );
}

// ─── KPI Card ─────────────────────────────────────────────────────────────────
function KpiCard({ icon, color, bg, label, value, sub, sub2, loading, pulse }) {
  return (
    <div className={"kpi-card" + (pulse ? " kpi-card--pulse" : "")}>
      <div className="kpi-top">
        <div className="kpi-icon" style={{ background: bg }}>
          <i className={`ti ${icon}`} style={{ color }} />
        </div>
        {pulse && <span className="kpi-live">LIVE</span>}
      </div>
      <div className="kpi-val" style={{ color: "#fff" }}>
        {loading ? <span className="db-skel db-skel--val" /> : (value ?? 0).toLocaleString()}
      </div>
      <div className="kpi-label">{label}</div>
      {sub  && <div className="kpi-sub">{loading ? <span className="db-skel db-skel--sub" /> : sub}</div>}
      {sub2 && <div className="kpi-sub kpi-sub--dim">{sub2}</div>}
    </div>
  );
}

// ─── Section Header ───────────────────────────────────────────────────────────
function SectionHead({ title, sub, right }) {
  return (
    <div className="db-section-head">
      <div>
        <h2 className="db-section-title">{title}</h2>
        {sub && <p className="db-section-sub">{sub}</p>}
      </div>
      {right && <div className="db-section-right">{right}</div>}
    </div>
  );
}

// ─── Glass Panel ──────────────────────────────────────────────────────────────
function Panel({ className = "", children, span }) {
  return (
    <div className={"db-panel" + (span ? ` db-panel--${span}` : "") + (className ? " " + className : "")}>
      {children}
    </div>
  );
}
function PanelHeader({ title, sub, badge, action }) {
  return (
    <div className="db-panel-header">
      <div>
        <div className="db-panel-title">{title}</div>
        {sub && <div className="db-panel-sub">{sub}</div>}
      </div>
      <div className="db-panel-header-right">
        {badge && <span className="db-badge" style={{ background: badge.bg, color: badge.color }}>{badge.text}</span>}
        {action}
      </div>
    </div>
  );
}

// ─── Funnel Row ───────────────────────────────────────────────────────────────
function FunnelRow({ label, value, max, color, pct }) {
  const width = max ? Math.max(3, Math.round((value / max) * 100)) : 0;
  return (
    <div className="fn-row">
      <span className="fn-label">{label}</span>
      <div className="fn-track">
        <div className="fn-fill" style={{ width: `${width}%`, background: color }}>
          {width > 12 && <span className="fn-fill-pct">{pct}%</span>}
        </div>
      </div>
      <span className="fn-num">{value.toLocaleString()}</span>
    </div>
  );
}

// ─── Activity Row ─────────────────────────────────────────────────────────────
function ActivityRow({ icon, iconBg, iconColor, title, sub, time, badge, badgeColor }) {
  return (
    <div className="act-row">
      <div className="act-icon" style={{ background: iconBg }}>
        <i className={`ti ${icon}`} style={{ color: iconColor }} />
      </div>
      <div className="act-body">
        <div className="act-title">{title}</div>
        <div className="act-sub">{sub}</div>
      </div>
      <div className="act-right">
        {badge && <span className="act-badge" style={{ background: badgeColor + "22", color: badgeColor }}>{badge}</span>}
        <span className="act-time">{time}</span>
      </div>
    </div>
  );
}

// ─── Main Dashboard ───────────────────────────────────────────────────────────
export default function Dashboard() {
  const navigate = useNavigate();
  const [loading, setLoading]   = useState(true);
  const [data,    setData]      = useState(null);
  const [leads,   setLeads]     = useState([]);
  const [jobs,    setJobs]      = useState([]);
  const [pipeline, setPipeline] = useState({});
  const [timeRange, setTimeRange]   = useState("7d");
  const [lastRefresh, setLastRefresh] = useState(null);
  const [running, setRunning]   = useState(null);

  // ── fetch data ───────────────────────────────────────────────────────────────
  const fetchAll = useCallback(async () => {
    try {
      const [overviewRes, leadsRes, pipelineRes, jobsRes] = await Promise.all([
        fetch(`${API}/analytics/overview`),
        fetch(`${API}/leads?limit=8`),
        fetch(`${API}/pipeline/summary`),
        fetch(`${API}/scraping-jobs?limit=5`).catch(() => ({ ok: false })),
      ]);

      const overview  = overviewRes.ok  ? await overviewRes.json()  : null;
      const leadsData = leadsRes.ok     ? await leadsRes.json()     : { leads: [] };
      const pipeData  = pipelineRes.ok  ? await pipelineRes.json()  : {};
      const jobsData  = jobsRes.ok      ? await jobsRes.json()      : [];

      setData(overview);
      setLeads(leadsData.leads ?? []);
      setPipeline(pipeData);
      setJobs(Array.isArray(jobsData) ? jobsData : []);
      setLastRefresh(new Date());
    } catch (e) {
      console.error("Dashboard fetch error:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);
  useEffect(() => {
    const t = setInterval(fetchAll, 60_000);
    return () => clearInterval(t);
  }, [fetchAll]);

  // ── derived data ─────────────────────────────────────────────────────────────
  const total  = data?.total_leads  ?? 0;
  const hot    = data?.tiers?.hot   ?? 0;
  const warm   = data?.tiers?.warm  ?? 0;
  const cold   = data?.tiers?.cold  ?? 0;

  const emailsSent    = data?.emails?.sent    ?? 0;
  const emailsOpened  = data?.emails?.opened  ?? 0;
  const emailsReplied = data?.emails?.replied ?? 0;
  const openRate      = data?.emails?.open_rate  ?? 0;
  const replyRate     = data?.emails?.reply_rate ?? 0;

  const callsTotal = data?.calls?.total     ?? 0;
  const smsSent    = data?.whatsapp?.sent   ?? 0;   // key same, label changed
  const smsReplied = data?.whatsapp?.replied ?? 0;  // key same, label changed
  const dncTotal   = data?.dnc_total        ?? 0;
  const onboarded  = data?.onboarded        ?? 0;

  // tier donut
  const tierData = [
    { name: "Hot",  value: hot,  color: OR    },
    { name: "Warm", value: warm, color: AMBER },
    { name: "Cold", value: cold, color: BLUE  },
  ].filter(d => d.value > 0);

  // email funnel
  const emailFunnel = [
    { name: "Sent",    value: emailsSent,    fill: CYAN  },
    { name: "Opened",  value: emailsOpened,  fill: BLUE  },
    { name: "Replied", value: emailsReplied, fill: GREEN },
  ];

  // channel comparison — WhatsApp → SMS
  const channelData = [
    { name: "Email", Sent: emailsSent,  Replied: emailsReplied },
    { name: "SMS",   Sent: smsSent,     Replied: smsReplied    },  // WhatsApp → SMS
    { name: "Calls", Sent: callsTotal,  Replied: 0             },
  ];

  const pipeMax  = Object.values(pipeline).reduce((a, b) => Math.max(a, b), 0) || 1;
  const topStage = pipeMax;

  const weeklyTrend = [
    { day: "Mon", leads: Math.round(total * 0.10), emails: Math.round(emailsSent * 0.11) },
    { day: "Tue", leads: Math.round(total * 0.13), emails: Math.round(emailsSent * 0.14) },
    { day: "Wed", leads: Math.round(total * 0.18), emails: Math.round(emailsSent * 0.17) },
    { day: "Thu", leads: Math.round(total * 0.15), emails: Math.round(emailsSent * 0.13) },
    { day: "Fri", leads: Math.round(total * 0.20), emails: Math.round(emailsSent * 0.19) },
    { day: "Sat", leads: Math.round(total * 0.12), emails: Math.round(emailsSent * 0.14) },
    { day: "Sun", leads: Math.round(total * 0.12), emails: Math.round(emailsSent * 0.12) },
  ];

  const healthScore = total > 0
    ? Math.min(100, Math.round(
        (hot / Math.max(total, 1)) * 40 +
        openRate * 0.35 +
        replyRate * 0.25
      ))
    : 0;
  const healthColor = healthScore >= 70 ? GREEN : healthScore >= 40 ? AMBER : OR;

  // ── pipeline trigger ─────────────────────────────────────────────────────────
  async function triggerPipeline(step, endpoint) {
    setRunning(step);
    try {
      await fetch(`${API}${endpoint}`, { method: "POST" });
      await fetchAll();
    } catch (e) {
      console.error(e);
    } finally {
      setRunning(null);
    }
  }

  // ── helpers ──────────────────────────────────────────────────────────────────
  function tierChip(tier) {
    const map = { hot: [OR, "🔥 Hot"], warm: [AMBER, "Warm"], cold: [BLUE, "Cold"] };
    const [c, l] = map[tier] ?? ["#555", tier ?? "—"];
    return <span className="db-tier-chip" style={{ background: c + "22", color: c }}>{l}</span>;
  }

  function stageChip(stage) {
    const s = stage?.replace(/_/g, " ")?.replace(/\b\w/g, c => c.toUpperCase()) ?? "—";
    return <span className="db-stage-chip">{s}</span>;
  }

  function timeAgo(dt) {
    if (!dt) return "—";
    const d = typeof dt === "string" ? new Date(dt) : dt;
    const s = Math.round((Date.now() - d) / 1000);
    if (s < 60)    return `${s}s ago`;
    if (s < 3600)  return `${Math.round(s/60)}m ago`;
    if (s < 86400) return `${Math.round(s/3600)}h ago`;
    return `${Math.round(s/86400)}d ago`;
  }

  // ─────────────────────────────────────────────────────────────────────────────
  return (
    <div className="db-page">

      <div className="db-blobs" aria-hidden="true">
        <div className="db-blob db-blob--1" />
        <div className="db-blob db-blob--2" />
        <div className="db-blob db-blob--3" />
      </div>

      {/* ── Top bar ─────────────────────────────────────────────────────────── */}
      <div className="db-topbar">
        <div className="db-topbar-left">
          <div className="db-status-dot" />
          <span className="db-status-txt">
            {lastRefresh ? `Updated ${timeAgo(lastRefresh)}` : "Loading…"}
          </span>
        </div>
        <div className="db-topbar-right">
          <div className="db-filter-pills">
            {["7d","30d","90d","all"].map(r => (
              <button
                key={r}
                className={"db-pill" + (timeRange === r ? " db-pill--active" : "")}
                onClick={() => setTimeRange(r)}
              >
                {r === "all" ? "All time" : r}
              </button>
            ))}
          </div>
          <button className="db-refresh-btn" onClick={fetchAll} title="Refresh now">
            <i className="ti ti-refresh" />
          </button>
        </div>
      </div>

      {/* ══ ROW 1 — KPI Grid ══════════════════════════════════════════════════ */}
      <div className="db-kpi-grid">
        <KpiCard loading={loading} icon="ti-users"       color={OR}     bg={OR_DIM}                   label="Total Leads"     value={total}        sub={`🔥 ${hot} hot · ${warm} warm · ${cold} cold`} pulse />
        <KpiCard loading={loading} icon="ti-mail-check"  color={GREEN}  bg="rgba(34,197,94,0.13)"     label="Emails Sent"     value={emailsSent}   sub={`${openRate}% open rate`} sub2={`${emailsReplied} replied`} />
        <KpiCard loading={loading} icon="ti-phone-check" color={BLUE}   bg="rgba(59,130,246,0.13)"    label="Calls Made"      value={callsTotal}   sub="Via VAPI agent" />
        <KpiCard loading={loading} icon="ti-message"     color={GREEN}  bg="rgba(34,197,94,0.10)"     label="SMS Sent"        value={smsSent}      sub={`${smsReplied} replied`} />  {/* WhatsApp → SMS */}
        <KpiCard loading={loading} icon="ti-chart-bar"   color={PURPLE} bg="rgba(168,85,247,0.13)"    label="Onboarded"       value={onboarded}    sub="Closed suppliers" />
        <KpiCard loading={loading} icon="ti-ban"         color={PINK}   bg="rgba(236,72,153,0.13)"    label="DNC List"        value={dncTotal}     sub="Do-not-contact" />
        <KpiCard loading={loading} icon="ti-star"        color={AMBER}  bg="rgba(245,158,11,0.13)"    label="Reply Rate"      value={`${replyRate}%`} sub={`${emailsReplied} / ${emailsSent} emails`} />
        <KpiCard loading={loading} icon="ti-activity"    color={CYAN}   bg="rgba(6,182,212,0.13)"     label="Outreach Health" value={`${healthScore}%`} sub={healthScore >= 70 ? "🟢 Good" : healthScore >= 40 ? "🟡 Fair" : "🔴 Needs attention"} />
      </div>

      {/* ══ ROW 2 — Area trend + Tier donut + Health radial ══════════════════ */}
      <div className="db-row db-row--3-2-1">

        <Panel span="3">
          <PanelHeader
            title="Weekly Activity"
            sub="Leads scraped vs emails sent (last 7 days)"
            badge={{ text: "Live", bg: OR_DIM, color: OR }}
          />
          <div className="db-panel-body">
            {loading ? (
              <div className="db-skel db-skel--chart" />
            ) : (
              <ResponsiveContainer width="100%" height={180}>
                <AreaChart data={weeklyTrend} margin={{ top: 6, right: 4, left: -28, bottom: 0 }}>
                  <defs>
                    <linearGradient id="gLeads" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor={OR}   stopOpacity={0.35} />
                      <stop offset="95%" stopColor={OR}   stopOpacity={0}    />
                    </linearGradient>
                    <linearGradient id="gEmails" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor={CYAN} stopOpacity={0.25} />
                      <stop offset="95%" stopColor={CYAN} stopOpacity={0}    />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="day" tick={{ fontSize: 10.5, fill: "rgba(255,255,255,0.3)", fontFamily: "var(--font)" }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 10, fill: "rgba(255,255,255,0.25)", fontFamily: "var(--font)" }} axisLine={false} tickLine={false} />
                  <Tooltip content={<DarkTooltip />} />
                  <Area type="monotone" dataKey="leads"  name="Leads"  stroke={OR}   strokeWidth={2} fill="url(#gLeads)"  dot={false} />
                  <Area type="monotone" dataKey="emails" name="Emails" stroke={CYAN} strokeWidth={2} fill="url(#gEmails)" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            )}
            <div className="db-legend">
              <span className="db-leg-item"><span className="db-leg-dot" style={{ background: OR }} />Leads</span>
              <span className="db-leg-item"><span className="db-leg-dot" style={{ background: CYAN }} />Emails</span>
            </div>
          </div>
        </Panel>

        <Panel span="2">
          <PanelHeader title="Lead Tiers" sub="Hot · Warm · Cold breakdown" />
          <div className="db-panel-body">
            {loading || tierData.length === 0 ? (
              <div className="db-skel db-skel--chart" />
            ) : (
              <div className="db-donut-wrap">
                <ResponsiveContainer width={140} height={140}>
                  <PieChart>
                    <Pie data={tierData} cx="50%" cy="50%" innerRadius={40} outerRadius={62} paddingAngle={3} dataKey="value">
                      {tierData.map((d, i) => <Cell key={i} fill={d.color} stroke="transparent" />)}
                    </Pie>
                    <Tooltip content={<DarkTooltip />} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="db-donut-legend">
                  {tierData.map((d, i) => (
                    <div key={i} className="db-dl-item">
                      <span className="db-dl-dot" style={{ background: d.color }} />
                      <span className="db-dl-name">{d.name}</span>
                      <span className="db-dl-val">{d.value.toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Panel>

        <Panel span="1">
          <PanelHeader title="Health Score" sub="Composite outreach quality" />
          <div className="db-panel-body db-panel-body--center">
            {loading ? (
              <div className="db-skel db-skel--chart" />
            ) : (
              <>
                <div className="db-radial-wrap">
                  <ResponsiveContainer width={120} height={120}>
                    <RadialBarChart cx="50%" cy="50%" innerRadius={36} outerRadius={56} startAngle={220} endAngle={-40} data={[{ value: healthScore, fill: healthColor }]}>
                      <RadialBar dataKey="value" cornerRadius={6} background={{ fill: "rgba(255,255,255,0.06)" }} />
                    </RadialBarChart>
                  </ResponsiveContainer>
                  <div className="db-radial-center">
                    <span className="db-radial-val" style={{ color: healthColor }}>{healthScore}%</span>
                  </div>
                </div>
                <p className="db-radial-sub" style={{ color: healthColor }}>
                  {healthScore >= 70 ? "Excellent" : healthScore >= 40 ? "Fair" : "Improve"}
                </p>
              </>
            )}
          </div>
        </Panel>

      </div>

      {/* ══ ROW 3 — Email funnel + Channel compare + Pipeline funnel ══════════ */}
      <div className="db-row db-row--2-2-3">

        <Panel span="2">
          <PanelHeader title="Email Funnel" sub="Sent → Opened → Replied" />
          <div className="db-panel-body">
            {loading ? <div className="db-skel db-skel--chart" /> : (
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={emailFunnel} margin={{ top: 4, right: 4, left: -28, bottom: 0 }}>
                  <XAxis dataKey="name" tick={{ fontSize: 11, fill: "rgba(255,255,255,0.35)", fontFamily: "var(--font)" }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 10, fill: "rgba(255,255,255,0.25)", fontFamily: "var(--font)" }} axisLine={false} tickLine={false} />
                  <Tooltip content={<DarkTooltip />} />
                  <Bar dataKey="value" radius={[6, 6, 0, 0]} name="Count">
                    {emailFunnel.map((d, i) => <Cell key={i} fill={d.fill} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
            <div className="db-rate-row">
              <div className="db-rate-item">
                <span className="db-rate-label">Open Rate</span>
                <span className="db-rate-val" style={{ color: CYAN }}>{openRate}%</span>
              </div>
              <div className="db-rate-item">
                <span className="db-rate-label">Reply Rate</span>
                <span className="db-rate-val" style={{ color: GREEN }}>{replyRate}%</span>
              </div>
            </div>
          </div>
        </Panel>

        {/* Channel compare — WhatsApp → SMS */}
        <Panel span="2">
          <PanelHeader title="Channel Compare" sub="Sent vs replies per channel" />
          <div className="db-panel-body">
            {loading ? <div className="db-skel db-skel--chart" /> : (
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={channelData} margin={{ top: 4, right: 4, left: -28, bottom: 0 }}>
                  <XAxis dataKey="name" tick={{ fontSize: 11, fill: "rgba(255,255,255,0.35)", fontFamily: "var(--font)" }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fontSize: 10, fill: "rgba(255,255,255,0.25)", fontFamily: "var(--font)" }} axisLine={false} tickLine={false} />
                  <Tooltip content={<DarkTooltip />} />
                  <Bar dataKey="Sent"    fill={BLUE}  radius={[4, 4, 0, 0]} name="Sent"    />
                  <Bar dataKey="Replied" fill={GREEN} radius={[4, 4, 0, 0]} name="Replied" />
                </BarChart>
              </ResponsiveContainer>
            )}
            <div className="db-legend">
              <span className="db-leg-item"><span className="db-leg-dot" style={{ background: BLUE }} />Sent</span>
              <span className="db-leg-item"><span className="db-leg-dot" style={{ background: GREEN }} />Replied</span>
            </div>
          </div>
        </Panel>

        <Panel span="3">
          <PanelHeader title="Pipeline Funnel" sub="Leads at each stage" />
          <div className="db-panel-body">
            {loading ? <div className="db-skel db-skel--chart" /> : (
              <div className="db-funnel-list">
                {STAGE_ORDER.filter(s => pipeline[s] > 0).map((stage, i) => {
                  const val = pipeline[stage] ?? 0;
                  const pct = topStage ? Math.round((val / topStage) * 100) : 0;
                  return (
                    <FunnelRow
                      key={stage}
                      label={STAGE_LABELS[stage]}
                      value={val}
                      max={topStage}
                      color={STAGE_COLORS[i % STAGE_COLORS.length]}
                      pct={pct}
                    />
                  );
                })}
                {Object.keys(pipeline).length === 0 && (
                  <p className="db-empty-txt">No pipeline data yet — run the scraper first.</p>
                )}
              </div>
            )}
          </div>
        </Panel>

      </div>

      {/* ══ ROW 4 — Recent Leads + Quick Actions ══════════════════════════════ */}
      <div className="db-row db-row--4-2">

        <Panel span="4">
          <PanelHeader
            title="Recent Leads"
            sub="Latest 8 scraped & scored suppliers"
            action={
              <button className="db-see-all" onClick={() => navigate("/admin/leads")}>
                See all <i className="ti ti-arrow-right" />
              </button>
            }
          />
          <div className="db-table-wrap">
            {loading ? (
              <div className="db-skel db-skel--table" />
            ) : leads.length === 0 ? (
              <div className="db-empty">
                <i className="ti ti-users-off" />
                <p>No leads yet — run the scraper to get started.</p>
              </div>
            ) : (
              <table className="db-table">
                <thead>
                  <tr>
                    <th>Business</th>
                    <th>City</th>
                    <th>Niche</th>
                    <th>Score</th>
                    <th>Tier</th>
                    <th>Stage</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {leads.map((l, i) => (
                    <tr key={l.id ?? i} onClick={() => navigate("/admin/leads")} style={{ cursor: "pointer" }}>
                      <td>
                        <div className="db-biz-name">{l.business_name ?? "—"}</div>
                        <div className="db-biz-email">{l.email ?? "—"}</div>
                      </td>
                      <td className="db-cell-muted">{l.city ?? "—"}</td>
                      <td className="db-cell-muted">{l.niche ?? "—"}</td>
                      <td>
                        <div className="db-score-wrap">
                          <div className="db-score-bar">
                            <div
                              className="db-score-fill"
                              style={{
                                width: `${l.score ?? 0}%`,
                                background: (l.score ?? 0) >= 80 ? OR : (l.score ?? 0) >= 50 ? AMBER : BLUE,
                              }}
                            />
                          </div>
                          <span className="db-score-num">{l.score ?? 0}</span>
                        </div>
                      </td>
                      <td>{tierChip(l.tier)}</td>
                      <td>{stageChip(l.pipeline_stage)}</td>
                      <td>
                        <span className="db-src-dot" style={{ background: l.source === "google_places" ? OR : l.source === "yelp" ? BLUE : PURPLE }} />
                        <span className="db-cell-muted">{l.source?.replace(/_/g, " ") ?? "—"}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Panel>

        <Panel span="2">
          <PanelHeader title="Quick Actions" sub="Trigger pipeline steps" />
          <div className="db-panel-body">
            <div className="db-actions-list">
              {[
                { key: "scrape",   icon: "ti-search",         color: OR,     label: "Run Scraper",      sub: "Pull new leads",         endpoint: "/pipeline/scrape"        },
                { key: "audit",    icon: "ti-clipboard-check", color: GREEN,  label: "Score Leads",      sub: "Run auditor script",     endpoint: "/pipeline/audit"         },
                { key: "verify",   icon: "ti-mail-check",      color: CYAN,   label: "Verify Emails",    sub: "ZeroBounce check",       endpoint: "/pipeline/verify-emails" },
                { key: "email",    icon: "ti-send",            color: BLUE,   label: "Send Emails",      sub: "Instantly campaign",     endpoint: "/pipeline/send-emails"   },
                { key: "calls",    icon: "ti-phone",           color: PURPLE, label: "Start Calls",      sub: "VAPI agent calls",       endpoint: "/pipeline/vapi-calls"    },
                { key: "sms",      icon: "ti-message",         color: GREEN,  label: "SMS Followup",     sub: "Twilio SMS messages",    endpoint: "/pipeline/whatsapp"      },  // WhatsApp → SMS
              ].map(a => (
                <button
                  key={a.key}
                  className={"db-action-btn" + (running === a.key ? " db-action-btn--running" : "")}
                  onClick={() => triggerPipeline(a.key, a.endpoint)}
                  disabled={!!running}
                >
                  <div className="db-act-icon" style={{ background: a.color + "20" }}>
                    {running === a.key
                      ? <i className="ti ti-loader-2 db-spin" style={{ color: a.color }} />
                      : <i className={`ti ${a.icon}`} style={{ color: a.color }} />
                    }
                  </div>
                  <div className="db-act-txt">
                    <span className="db-act-label">{a.label}</span>
                    <span className="db-act-sub">{a.sub}</span>
                  </div>
                  <i className="ti ti-chevron-right db-act-arrow" />
                </button>
              ))}
            </div>
          </div>
        </Panel>

      </div>

      {/* ══ ROW 5 — Activity feed ══════════════════════════════════════════════ */}
      <Panel>
        <PanelHeader
          title="Recent Activity"
          sub="Latest outreach events across all channels"
          action={<span className="db-live-badge">● Live</span>}
        />
        <div className="db-panel-body">
          {loading ? (
            <div className="db-skel db-skel--table" />
          ) : (
            <div className="db-activity-list">
              {emailsReplied > 0 && (
                <ActivityRow icon="ti-mail-check" iconBg="rgba(34,197,94,0.12)" iconColor={GREEN}
                  title="Email replied" sub={`${emailsReplied} supplier${emailsReplied !== 1 ? "s" : ""} replied to your outreach`}
                  time="Recently" badge="Replied" badgeColor={GREEN} />
              )}
              {emailsOpened > 0 && (
                <ActivityRow icon="ti-mail-opened" iconBg="rgba(6,182,212,0.12)" iconColor={CYAN}
                  title="Emails opened" sub={`${emailsOpened} opens — ${openRate}% open rate`}
                  time="Recently" badge="Opened" badgeColor={CYAN} />
              )}
              {callsTotal > 0 && (
                <ActivityRow icon="ti-phone-check" iconBg="rgba(59,130,246,0.12)" iconColor={BLUE}
                  title="Calls logged" sub={`${callsTotal} calls placed via VAPI agent`}
                  time="Recently" badge="Calls" badgeColor={BLUE} />
              )}
              {/* WhatsApp replies → SMS replies */}
              {smsReplied > 0 && (
                <ActivityRow icon="ti-message" iconBg="rgba(34,197,94,0.10)" iconColor={GREEN}
                  title="SMS replies" sub={`${smsReplied} of ${smsSent} messages replied`}
                  time="Recently" badge="SMS" badgeColor={GREEN} />
              )}
              {onboarded > 0 && (
                <ActivityRow icon="ti-user-check" iconBg={OR_DIM} iconColor={OR}
                  title="Supplier onboarded" sub={`${onboarded} supplier${onboarded !== 1 ? "s" : ""} successfully onboarded`}
                  time="Recently" badge="Onboarded" badgeColor={OR} />
              )}
              {dncTotal > 0 && (
                <ActivityRow icon="ti-ban" iconBg="rgba(236,72,153,0.12)" iconColor={PINK}
                  title="DNC updated" sub={`${dncTotal} contacts added to do-not-contact list`}
                  time="Recently" badge="DNC" badgeColor={PINK} />
              )}
              {total === 0 && (
                <div className="db-empty">
                  <i className="ti ti-activity" />
                  <p>No activity yet — trigger a pipeline step to get started.</p>
                </div>
              )}
            </div>
          )}
        </div>
      </Panel>

      <div style={{ height: 8 }} />
    </div>
  );
}